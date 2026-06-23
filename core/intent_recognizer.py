"""
Planner — 理解层（LLM）

职责：
  将用户自然语言输入转化为结构化信息，供编排层处理。
  Planner 是"理解层"，只做理解，不做判断和生成。

输出：
  - primary_intent: 主意图（全量）
  - sub_tasks: 子任务列表（全量，每轮完整输出）
  - slot_ops: 槽位变更（增量 Diff，SET / DELETE）
  - emotion: 用户情绪

设计原则：
  - LLM 负责理解（它擅长的）
  - 状态管理交给程序（Slot Manager / Orchestrator）
  - 输出增量 Diff 而非全量状态 —— 避免 LLM 漏字段的歧义

向后兼容：
  IntentRecognizer 保留为 Planner 的别名，供 evaluator / api 使用。
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════════════════════════════════════

class IntentCategory(Enum):
    """留资型客服意图（保持向后兼容）"""
    GREETING      = "greeting"
    PRODUCT_INQ   = "product_inq"
    PRICE_INQ     = "price_inq"
    PURCHASE      = "purchase"
    COMPLAINT     = "complaint"
    CONTACT_GIVE  = "contact_give"
    CONTACT_NO    = "contact_no"
    CONTACT_FIX   = "contact_fix"
    CHITCHAT      = "chitchat"


class Sentiment(Enum):
    """用户情绪（保持向后兼容）"""
    POSITIVE  = "positive"
    NEUTRAL   = "neutral"
    SKEPTICAL = "skeptical"
    ANXIOUS   = "anxious"
    NEGATIVE  = "negative"


class UrgencyLevel(Enum):
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4


class SlotOpType(Enum):
    """槽位操作类型"""
    SET    = "SET"
    DELETE = "DELETE"


# ═══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SlotOp:
    """单条槽位操作"""
    op:    SlotOpType
    slot:  str
    value: Any = None


@dataclass
class PlannerOutput:
    """Planner 输出 — 新架构的核心数据结构"""

    primary_intent: str               # 主意图（取值同 IntentCategory）
    sub_tasks: List[str]              # 子任务列表（对应 Skill.name）
    slot_ops: List[SlotOp]            # 槽位变更（增量 Diff）
    emotion: str                      # 用户情绪（取值同 Sentiment）
    confidence: float = 1.0           # 整体置信度
    reasoning: str = ""               # 推理说明
    latency_ms: float = 0.0           # 耗时

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_intent": self.primary_intent,
            "sub_tasks": self.sub_tasks,
            "slot_ops": [{"op": o.op.value, "slot": o.slot, "value": o.value} for o in self.slot_ops],
            "emotion": self.emotion,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


# ── 向后兼容：保持旧的 IntentResult ──────────────────────────────────────────

@dataclass
class IntentResult:
    """旧版意图识别结果（向后兼容，供 evaluator 使用）"""
    intent:      IntentCategory
    sentiment:   Sentiment
    confidence:  float
    urgency:     UrgencyLevel
    entities:    Dict[str, List[str]]
    reasoning:   str
    latency_ms:  float


# ═══════════════════════════════════════════════════════════════════════════════
# Few-shot 模板（供 Planner 的 LLM prompt 使用）
# ═══════════════════════════════════════════════════════════════════════════════

_FEWSHOT = [
    # (intent, sub_tasks_str, emotion, 示例消息, slot_ops)
    (IntentCategory.GREETING,    ["GREETING"],              "positive",  "你好",       []),
    (IntentCategory.GREETING,    ["GREETING"],              "neutral",   "在吗？",     []),
    (IntentCategory.PRODUCT_INQ, ["PRODUCT"],               "neutral",   "M8有什么配置？", [{"op":"SET","slot":"model","value":"M8"}]),
    (IntentCategory.PRODUCT_INQ, ["PRODUCT", "PRICE"],      "skeptical", "M8怎么样，贵不贵？", [{"op":"SET","slot":"model","value":"M8"}]),
    (IntentCategory.PRICE_INQ,   ["PRICE"],                 "neutral",   "多少钱一个月？", []),
    (IntentCategory.PRICE_INQ,   ["PRICE", "FINANCE"],      "skeptical", "预算20万，M8能分期吗？", [{"op":"SET","slot":"model","value":"M8"},{"op":"SET","slot":"budget","value":"20万"}]),
    (IntentCategory.PURCHASE,    ["PURCHASE", "LEAD_CAPTURE"], "positive", "我要买，怎么下单？", []),
    (IntentCategory.COMPLAINT,   ["COMPLAINT"],             "negative",  "等了这么久没人理我", [{"op":"SET","slot":"issue","value":"无人响应"}]),
    (IntentCategory.COMPLAINT,   ["COMPLAINT", "PRICE"],    "skeptical", "你们服务太差了，M8到底多少钱？", [{"op":"SET","slot":"issue","value":"服务差"},{"op":"SET","slot":"model","value":"M8"}]),
    (IntentCategory.CONTACT_GIVE, ["LEAD_CAPTURE"],         "neutral",   "13712345678", [{"op":"SET","slot":"phone","value":"13712345678"}]),
    (IntentCategory.CONTACT_GIVE, ["LEAD_CAPTURE"],         "positive",  "我的微信号是abc123", [{"op":"SET","slot":"wechat","value":"abc123"}]),
    (IntentCategory.CONTACT_NO,  ["CONTACT_NO"],            "neutral",   "不方便留电话", [{"op":"SET","slot":"lead_refused","value":True}]),
    (IntentCategory.CHITCHAT,    ["GREETING"],              "positive",  "今天天气不错", []),
    (IntentCategory.CHITCHAT,    ["GREETING"],              "neutral",   "你是机器人吗？", []),
]

# 意图 → sub_tasks 映射（给 LLM 参考）
_INTENT_TO_SUBTASKS: Dict[IntentCategory, List[str]] = {
    IntentCategory.GREETING:      ["GREETING"],
    IntentCategory.PRODUCT_INQ:   ["PRODUCT"],
    IntentCategory.PRICE_INQ:     ["PRICE"],
    IntentCategory.PURCHASE:      ["PURCHASE", "LEAD_CAPTURE"],
    IntentCategory.COMPLAINT:     ["COMPLAINT"],
    IntentCategory.CONTACT_GIVE:  ["LEAD_CAPTURE"],
    IntentCategory.CONTACT_NO:    ["CONTACT_NO"],
    IntentCategory.CONTACT_FIX:   ["CONTACT_FIX"],
    IntentCategory.CHITCHAT:      ["GREETING"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# Planner
# ═══════════════════════════════════════════════════════════════════════════════

class Planner:
    """
    理解层 — 将用户输入转化为结构化信息。

    用法：
        planner = Planner(api_key=..., model=...)
        result = await planner.plan("我想买M8", history=[...])
        # result.sub_tasks → ["PURCHASE", "LEAD_CAPTURE"]
        # result.slot_ops  → [SlotOp(SET, "model", "M8")]
    """

    def __init__(
        self,
        api_key: str = "sk-92f09f3ada494ecd8390763ff293906b",
        base_url: Optional[str] = "https://api.deepseek.com/anthropic",
        model: str = "deepseek-chat",
    ):
        self.client = AsyncAnthropic(api_key=api_key, base_url=base_url)
        self.model = model
        self._cache: Dict[str, PlannerOutput] = {}

    # ── 主入口 ─────────────────────────────────────────────────────────────

    async def plan(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        existing_slots: Optional[Dict[str, Any]] = None,
    ) -> PlannerOutput:
        """
        理解用户输入，输出结构化信息。

        参数：
            message:        用户当前输入
            history:        对话历史 [{"role":"user"/"assistant", "content":"..."}]
            existing_slots: 当前会话已有槽位（让 Planner 知道已积累的信息）
        """
        t0 = time.monotonic()

        output = await self._llm_plan(message, history, existing_slots)
        output.latency_ms = (time.monotonic() - t0) * 1000
        return output

    # ── LLM 调用 ──────────────────────────────────────────────────────────

    async def _llm_plan(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
        existing_slots: Optional[Dict[str, Any]],
    ) -> PlannerOutput:
        """调用 LLM 进行意图理解 + 槽位提取。"""
        message = self._clean_text(message)

        # 构建 Few-shot 示例
        examples = []
        for cat, tasks, emo, msg, ops in _FEWSHOT:
            ops_str = json.dumps(ops, ensure_ascii=False) if ops else "[]"
            examples.append(f'  消息: "{msg}"\n    意图: {cat.value}  子任务: {tasks}  情绪: {emo}  槽位操作: {ops_str}')

        # 会话历史
        ctx = ""
        if history:
            ctx = "\n最近对话:\n" + "\n".join(
                f"  {m.get('role', 'user')}: {self._clean_text(m.get('content', ''))}"
                for m in history[-5:]
            )

        # 已有槽位
        slots_hint = ""
        if existing_slots:
            slots_hint = f"\n已有槽位: {json.dumps(existing_slots, ensure_ascii=False)}"

        # sub_tasks 可选值
        all_sub_tasks = sorted(set(
            t for tasks in _INTENT_TO_SUBTASKS.values() for t in tasks
        ))

        prompt = f"""你是客服语义理解专家。请分析用户消息，输出 JSON。

返回格式：
{{
    "primary_intent": "主意图",
    "sub_tasks": ["子任务1", "子任务2"],
    "slot_ops": [
        {{"op": "SET", "slot": "字段名", "value": "值"}},
        {{"op": "DELETE", "slot": "字段名"}}
    ],
    "emotion": "情绪",
    "confidence": 0-1,
    "reasoning": "一句话推理"
}}

=== 示例 ===
{chr(10).join(examples)}

=== 规则 ===
- primary_intent 取值: {", ".join(c.value for c in IntentCategory)}
- sub_tasks 从以下取值（可多个）: {", ".join(all_sub_tasks)}
  - GREETING        基础问候/闲聊
  - PRODUCT         产品/车型咨询
  - PRICE           价格咨询
  - FINANCE         金融方案
  - COMPLAINT       投诉/不满
  - LEAD_CAPTURE    留资/联系方式
  - CONTACT_NO      拒绝留资
- emotion 取值: {", ".join(s.value for s in Sentiment)}
- slot_ops 用 SET 设置提取到的字段，DELETE 删除用户明确取消的字段
- 常见槽位: model(车型), budget(预算), phone(手机号), wechat(微信号),
            issue(投诉事由), product(产品名), name(姓名),
            lead_refused(拒绝留资)
- lead_refused 在用户明确说不留电话/不需要时 SET 为 true
- slot 值是用户消息中明确提到的，不要猜、不要编

=== 注意事项 ===
- 多个意图时，primary_intent 选最核心的那个，其他放 sub_tasks
- 用户报预算但没说要买 → sub_tasks 包含 PRICE、不一定要 PURCHASE
- "滚滚滚"/"骗子"是 negative；"你确定吗"只是 skeptical
- 纯数字手机号 → 提取 phone 字段
- 用户说"算了不要了" → slot_ops 删除相关字段（如 DELETE budget）{slots_hint}{ctx}

用户消息: "{message}"""

        prompt = self._clean_text(prompt)

        try:
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=512,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e])
        except Exception as ex:
            logger.warning(f"Planner LLM 调用失败: {ex}")
            return PlannerOutput(
                primary_intent=IntentCategory.CHITCHAT.value,
                sub_tasks=["GREETING"],
                slot_ops=[],
                emotion=Sentiment.NEUTRAL.value,
                confidence=0.0,
                reasoning=f"Planner 失败: {ex}",
            )

        # 解析并校验
        primary = data.get("primary_intent", IntentCategory.CHITCHAT.value)
        if isinstance(primary, str):
            # 验证是否合法的 IntentCategory
            valid_intents = {c.value for c in IntentCategory}
            if primary not in valid_intents:
                primary = IntentCategory.CHITCHAT.value

        sub_tasks = data.get("sub_tasks", [])
        if not isinstance(sub_tasks, list):
            sub_tasks = [primary]

        emotion = data.get("emotion", Sentiment.NEUTRAL.value)
        valid_emotions = {s.value for s in Sentiment}
        if emotion not in valid_emotions:
            emotion = Sentiment.NEUTRAL.value

        # 解析 slot_ops
        slot_ops = []
        raw_ops = data.get("slot_ops", [])
        if isinstance(raw_ops, list):
            for op in raw_ops:
                try:
                    op_type = SlotOpType(op.get("op", "SET"))
                    slot_ops.append(SlotOp(
                        op=op_type,
                        slot=str(op.get("slot", "")),
                        value=op.get("value"),
                    ))
                except (ValueError, KeyError):
                    continue

        return PlannerOutput(
            primary_intent=primary,
            sub_tasks=sub_tasks,
            slot_ops=slot_ops,
            emotion=emotion,
            confidence=float(data.get("confidence", 0.8)),
            reasoning=data.get("reasoning", ""),
        )

    # ── 辅助 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_text(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 向后兼容 — IntentRecognizer 包装为 Planner 的别名
# ═══════════════════════════════════════════════════════════════════════════════

class IntentRecognizer:
    """
    向后兼容包装器（旧版 IntentRecognizer 接口）。
    内部使用 Planner，输出转为旧的 IntentResult 格式。

    新代码请直接使用 Planner。
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None,
                 model: str = "claude-3-5-sonnet-20241022",
                 confidence_threshold: float = 0.5):
        self._planner = Planner(api_key=api_key, base_url=base_url, model=model)
        self.threshold = confidence_threshold
        self._cache: Dict[str, IntentResult] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    async def recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> IntentResult:
        """输出旧版 IntentResult（供 evaluator 等模块使用）。"""
        t0 = time.monotonic()
        plan = await self._planner.plan(message, history=history)

        # 将 Planner 输出转回旧的 IntentResult 格式
        try:
            intent = IntentCategory(plan.primary_intent)
        except (ValueError, KeyError):
            intent = IntentCategory.CHITCHAT

        try:
            sentiment = Sentiment(plan.emotion)
        except (ValueError, KeyError):
            sentiment = Sentiment.NEUTRAL

        entities: Dict[str, List[str]] = {}
        for op in plan.slot_ops:
            if op.op == SlotOpType.SET and op.value is not None:
                entities.setdefault(op.slot, []).append(str(op.value))

        urgency = self._calc_urgency(intent, sentiment)

        return IntentResult(
            intent=intent,
            sentiment=sentiment,
            confidence=plan.confidence,
            urgency=urgency,
            entities=entities,
            reasoning=plan.reasoning,
            latency_ms=(time.monotonic() - t0) * 1000,
        )

    @staticmethod
    def _calc_urgency(intent: IntentCategory, sentiment: Sentiment) -> UrgencyLevel:
        if sentiment == Sentiment.NEGATIVE and intent == IntentCategory.COMPLAINT:
            return UrgencyLevel.HIGH
        if sentiment == Sentiment.NEGATIVE:
            return UrgencyLevel.MEDIUM
        return UrgencyLevel.LOW

    # 保持旧的 Pattern / Embedding 方法签名（不实现，保持兼容）
    def learn(self, message: str, correct_intent: IntentCategory,
              correct_sentiment: Sentiment = Sentiment.NEUTRAL) -> None:
        logger.info(f"学习接口已调用（Planner 模式下 learn 暂不生效）: {message[:40]}")

    @property
    def cache_stats(self) -> Dict[str, Any]:
        return {"size": len(self._cache), "hits": self.cache_hits, "misses": self.cache_misses, "hit_rate": 0.0}
