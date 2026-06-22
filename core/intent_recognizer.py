"""
亮点：端到端二维意图识别（Intent + Sentiment）

三路融合策略：
  1. LLM 语义理解（权重 70%）—— 主力，一次输出 intent + sentiment
  2. Embedding 向量相似度（权重 20%）—— 快速匹配常见表达
  3. 关键词模式匹配（权重 10%）—— 零延迟兜底，手机号正则等

三路结果通过加权投票合并 intent，sentiment 以 LLM 为准、Pattern 辅助。
LLM 和 Embedding 并行调用，不串行等待。
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)


class IntentCategory(Enum):
    """留资型客服意图"""
    GREETING      = "greeting"       # 打招呼/开场
    PRODUCT_INQ   = "product_inq"    # 咨询产品/服务
    PRICE_INQ     = "price_inq"      # 询问价格/预算
    PURCHASE      = "purchase"       # 购买意向明确
    COMPLAINT     = "complaint"      # 投诉/不满
    CONTACT_GIVE  = "contact_give"   # 给出联系方式
    CONTACT_NO    = "contact_no"     # 拒绝留联系方式
    CONTACT_FIX   = "contact_fix"    # 更正联系方式
    CHITCHAT      = "chitchat"       # 闲聊/无关


class Sentiment(Enum):
    """用户情绪/态度"""
    POSITIVE  = "positive"   # 积极/信任
    NEUTRAL   = "neutral"    # 中性
    SKEPTICAL = "skeptical"  # 质疑/犹豫
    ANXIOUS   = "anxious"    # 焦虑/顾虑
    NEGATIVE  = "negative"   # 愤怒/驱离


class UrgencyLevel(Enum):
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4


@dataclass
class IntentResult:
    intent:      IntentCategory
    sentiment:   Sentiment
    confidence:  float
    urgency:     UrgencyLevel
    entities:    Dict[str, List[str]]
    reasoning:   str
    latency_ms:  float


# ═══════════════════════════════════════════════════════════════════════════════
# Few-shot 模板（同时用于 LLM 示例和 Embedding 匹配）
# 每条模板标注 intent + sentiment 双标签，LLM 靠这些示例理解边界
# ═══════════════════════════════════════════════════════════════════════════════

# ── (intent, sentiment, 示例消息) ──────────────────────────────────────────────
_FEWSHOT = [
    # GREETING
    (IntentCategory.GREETING,     Sentiment.POSITIVE,  "你好"),
    (IntentCategory.GREETING,     Sentiment.NEUTRAL,   "在吗？"),
    (IntentCategory.GREETING,     Sentiment.POSITIVE,  "早上好"),
    # PRODUCT_INQ
    (IntentCategory.PRODUCT_INQ,  Sentiment.NEUTRAL,   "你们产品有什么功能？"),
    (IntentCategory.PRODUCT_INQ,  Sentiment.NEUTRAL,   "这个和XX比怎么样？"),
    (IntentCategory.PRODUCT_INQ,  Sentiment.SKEPTICAL, "你确定这个有用吗？"),
    (IntentCategory.PRODUCT_INQ,  Sentiment.ANXIOUS,   "会不会用几天就坏了？"),
    # PRICE_INQ
    (IntentCategory.PRICE_INQ,    Sentiment.NEUTRAL,   "多少钱一个月？"),
    (IntentCategory.PRICE_INQ,    Sentiment.NEUTRAL,   "我目前手上的预算是5000"),
    (IntentCategory.PRICE_INQ,    Sentiment.SKEPTICAL, "太贵了吧，能不能便宜点？"),
    (IntentCategory.PRICE_INQ,    Sentiment.ANXIOUS,   "带不了款，还有别的办法吗？"),
    # PURCHASE
    (IntentCategory.PURCHASE,     Sentiment.POSITIVE,  "我要买，怎么下单？"),
    (IntentCategory.PURCHASE,     Sentiment.POSITIVE,  "给我来一个试试"),
    (IntentCategory.PURCHASE,     Sentiment.NEUTRAL,   "怎么付款？"),
    (IntentCategory.PURCHASE,     Sentiment.SKEPTICAL, "算了算了，不买了"),
    # COMPLAINT
    (IntentCategory.COMPLAINT,    Sentiment.NEGATIVE,  "滚滚滚，别烦我"),
    (IntentCategory.COMPLAINT,    Sentiment.SKEPTICAL, "说的好听，骗人的吧"),
    (IntentCategory.COMPLAINT,    Sentiment.NEGATIVE,  "等了这么久没人理我"),
    # CONTACT_GIVE
    (IntentCategory.CONTACT_GIVE, Sentiment.NEUTRAL,   "13712345678"),
    (IntentCategory.CONTACT_GIVE, Sentiment.POSITIVE,  "我的微信号是abc123"),
    (IntentCategory.CONTACT_GIVE, Sentiment.POSITIVE,  "手机号138xxxx，微信同号"),
    (IntentCategory.CONTACT_GIVE, Sentiment.NEUTRAL,   "你记一下，13900001111"),
    # CONTACT_NO
    (IntentCategory.CONTACT_NO,   Sentiment.NEUTRAL,   "不方便留电话"),
    (IntentCategory.CONTACT_NO,   Sentiment.ANXIOUS,   "还是算了吧，不放心"),
    (IntentCategory.CONTACT_NO,   Sentiment.NEGATIVE,  "说了不留就是不留"),
    # CONTACT_FIX
    (IntentCategory.CONTACT_FIX,  Sentiment.NEUTRAL,   "不好意思，号码发错了，是139"),
    (IntentCategory.CONTACT_FIX,  Sentiment.NEUTRAL,   "微信不是这个，换一个"),
    # CHITCHAT
    (IntentCategory.CHITCHAT,     Sentiment.POSITIVE,  "今天天气不错"),
    (IntentCategory.CHITCHAT,     Sentiment.NEUTRAL,   "你是机器人吗？"),
]

# 按 IntentCategory 分组（供 Embedding 匹配使用）
_TEMPLATES: Dict[IntentCategory, List[str]] = {}
for _cat, _sent, _msg in _FEWSHOT:
    _TEMPLATES.setdefault(_cat, []).append(_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 关键词/正则匹配（零延迟兜底）
# ═══════════════════════════════════════════════════════════════════════════════

_PATTERNS: Dict[IntentCategory, List[str]] = {
    IntentCategory.CONTACT_GIVE: [
        r"1[3-9]\d{9}",             # 手机号
        r"微信[号:\s]*[a-zA-Z]\w+",
        r"手机[号:\s]*1\d+",
        r"你记一下",
    ],
    IntentCategory.CONTACT_NO: [
        "不方便", "不想留", "不用了", "算了吧",
        "不留", "别问了", "不给",
    ],
    IntentCategory.CONTACT_FIX: [
        "发错了", "打错了", "换一个", "不是这个",
        "更正", "纠正",
    ],
    IntentCategory.COMPLAINT: [
        "滚滚滚", "骗子", "坑人", "投诉", "举报", "垃圾",
    ],
    IntentCategory.GREETING: [
        "你好", "嗨", "hello", "hi", "在吗", "早上好", "晚上好",
    ],
    IntentCategory.PURCHASE: [
        "我要买", "下单", "购买", "怎么买", "给我来",
    ],
    IntentCategory.PRICE_INQ: [
        "多少钱", "价格", "预算", "优惠", "折扣", "分期",
    ],
    IntentCategory.PRODUCT_INQ: [
        "功能", "怎么用", "怎么样", "介绍", "说明",
    ],
}

# 情绪关键词（Pattern 辅助，LLM 为准）
_SENTIMENT_PATTERNS: Dict[Sentiment, List[str]] = {
    Sentiment.NEGATIVE:  ["滚滚滚", "骗子", "别烦", "垃圾", "傻逼", "滚", "滚蛋"],
    Sentiment.SKEPTICAL: ["真的吗", "确定吗", "靠谱吗", "骗人的吧", "假的吧", "忽悠"],
    Sentiment.ANXIOUS:   ["怕", "担心", "万一", "带不了款", "不敢", "怕被骗"],
    Sentiment.POSITIVE:  ["好的", "不错", "可以", "谢谢", "行", "OK", "ok"],
}

# 紧急关键词
# 紧急关键词（已废弃，紧急度改为从 intent+sentiment 推导）
# _URGENCY_KEYWORDS = {
#     UrgencyLevel.CRITICAL: ["紧急", "emergency", "urgent", "asap", "立刻"],
#     UrgencyLevel.HIGH:     ["今天", "马上", "尽快", "hurry", "now"],
#     UrgencyLevel.MEDIUM:   ["这周", "soon", "快点"],
# }


def _cosine(a: List[float], b: List[float]) -> float:
    """纯 Python 余弦相似度，不依赖 numpy。"""
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _match_regex(pattern: str, text: str) -> bool:
    """检查正则 pattern 是否在 text 中命中。"""
    try:
        return bool(re.search(pattern, text))
    except re.error:
        return False


class IntentRecognizer:
    """
    二维意图识别器（Intent + Sentiment）。

    初始化时不加载任何本地模型，所有 AI 能力通过 Anthropic API 调用。
    模板 Embedding 在首次请求时懒加载并缓存，后续复用。
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        confidence_threshold: float = 0.5,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client    = AsyncAnthropic(**kwargs)
        self.model     = model
        self.threshold = confidence_threshold
        self._embedding_enabled = not bool(base_url)

        self._tpl_embeddings: Dict[IntentCategory, List[List[float]]] = {}
        self._cache: Dict[str, IntentResult] = {}
        self.cache_hits   = 0
        self.cache_misses = 0

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    async def recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> IntentResult:
        """
        识别用户意图和情绪。

        history 格式：[{"role": "user"/"assistant", "content": "..."}]
        """
        key = self._cache_key(message)
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        self.cache_misses += 1

        t0 = time.monotonic()

        # 仅使用 LLM 识别（Embedding + Pattern 路径已注释，简化链路为后续本地模型替代做准备）
        llm = await self._llm_recognize(message, history)

        # # ── 三路融合（已注释）──────────────────────────────────────────
        # # LLM 和 Embedding 并行
        # llm_task = asyncio.create_task(self._llm_recognize(message, history))
        # emb_task = asyncio.create_task(self._embedding_recognize(message)) if self._embedding_enabled else None
        # pat      = self._pattern_recognize(message)
        #
        # if emb_task:
        #     llm, emb = await asyncio.gather(llm_task, emb_task)
        # else:
        #     llm = await llm_task
        #     emb = {"intent": IntentCategory.CHITCHAT, "confidence": 0.0}
        #
        # intent    = self._vote(llm, emb, pat)

        intent    = llm.get("intent", IntentCategory.CHITCHAT)
        sentiment = self._vote_sentiment(llm, message)
        entities  = await self._extract_entities(message, intent)
        urgency   = self._urgency(message, intent, sentiment)

        result = IntentResult(
            intent=intent,
            sentiment=sentiment,
            confidence=llm["confidence"],
            urgency=urgency,
            entities=entities,
            reasoning=llm.get("reasoning", ""),
            latency_ms=(time.monotonic() - t0) * 1000,
        )

        # LRU 缓存
        # if len(self._cache) >= 1000:
        #     for k in list(self._cache)[:500]:
        #         del self._cache[k]
        # self._cache[key] = result
        return result

    def learn(self, message: str, correct_intent: IntentCategory,
              correct_sentiment: Sentiment = Sentiment.NEUTRAL) -> None:
        """在线学习：将纠正样本加入模板，清除对应 Embedding 缓存。"""
        _FEWSHOT.append((correct_intent, correct_sentiment, message))
        tpls = _TEMPLATES.setdefault(correct_intent, [])
        if message not in tpls:
            tpls.append(message)
            self._tpl_embeddings.pop(correct_intent, None)
            logger.info(f"学习新样本 → {correct_intent.value}/{correct_sentiment.value}: {message[:40]}")

    # ── 策略 1：LLM 二维识别 ─────────────────────────────────────────────────

    async def _llm_recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
    ) -> Dict[str, Any]:
        """策略 1：LLM 一次输出 intent + sentiment（Few-shot + 上下文）。"""
        message = self._clean_text(message)

        # 构建 Few-shot 示例（每类挑代表性条目，控制 prompt 长度）
        seen = set()
        examples = []
        for cat, sent, msg in _FEWSHOT:
            if (cat.value, sent.value) not in seen:
                seen.add((cat.value, sent.value))
                examples.append(f'  消息: "{msg}" → 意图: {cat.value}  情绪: {sent.value}')
            # 对关键意图多给一个示例
            elif cat in (IntentCategory.CONTACT_GIVE, IntentCategory.COMPLAINT) and \
                 (cat.value, sent.value, "extra") not in seen:
                seen.add((cat.value, sent.value, "extra"))
                examples.append(f'  消息: "{msg}" → 意图: {cat.value}  情绪: {sent.value}')

        # 最近 3 轮对话上下文
        ctx = ""
        if history:
            ctx = "\n最近对话:\n" + "\n".join(
                f"  {self._clean_text(m.get('role', 'user'))}: {self._clean_text(m.get('content', ''))}"
                for m in history[-3:]
            )

        prompt = f"""你是客服意图分析专家。请同时判断用户意图和情绪，返回 JSON。

示例:
{chr(10).join(examples)}

{ctx}
用户消息: "{message}"

返回格式（仅 JSON，不要其他文字）:
{{"intent": "<意图值>", "sentiment": "<情绪值>", "confidence": <0-1>, "reasoning": "<一句话说明>"}}

可选意图: {", ".join(c.value for c in IntentCategory)}
可选情绪: {", ".join(s.value for s in Sentiment)}

注意区分以下容易混淆的情况：
- 用户报预算但没说要买 → intent=price_inq（不是 purchase）
- 用户质疑产品效果但没赶人 → sentiment=skeptical（不是 negative）
- "滚滚滚" / "骗子" 才是 negative；"你确定吗" 只是 skeptical
- 纯数字手机号 → intent=contact_give"""
        prompt = self._clean_text(prompt)

        try:
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=256,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e])
            try:
                data["intent"] = IntentCategory(data["intent"])
            except (ValueError, KeyError):
                data["intent"] = IntentCategory.CHITCHAT
            try:
                data["sentiment"] = Sentiment(data.get("sentiment", "neutral"))
            except ValueError:
                data["sentiment"] = Sentiment.NEUTRAL
            return data
        except Exception as ex:
            logger.warning(f"LLM 识别失败: {ex}")
            return {
                "intent": IntentCategory.CHITCHAT,
                "sentiment": Sentiment.NEUTRAL,
                "confidence": 0.0,
                "reasoning": "LLM 失败",
                "failed": True,
            }

    # ── 策略 2：Embedding 向量匹配 ────────────────────────────────────────────

    async def _embedding_recognize(self, message: str) -> Dict[str, Any]:
        """策略 2：Embedding 向量相似度匹配（只匹配 intent）。"""
        try:
            await self._load_template_embeddings()
            msg_vec = await self._embed_text(message)

            best_cat, best_score = IntentCategory.CHITCHAT, 0.0
            for cat, vecs in self._tpl_embeddings.items():
                score = max(_cosine(msg_vec, v) for v in vecs)
                if score > best_score:
                    best_score, best_cat = score, cat

            return {"intent": best_cat, "confidence": best_score}
        except Exception as ex:
            logger.warning(f"Embedding 识别失败: {ex}")
            return {"intent": IntentCategory.CHITCHAT, "confidence": 0.0}

    # ── 策略 3：Pattern 匹配 ─────────────────────────────────────────────────

    def _pattern_recognize(self, message: str) -> Dict[str, Any]:
        """策略 3：关键词/正则匹配（同步，零延迟兜底）。"""
        msg = message.lower()
        scores: Dict[IntentCategory, float] = {}

        for cat, kws in _PATTERNS.items():
            hits = 0
            for kw in kws:
                if _match_regex(kw, msg):
                    hits += 1
            if hits:
                scores[cat] = hits / len(kws)

        if not scores:
            return {"intent": IntentCategory.CHITCHAT, "confidence": 0.0}

        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        return {"intent": best, "confidence": scores[best]}

    # ── 意图投票（已注释 — 当前仅使用 LLM 单路，三路融合暂不使用）──────────

    # def _vote(self, llm: Dict, emb: Dict, pat: Dict) -> IntentCategory:
    #     """加权投票合成 intent。sentiment 不走投票流程。"""
    #     if llm.get("failed"):
    #         if emb.get("intent", IntentCategory.CHITCHAT) != IntentCategory.CHITCHAT and \
    #            emb.get("confidence", 0.0) > 0:
    #             return emb["intent"]
    #         if pat.get("intent", IntentCategory.CHITCHAT) != IntentCategory.CHITCHAT and \
    #            pat.get("confidence", 0.0) > 0:
    #             return pat["intent"]
    #         return IntentCategory.CHITCHAT
    #
    #     if self._embedding_enabled:
    #         weights = [(llm, 0.7), (emb, 0.2), (pat, 0.1)]
    #     else:
    #         weights = [(llm, 0.85), (pat, 0.15)]
    #     scores: Dict[IntentCategory, float] = {}
    #
    #     for result, w in weights:
    #         cat  = result.get("intent", IntentCategory.CHITCHAT)
    #         conf = result.get("confidence", 0.0)
    #         scores[cat] = scores.get(cat, 0.0) + w * conf
    #
    #     best = max(scores, key=scores.get)  # type: ignore[arg-type]
    #     return best if scores[best] >= self.threshold else IntentCategory.CHITCHAT

    def _vote_sentiment(self, llm: Dict, message: str) -> Sentiment:
        """
        Sentiment 以 LLM 为准；Pattern 只在 LLM 失败时做兜底。
        因为情绪判断依赖语义理解，不能靠关键词硬匹配。
        """
        sent = llm.get("sentiment", Sentiment.NEUTRAL)
        if isinstance(sent, Sentiment):
            return sent

        # LLM 给出的字符串，尝试映射
        if isinstance(sent, str):
            try:
                return Sentiment(sent)
            except ValueError:
                pass

        # LLM 失败 → Pattern 兜底
        msg = message.lower()
        for s, kws in _SENTIMENT_PATTERNS.items():
            if any(kw in msg for kw in kws):
                return s
        return Sentiment.NEUTRAL

    # ── 实体提取 ──────────────────────────────────────────────────────────────

    async def _extract_entities(self, message: str,
                                intent: IntentCategory) -> Dict[str, List[str]]:
        """
        用 LLM 从消息中提取结构化实体。

        留资场景关注: phone, wechat, budget, product, name。
        """
        message = self._clean_text(message)
        prompt = f"""从用户消息中提取实体，返回 JSON（字段值为列表，没有则为空列表）:
消息: "{message}"
格式: {{"phone":[],"wechat":[],"name":[],"product":[],"budget":[],"error_code":[]}}
其中 phone 匹配手机号（1开头11位），wechat 匹配微信号。"""
        prompt = self._clean_text(prompt)
        try:
            resp = await self.client.messages.create(
                model=self.model, max_tokens=256, temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text
            s, e = raw.find("{"), raw.rfind("}") + 1
            return json.loads(raw[s:e])
        except Exception:
            # 用正则兜底提取手机号
            phones = re.findall(r"1[3-9]\d{9}", message)
            return {"phone": phones, "wechat": [], "name": [], "product": [], "budget": [], "error_code": []}

    # ── 紧急度 ────────────────────────────────────────────────────────────────

    def _urgency(self, message: str, intent: IntentCategory,
                 sentiment: Sentiment) -> UrgencyLevel:
        """基于意图+情绪推导紧急度，不做关键词匹配。"""
        if sentiment == Sentiment.NEGATIVE and intent == IntentCategory.COMPLAINT:
            return UrgencyLevel.HIGH
        if sentiment == Sentiment.NEGATIVE:
            return UrgencyLevel.MEDIUM
        return UrgencyLevel.LOW

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    async def _load_template_embeddings(self) -> None:
        """懒加载所有模板的 Embedding。"""
        missing = [cat for cat in _TEMPLATES if cat not in self._tpl_embeddings]
        if not missing:
            return

        all_texts = [t for cat in missing for t in _TEMPLATES[cat]]
        vecs = [await self._embed_text(text) for text in all_texts]
        idx = 0
        for cat in missing:
            n = len(_TEMPLATES[cat])
            self._tpl_embeddings[cat] = vecs[idx: idx + n]
            idx += n

    async def _embed_text(self, text: str) -> List[float]:
        """生成文本向量。远端不可用时回退本地 n-gram 哈希向量。"""
        embeddings = getattr(self.client, "embeddings", None)
        if embeddings is not None:
            try:
                resp = await embeddings.create(model="voyage-3-lite", input=[text])
                return list(resp.data[0].embedding)
            except Exception as ex:
                logger.warning(f"远端 Embedding 失败，使用本地向量兜底: {ex}")

        return self._local_embedding(text)

    @staticmethod
    def _local_embedding(text: str, dims: int = 256) -> List[float]:
        """稳定的字符 n-gram 哈希向量。"""
        normalized = text.lower().strip()
        vec = [0.0] * dims
        tokens = set()
        for n in (1, 2, 3):
            if len(normalized) >= n:
                tokens.update(normalized[i:i + n] for i in range(len(normalized) - n + 1))
        if not tokens:
            tokens.add(normalized)

        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return vec

    def _cache_key(self, message: str) -> str:
        return self._clean_text(message)[:200]

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @property
    def cache_stats(self) -> Dict[str, Any]:
        total = self.cache_hits + self.cache_misses
        return {
            "size": len(self._cache),
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "hit_rate": self.cache_hits / total if total else 0.0,
        }
