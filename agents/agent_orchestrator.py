"""
亮点：多 Agent 路由与编排 — 留资型智能客服

路由策略（三层决策）：
  1. 意图路由 —— 根据 IntentCategory 直接映射到专属 Agent
  2. 性能路由 —— 同类 Agent 有多个时，选成功率最高、延迟最低的
  3. 降级路由 —— 专属 Agent 不可用时，自动降级到 ConsultAgent

留资决策：
  - 先解答问题（RAG 知识库），再判断时机是否适合引导留资
  - 拒绝后冷却 3 轮，不重复骚扰
  - 已留资用户不再引导

升级机制：
  - Agent 置信度低于阈值 → 自动升级到更高级 Agent 或转人工
"""
import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import redis
from anthropic import AsyncAnthropic

from core.intent_recognizer import IntentCategory, IntentRecognizer, Sentiment, UrgencyLevel

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class AgentType(Enum):
    GREETING     = "greeting"      # 问候 + 探需求
    CONSULT      = "consult"       # 产品介绍 + 答疑 + 引导留资（主力）
    LEAD_CAPTURE = "lead_capture"  # 专门处理留资：验证、确认、存储
    ESCALATION   = "escalation"    # 投诉/负面 → 转人工


@dataclass
class AgentStats:
    """Agent 运行时统计。"""
    total:     int   = 0
    success:   int   = 0
    total_ms:  float = 0.0
    monitor_penalty: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.total if self.total else 0.0

    def routing_score(self) -> float:
        latency_score = 1.0 / (1.0 + self.avg_ms / 1000)
        base_score = self.success_rate * 0.7 + latency_score * 0.3
        return base_score * max(0.0, 1.0 - self.monitor_penalty)


@dataclass
class AgentResponse:
    agent_type:  AgentType
    content:     str
    success:     bool
    confidence:  float = 1.0
    latency_ms:  float = 0.0
    escalate:    bool  = False
    ask_contact: bool  = False   # 本轮是否引导了留资


@dataclass
class Request:
    message:     str
    user_id:     str
    conv_id:     str
    context:     str = ""
    history:     Optional[List[Dict[str, str]]] = None
    intent:      Optional[IntentCategory] = None
    sentiment:   Optional[Sentiment]        = None
    urgency:     Optional[UrgencyLevel]      = None
    request_id:  str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class OrchestratorResult:
    request_id:  str
    response:    str
    agent_type:  AgentType
    intent:      Optional[IntentCategory]
    sentiment:   Optional[Sentiment]
    escalated:   bool  = False
    ask_contact: bool  = False   # 本轮是否引导了留资
    latency_ms:  float = 0.0


# ── 基础 Agent ────────────────────────────────────────────────────────────────

class BaseAgent:
    """所有 Agent 的基类。"""

    agent_type: AgentType
    system_prompt: str

    def __init__(self, client: AsyncAnthropic, model: str):
        self._client = client
        self._model  = model
        self.stats   = AgentStats()

    async def handle(self, req: Request) -> AgentResponse:
        t0 = time.monotonic()
        self.stats.total += 1
        try:
            content = await self._call_llm(req)
            ms = (time.monotonic() - t0) * 1000
            self.stats.success += 1
            self.stats.total_ms += ms
            escalate = self._needs_escalation(content)
            return AgentResponse(
                agent_type=self.agent_type,
                content=content,
                success=True,
                latency_ms=ms,
                escalate=escalate,
            )
        except Exception as ex:
            ms = (time.monotonic() - t0) * 1000
            self.stats.total_ms += ms
            logger.error(f"{self.agent_type.value} 处理失败: {ex}")
            return AgentResponse(
                agent_type=self.agent_type,
                content="抱歉，处理您的请求时出现问题，请稍后重试。",
                success=False,
                latency_ms=ms,
            )

    async def _call_llm(self, req: Request) -> str:
        def _clean(s: str) -> str:
            return s.encode("utf-8", errors="ignore").decode("utf-8")

        messages = []
        if req.context:
            messages.append({"role": "user", "content": f"[背景信息]\n{_clean(req.context)}"})
            messages.append({"role": "assistant", "content": "好的，我已了解背景信息。"})
        messages.append({"role": "user", "content": _clean(req.message)})

        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=self.system_prompt,
            messages=messages,
        )
        return resp.content[0].text

    @staticmethod
    def _needs_escalation(content: str) -> bool:
        """只有 Agent 明确表示无法处理时才触发升级，避免礼貌话术误判。"""
        return "无法处理" in content


# ── 具体 Agent ────────────────────────────────────────────────────────────────

class GreetingAgent(BaseAgent):
    agent_type = AgentType.GREETING
    system_prompt = (
        "你是留资型智能客服的开场接待。"
        "任务：友好问候 + 快速探明用户需求。"
        "如果用户打了招呼但没说目的，主动问一句想了解哪方面。"
        "保持轻松自然，不要直接要联系方式。"
    )


class ConsultAgent(BaseAgent):
    agent_type = AgentType.CONSULT
    system_prompt = (
        "你是留资型智能客服的主力咨询顾问。"
        "任务："
        "1. 先依据知识库内容准确回答用户问题（产品、价格、功能等）"
        "2. 回答完后，如果系统标记为合适的留资时机，自然引导留资。"
        "   话术示例：'方便留个联系方式吗？我让顾问给您发详细资料。'"
        "3. 如果用户给出联系方式（手机号/微信号），确认并感谢"
        "4. 如果用户拒绝留资，不要纠缠，说'没关系，有需要随时找我'"
        "5. 如果用户质疑或焦虑，先安抚解决顾虑，不要急着要联系方式"
        "保持专业亲和，以提供价值为先。"
    )


class LeadCaptureAgent(BaseAgent):
    agent_type = AgentType.LEAD_CAPTURE
    system_prompt = (
        "你是留资型智能客服的联系方式收集专家。"
        "任务："
        "1. 从用户消息中识别手机号（11位1开头）或微信号"
        "2. 如果格式正确，确认并感谢：'收到，138xxxx，稍后顾问联系您！'"
        "3. 如果格式可疑（位数不对），温和请用户确认：'确认一下号码是138xxxx吗？'"
        "4. 如果用户更正了联系方式，确认新信息"
        "5. 不要重复索要已提供的联系方式"
        "保持高效、不拖沓。"
    )


class EscalationAgent(BaseAgent):
    agent_type = AgentType.ESCALATION
    system_prompt = (
        "你是留资型智能客服的升级处理专家。"
        "任务：处理投诉、强烈负面情绪的客户。"
        "1. 先真诚道歉、安抚情绪"
        "2. 说明会将问题升级给人工团队处理"
        "3. 不要在此时引导留资或推销"
        "4. 如果用户要求删除个人信息，确认并执行"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 编排器
# ═══════════════════════════════════════════════════════════════════════════════

class AgentOrchestrator:
    """
    多 Agent 编排器 — 留资型智能客服。

    路由逻辑（三层）：
      1. 意图 → Agent 类型映射
      2. 同类多实例时按 routing_score() 选最优
      3. 专属 Agent 失败时降级到 ConsultAgent

    留资决策：
      - 先回答，再判断是否引导留资（_should_ask_contact）
      - 拒绝后冷却 3 轮
      - 已留资用户不再引导
    """

    # 意图 → Agent 类型的路由表
    _INTENT_ROUTING: Dict[IntentCategory, AgentType] = {
        IntentCategory.GREETING:      AgentType.GREETING,
        IntentCategory.PRODUCT_INQ:   AgentType.CONSULT,
        IntentCategory.PRICE_INQ:     AgentType.CONSULT,
        IntentCategory.PURCHASE:      AgentType.CONSULT,
        IntentCategory.COMPLAINT:     AgentType.ESCALATION,
        IntentCategory.CONTACT_GIVE:  AgentType.LEAD_CAPTURE,
        IntentCategory.CONTACT_NO:    AgentType.CONSULT,
        IntentCategory.CONTACT_FIX:   AgentType.LEAD_CAPTURE,
        IntentCategory.CHITCHAT:      AgentType.GREETING,
    }

    # 留资引导策略
    ASK_COOLDOWN = 3          # 拒绝后冷却轮数
    CONTACT_NO_KEY_PREFIX = "lead:refused:"   # Redis key 前缀

    def __init__(
        self,
        redis_url: Optional[str] = None,
    ):
        api_key = "sk-92f09f3ada494ecd8390763ff293906b"
        base_url = "https://api.deepseek.com/anthropic"
        model = "deepseek-chat"
        client = AsyncAnthropic(api_key=api_key, base_url=base_url)

        self._intent_recognizer = IntentRecognizer(
            api_key=api_key, base_url=base_url, model=model,
        )

        # Agent 池
        self._pool: Dict[AgentType, List[BaseAgent]] = {
            AgentType.GREETING:     [GreetingAgent(client, model)],
            AgentType.CONSULT:      [ConsultAgent(client, model)],
            AgentType.LEAD_CAPTURE: [LeadCaptureAgent(client, model)],
            AgentType.ESCALATION:   [EscalationAgent(client, model)],
        }

        # Redis（留资拒绝冷却）
        self._redis: Optional[redis.Redis] = None
        if redis_url:
            try:
                self._redis = redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
            except Exception as ex:
                logger.warning(f"Redis 不可用，留资冷却功能禁用: {ex}")
                self._redis = None

    # ── 公开意图识别 ──────────────────────────────────────────────────────────

    async def recognize_intent(self, message: str,
                               history: Optional[List[Dict[str, str]]] = None):
        """供 API 层在 RAG 之前调用，用真实意图替代硬编码门控。"""
        return await self._intent_recognizer.recognize(message, history=history)

    # ── 主入口 ────────────────────────────────────────────────────────────────

    async def run(self, req: Request) -> OrchestratorResult:
        """
        处理一次请求的完整流程：
          意图识别 → 留资决策 → 路由 → 执行 → 检查升级 → 返回
        """
        t0 = time.monotonic()

        # 1. 意图 + 情绪识别
        if req.intent is None:
            intent_result = await self._intent_recognizer.recognize(
                req.message, history=req.history,
            )
            req.intent    = intent_result.intent
            req.sentiment = intent_result.sentiment
            req.urgency   = intent_result.urgency

        # 2. 留资决策：本轮是否应该引导留资
        should_ask = self._should_ask_contact(req)

        # 3. 路由选 Agent（综合意图 + 紧急度 + 情绪）
        agent_type = self._route(req.intent, req.urgency, req.sentiment)

        # 3.5 将留资决策信号注入上下文，让 Agent 在 LLM 调用时感知
        if agent_type == AgentType.CONSULT:
            if should_ask:
                req.context = (req.context or "") + \
                    "\n[系统指令] 本轮判断用户适合留资。请在回答完问题后，自然邀请用户留下手机号或微信号。"
            else:
                req.context = (req.context or "") + \
                    "\n[系统指令] 本轮不要引导留资。请专注解答用户疑问，先建立信任、解决顾虑。"

        # 4. 执行
        response = await self._execute(req, agent_type)

        # 5. 标记本轮是否引导了留资
        response.ask_contact = should_ask and agent_type == AgentType.CONSULT

        # 6. 升级检查：Agent 自行判断无法处理 或 紧急度为 HIGH
        escalated = response.escalate or req.urgency == UrgencyLevel.HIGH
        if escalated:
            logger.warning(f"请求 {req.request_id} 触发升级: intent={req.intent} sentiment={req.sentiment}")

        # 7. 记录拒绝冷却
        if req.intent == IntentCategory.CONTACT_NO and self._redis:
            key = self._cooling_key(req.user_id)
            self._redis.setex(key, 3600, str(time.time()))
            logger.info(f"用户 {req.user_id} 拒绝留资，冷却 {self.ASK_COOLDOWN} 轮")

        return OrchestratorResult(
            request_id=req.request_id,
            response=response.content,
            agent_type=response.agent_type,
            intent=req.intent,
            sentiment=req.sentiment,
            escalated=escalated,
            ask_contact=response.ask_contact,
            latency_ms=(time.monotonic() - t0) * 1000,
        )

    # ── 留资决策 ──────────────────────────────────────────────────────────────

    def _should_ask_contact(self, req: Request) -> bool:
        """
        判断本轮对话是否应该引导用户留联系方式。

        决策逻辑：
          1. 意图必须是适合引导的类型
          2. 情绪不能是 NEGATIVE / ANXIOUS
          3. 不在冷却期内（上次拒绝后未满 3 轮）
        """
        # 只有这些意图才适合引导留资
        if req.intent not in (
            IntentCategory.PURCHASE,
            IntentCategory.PRICE_INQ,
            IntentCategory.PRODUCT_INQ,
        ):
            return False

        # SKEPTICAL → 先提供信任状，本回合暂不引导
        # ANXIOUS  → 先安抚，不引导
        # NEGATIVE → 绝对不引导
        if req.sentiment in (Sentiment.SKEPTICAL, Sentiment.ANXIOUS, Sentiment.NEGATIVE):
            return False

        # PURCHASE + POSITIVE → 一定引导
        if req.intent == IntentCategory.PURCHASE and req.sentiment == Sentiment.POSITIVE:
            return True

        # 检查冷却期
        if self._redis:
            key = self._cooling_key(req.user_id)
            last_refused = self._redis.get(key)
            if last_refused:
                elapsed = time.time() - float(last_refused)
                if elapsed < self.ASK_COOLDOWN * 60:  # 冷却窗口（默认 3 分钟）
                    return False

        return True

    # ── 路由逻辑 ──────────────────────────────────────────────────────────────

    def _route(self, intent: Optional[IntentCategory],
               urgency: Optional[UrgencyLevel],
               sentiment: Optional[Sentiment] = None) -> AgentType:
        # COMPLAINT + 非愤怒（质疑/焦虑/中性）→ 用户可能只是带情绪提问，先答疑
        if intent == IntentCategory.COMPLAINT and sentiment != Sentiment.NEGATIVE:
            return AgentType.CONSULT

        if intent and intent in self._INTENT_ROUTING:
            target = self._INTENT_ROUTING[intent]
            if target in self._pool and self._pool[target]:
                return target

        return AgentType.CONSULT

    # ── 执行 ──────────────────────────────────────────────────────────────────

    def _best_agent(self, agent_type: AgentType) -> Optional[BaseAgent]:
        agents = self._pool.get(agent_type, [])
        if not agents:
            return None
        return max(agents, key=lambda a: a.stats.routing_score())

    async def _execute(self, req: Request, agent_type: AgentType) -> AgentResponse:
        agent = self._best_agent(agent_type)
        if agent is None:
            agent = self._best_agent(AgentType.CONSULT)
        if agent is None:
            return AgentResponse(
                agent_type=AgentType.CONSULT,
                content="服务暂时不可用，请稍后重试。",
                success=False,
            )

        response = await agent.handle(req)

        # 失败降级
        if not response.success and agent_type != AgentType.CONSULT:
            logger.warning(f"{agent_type.value} 失败，降级到 ConsultAgent")
            fallback = self._best_agent(AgentType.CONSULT)
            if fallback:
                response = await fallback.handle(req)

        return response

    # ── 线索存储 ──────────────────────────────────────────────────────────────

    def store_lead(self, user_id: str, phone: str = "",
                   wechat: str = "", source: str = "chat") -> bool:
        """
        将用户线索存入 Redis（双向索引，无过期时间）。

        Key: lead:phone:{phone}  → {"user_id": ..., "phone": ..., ...}
        Key: lead:user:{user_id} → {"user_id": ..., "phone": ..., ...}
        """
        if not self._redis:
            logger.warning("Redis 未连接，无法存储线索")
            return False
        if not phone and not wechat:
            return False

        lead_data = json.dumps({
            "user_id": user_id,
            "phone":   phone,
            "wechat":  wechat,
            "source":  source,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })

        try:
            for key in (f"lead:user:{user_id}", f"lead:phone:{phone}"):
                self._redis.set(key, lead_data)
            logger.info(f"线索已存储: user={user_id} phone={phone} wechat={wechat}")
            return True
        except Exception as ex:
            logger.error(f"线索存储失败: {ex}")
            return False

    def get_lead(self, user_id: str = "", phone: str = "") -> Optional[Dict[str, str]]:
        """查询用户线索。"""
        if not self._redis:
            return None
        try:
            key = f"lead:user:{user_id}" if user_id else f"lead:phone:{phone}"
            raw = self._redis.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        result = {}
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                result[key] = {
                    "total":        agent.stats.total,
                    "success_rate": round(agent.stats.success_rate, 3),
                    "avg_ms":       round(agent.stats.avg_ms, 1),
                    "monitor_penalty": round(agent.stats.monitor_penalty, 3),
                    "routing_score": round(agent.stats.routing_score(), 3),
                }
        return result

    def update_routing_penalties(self, penalties: Dict[str, float]) -> None:
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                penalty = penalties.get(key, 0.0)
                agent.stats.monitor_penalty = min(max(penalty, 0.0), 0.9)

    @staticmethod
    def _cooling_key(user_id: str) -> str:
        return f"lead:refused:{user_id}"
