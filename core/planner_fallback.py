"""
Planner 降级层 — 纯代码意图识别 + 槽位提取。

当 LLM Planner 不可用（限流 429、超时、JSON 解析失败等）时，
结合语义相似度 + 关键词加权匹配 + 正则槽位提取，
输出与正常 Planner 完全一致的 PlannerOutput。

设计原则：
  - 纯代码，零 LLM 调用，零网络依赖
  - 输出结构、字段含义与正常 Planner 完全一致
  - 可通过模块常量调整权值和阈值

降级链路：
  用户消息
    │
    ├── 1. 语义相似度匹配（ChromaDB 内置嵌入）
    │     对 few-shot 示例消息计算 cosine similarity
    │     按 intent 分组取最高分
    │
    ├── 2. 关键词加权匹配（归一化命中率）
    │     每个 intent 预定义关键词词典
    │     分数 = 命中数 / 总关键词数（消除 intent 间关键词数量差异）
    │
    ├── 3. 加权融合
    │     score = sim × 0.6 + keyword × 0.4
    │     ├── > 0.80 → 高置信度，直接使用
    │     ├── > 0.55 → 中置信度，标记降级
    │     └── ≤ 0.55 → 默认 CHITCHAT
    │
    ├── 4. 正则槽位提取
    │     phone / wechat / model / budget / location / issue
    │
    └── 5. 构造 PlannerOutput（情绪默认 neutral）
"""

import json
import logging
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import chromadb

logger = logging.getLogger(__name__)

_FS_COLLECTION = "planner_fewshot"

# ═══════════════════════════════════════════════════════════════════════════════
# 意图关键词词典
# ═══════════════════════════════════════════════════════════════════════════════
# 每个 intent 的关键词列表。分数 = 命中数 / 总关键词数（归一化）。
# 新增意图时同步更新此词典。

INTENT_KEYWORDS: Dict[str, List[str]] = {
    "greeting":     ["你好", "在吗", "嗨", "hello", "hi", "早上好", "晚上好",
                     "在不在", "哈喽", "hey", "下午好", "中午好"],
    "product_inq":  ["配置", "车型", "怎么样", "参数", "功能", "介绍",
                     "M8", "M9", "M7", "M5", "问界", "有什么", "啥"],
    "price_inq":    ["多少钱", "价格", "贵", "便宜", "报价", "价位",
                     "预算", "多少", "优惠", "折扣", "降价"],
    "purchase":     ["买", "下单", "订购", "购买", "提车", "订",
                     "怎么买", "门店", "试驾", "预约", "订车"],
    "complaint":    ["投诉", "差", "垃圾", "骗子", "滚", "服务差",
                     "不满", "态度", "没人理", "等了", "太慢", "解决"],
    "contact_give": ["电话", "微信", "联系", "号码", "加我", "加微",
                     "手机", "联系方式", "打我", "加好友"],
    "contact_no":   ["不方便", "不留", "不需要", "算了", "不要了",
                     "别", "不必", "不用了", "没兴趣"],
    "chitchat":     ["天气", "下雨", "温度", "多云", "晴", "台风",
                     "多少度", "冷", "热", "气温", "天气怎么样"],
}

# ═══════════════════════════════════════════════════════════════════════════════
# 正则槽位模式
# ═══════════════════════════════════════════════════════════════════════════════

SLOT_PATTERNS: Dict[str, re.Pattern] = {
    "phone":    re.compile(r"(1[3-9]\d{9})"),
    "wechat":   re.compile(r"(?:微信|wechat|vx|VX|wx|WX)[：:\s]*([a-zA-Z0-9_-]{6,20})"),
    "model":    re.compile(r"(?:问界\s*)?(M\d{1,2})"),
    "budget":   re.compile(r"(\d+(?:\.\d+)?)\s*万"),
    "location": re.compile(r"(北京|上海|广州|深圳|杭州|成都|武汉|南京|重庆|"
                           r"苏州|西安|长沙|天津|郑州|东莞|青岛|沈阳|宁波|昆明)"),
    "issue":    re.compile(r"(等了|没人理|服务差|质量|问题|故障|异常|出错|无法|不能|坏了)"),
}

# ═══════════════════════════════════════════════════════════════════════════════
# 加权融合参数
# ═══════════════════════════════════════════════════════════════════════════════

SIM_WEIGHT = 0.6    # 语义相似度权值
KW_WEIGHT  = 0.4    # 关键词匹配权值
HIGH_CONF  = 0.80   # 高置信度阈值（直接使用）
MED_CONF   = 0.55   # 中置信度阈值（低于此值先启发式，仍不匹配则兜底 GREETING）

# ═══════════════════════════════════════════════════════════════════════════════
# intent → sub_tasks 映射（与 intent_recognizer._INTENT_TO_SUBTASKS 保持一致）
# ═══════════════════════════════════════════════════════════════════════════════

_INTENT_TO_SUBTASKS: Dict[str, List[str]] = {
    "greeting":      ["GREETING"],
    "product_inq":   ["PRODUCT"],
    "price_inq":     ["PRICE"],
    "purchase":      ["PURCHASE", "LEAD_CAPTURE"],
    "complaint":     ["COMPLAINT"],
    "contact_give":  ["LEAD_CAPTURE"],
    "contact_no":    ["CONTACT_NO"],
    "contact_fix":   ["LEAD_CAPTURE"],
    "chitchat":      ["GREETING", "WEATHER"],
}


class PlannerFallback:
    """纯代码 Planner 降级层。

    当 LLM Planner 不可用时，结合语义相似度 + 关键词加权匹配，
    输出与正常 Planner 一致的 PlannerOutput。
    零 LLM 调用，零网络依赖。
    """

    def __init__(self):
        """初始化 PlannerFallback。

        ChromaDB collection 存储 few-shot 示例，由 ChromaDB 内置模型自动嵌入。
        首次调用 plan() 时从 intent_recognizer 加载 few-shot 数据。
        """
        self._collection = None
        self._intent_map: Dict[str, str] = {}  # chroma_id → intent_value
        self._fs_ready = False

        # 初始化 ChromaDB（本地嵌入式模式）
        try:
            client = chromadb.PersistentClient(
                path="./data/chroma",
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._collection = client.get_or_create_collection(
                name=_FS_COLLECTION,
                metadata={"description": "Planner 降级 few-shot 示例"},
            )
        except Exception as ex:
            logger.warning(f"PlannerFallback ChromaDB 初始化失败，降级为纯关键词: {ex}")

    # ── 主入口 ─────────────────────────────────────────────────────────────

    def plan(
        self,
        message: str,
        existing_slots: Optional[Dict[str, Any]] = None,
    ) -> "PlannerOutput":
        """执行降级意图识别 + 槽位提取。

        等价于 Planner._llm_plan() 的纯代码实现。

        Args:
            message: 用户消息原文
            existing_slots: 当前会话已有槽位（当前暂未使用，预留扩展）

        Returns:
            PlannerOutput，结构与正常 Planner 完全一致
        """
        from core.intent_recognizer import PlannerOutput  # 延迟加载避免循环依赖

        t0 = time.monotonic()
        self._ensure_fewshot()

        # 1. 语义相似度 + 关键词加权融合 → 排序后的 intent 列表
        fused = self._fuse(message)
        best_intent = fused[0][0] if fused else "chitchat"
        best_score = fused[0][1] if fused else 0.0

        # 2. 先提取槽位（后面的启发式规则依赖槽位信息）
        slot_ops = self._extract_slots(message, best_intent)
        slot_names = {op.slot for op in slot_ops}

        # 3. 置信度判断 + 启发式覆盖
        #    当融合分数过低时，先用启发式规则兜底；仍判断不出则走 GREETING
        if best_score < MED_CONF:
            heuristic_intent = self._heuristic_fallback(message, slot_names)
            if heuristic_intent:
                best_intent = heuristic_intent
                # 重新提取槽位（intent 变了可能影响 lead_refused 等标记）
                slot_ops = self._extract_slots(message, best_intent)
            else:
                # 语义和启发式都拿不准 → 安全兜底，引导用户说清楚
                best_intent = "greeting"
                slot_ops = []

        reasoning = self._build_reasoning(best_intent, best_score)

        # 4. 映射 sub_tasks
        sub_tasks = _INTENT_TO_SUBTASKS.get(best_intent, ["GREETING"])

        latency = (time.monotonic() - t0) * 1000

        return PlannerOutput(
            primary_intent=best_intent,
            sub_tasks=sub_tasks,
            slot_ops=slot_ops,
            emotion="neutral",
            confidence=round(best_score, 3),
            reasoning=reasoning,
            latency_ms=latency,
        )

    # ── 加权融合 ──────────────────────────────────────────────────────────

    def _fuse(self, message: str) -> List[Tuple[str, float]]:
        """加权融合语义相似度 + 关键词匹配。

        融合公式：score = sim × SIM_WEIGHT + keyword × KW_WEIGHT

        Args:
            message: 用户消息

        Returns:
            [(intent, score), ...]，按分数降序排列
        """
        sim_scores = self._semantic_scores(message)
        kw_scores = self._keyword_scores(message)

        all_intents = set(sim_scores.keys()) | set(kw_scores.keys())
        fused = []
        for intent in all_intents:
            sim = sim_scores.get(intent, 0.0)
            kw = kw_scores.get(intent, 0.0)
            total = sim * SIM_WEIGHT + kw * KW_WEIGHT
            fused.append((intent, total))

        return sorted(fused, key=lambda x: -x[1])

    def _semantic_scores(self, message: str) -> Dict[str, float]:
        """通过 ChromaDB 查询与 few-shot 示例的语义相似度。

        对每个 intent 取所有示例中的最高相似度作为该 intent 的语义分数。
        ChromaDB 不可用时返回全 0，降级为纯关键词。

        Args:
            message: 用户消息

        Returns:
            {intent: max_similarity, ...}
        """
        if not self._collection or self._collection.count() == 0:
            return defaultdict(float)

        try:
            results = self._collection.query(
                query_texts=[message],
                n_results=self._collection.count(),
            )

            intent_sim: Dict[str, float] = defaultdict(float)
            if results["ids"] and results["distances"]:
                for i, doc_id in enumerate(results["ids"][0]):
                    intent = self._intent_map.get(doc_id, "chitchat")
                    # ChromaDB 返回 L2 距离，转为 [0,1] 相似度
                    sim = 1.0 - float(results["distances"][0][i])
                    intent_sim[intent] = max(intent_sim[intent], sim)

            return intent_sim

        except Exception as ex:
            logger.warning(f"ChromaDB 语义相似度查询失败: {ex}")
            return defaultdict(float)

    def _keyword_scores(self, message: str) -> Dict[str, float]:
        """计算关键词匹配分数。

        分数 = 命中关键词数 / 该 intent 总关键词数。
        归一化保证不同规模的 intent 之间可比较。

        Args:
            message: 用户消息

        Returns:
            {intent: keyword_score, ...}
        """
        scores = {}
        for intent, kws in INTENT_KEYWORDS.items():
            if not kws:
                scores[intent] = 0.0
                continue
            hits = sum(1 for kw in kws if kw in message)
            scores[intent] = min(hits / 2, 1.0)  # 命中2个满分，1个0.5，避免被关键词总数稀释
        return scores

    # ── 槽位提取 ──────────────────────────────────────────────────────────

    def _extract_slots(self, message: str, intent: str) -> List[Any]:
        """用正则表达式提取槽位信息。

        支持：手机号、微信号、车型、预算、地点、投诉事由、拒绝留资标记。

        Args:
            message: 用户消息
            intent: 预测的意图

        Returns:
            SlotOp 列表（SET 操作）
        """
        from core.intent_recognizer import SlotOp, SlotOpType

        slot_ops = []

        # 手机号：校验 11 位且以 1 开头
        m = SLOT_PATTERNS["phone"].search(message)
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if len(digits) == 11 and digits.startswith("1"):
                slot_ops.append(SlotOp(op=SlotOpType.SET, slot="phone", value=digits))

        # 微信号
        m = SLOT_PATTERNS["wechat"].search(message)
        if m:
            slot_ops.append(SlotOp(op=SlotOpType.SET, slot="wechat", value=m.group(1)))

        # 车型
        m = SLOT_PATTERNS["model"].search(message)
        if m:
            slot_ops.append(SlotOp(op=SlotOpType.SET, slot="model", value=m.group(1)))

        # 预算
        m = SLOT_PATTERNS["budget"].search(message)
        if m:
            slot_ops.append(SlotOp(op=SlotOpType.SET, slot="budget", value=m.group(1) + "万"))

        # 地点
        m = SLOT_PATTERNS["location"].search(message)
        if m:
            slot_ops.append(SlotOp(op=SlotOpType.SET, slot="location", value=m.group(0)))

        # 投诉事由
        m = SLOT_PATTERNS["issue"].search(message)
        if m:
            slot_ops.append(SlotOp(op=SlotOpType.SET, slot="issue", value=m.group(0)))

        # 拒绝留资：intent 判定为 contact_no 时自动标记
        if intent == "contact_no":
            slot_ops.append(SlotOp(op=SlotOpType.SET, slot="lead_refused", value=True))

        return slot_ops

    # ── 辅助 ──────────────────────────────────────────────────────────────

    def _heuristic_fallback(self, message: str, slot_names: set) -> Optional[str]:
        """融合分数过低时的启发式意图兜底。

        当语义模型不可用且关键词全部未命中时，用简单规则推测意图。
        只处理有明显特征的消息，模糊的仍走默认 CHITCHAT。

        Args:
            message: 用户消息原文
            slot_names: 已提取到的槽位名称集合

        Returns:
            预测的 intent 字符串，无法判断时返回 None
        """
        # 手机号 → contact_give
        if "phone" in slot_names:
            return "contact_give"

        # 微信号 → contact_give
        if "wechat" in slot_names:
            return "contact_give"

        # 纯问候（短消息 + 无业务关键词）
        msg = message.strip()
        if len(msg) <= 6 and any(kw in msg for kw in ["你好", "嗨", "hi", "hello", "在吗"]):
            return "greeting"

        # 城市名 + 天气关键词 → chitchat（会触发 WEATHER skill）
        if "location" in slot_names:
            return "chitchat"

        return None

    def _build_reasoning(self, intent: str, score: float) -> str:
        """根据置信度构造推理说明。

        Args:
            intent: 预测的主意图
            score: 融合分数

        Returns:
            中文推理说明
        """
        if score >= HIGH_CONF:
            return f"降级: LLM 不可用，语义+关键词高置信匹配 (intent={intent}, score={score:.3f})"
        if score >= MED_CONF:
            return f"降级: LLM 不可用，语义+关键词匹配 (intent={intent}, score={score:.3f})"
        return f"降级: LLM 不可用，语义匹配无高置信结果 (best={score:.3f})，默认 CHITCHAT"

    def _ensure_fewshot(self) -> None:
        """将 few-shot 示例写入 ChromaDB（首次调用时一次写入）。

        从 intent_recognizer 加载数据，由 ChromaDB 内置模型自动嵌入。
        """
        if self._fs_ready:
            return
        self._fs_ready = True

        if not self._collection:
            return

        # collection 已有数据 → 重建 intent_map 即可
        if self._collection.count() > 0:
            existing = self._collection.get()
            for i, doc_id in enumerate(existing["ids"]):
                meta = existing["metadatas"][i] if existing["metadatas"] else {}
                self._intent_map[doc_id] = meta.get("intent", "chitchat")
            return

        # 延迟加载避免模块级循环依赖
        from core.intent_recognizer import _FEWSHOT

        ids, docs, metas = [], [], []
        for i, (cat, _, _, msg, _) in enumerate(_FEWSHOT):
            doc_id = f"fs_{i}"
            ids.append(doc_id)
            docs.append(msg)
            metas.append({"intent": cat.value, "index": i})
            self._intent_map[doc_id] = cat.value

        try:
            self._collection.add(ids=ids, documents=docs, metadatas=metas)
            logger.info(f"PlannerFallback: {len(ids)} 条 few-shot 已写入 ChromaDB")
        except Exception as ex:
            logger.warning(f"Few-shot 写入 ChromaDB 失败，降级为纯关键词匹配: {ex}")
