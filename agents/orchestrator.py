"""
编排层 — Orchestrator + Response Agent

职责：
  Orchestrator（纯代码）：
    1. 接收 Planner 输出
    2. 应用 slot_ops 到 Slot Manager
    3. 遍历 sub_tasks，匹配 Skill，检查 slots + emotion
    4. 收集 required_tools，统一执行 Tool Layer
    5. 合并 Instruction + Context，交给 Response Agent

  Response Agent（LLM）：
    1. 接收 Instruction（要完成的任务列表）
    2. 接收 Context（Knowledge / Slots / Emotion / History）
    3. 一次生成完整回复

设计原则：
  - 编排层不做 LLM 调用（纯代码）
  - 生成层不做逻辑判断（纯 LLM）
  - 多个 Skill 共享 RAG 结果，不重复检索
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from agents.skill_registry import SkillRegistry
from agents.skills.base import Tool
from agents.slot_manager import SlotOp, SlotManager, SlotOpType
from agents.tool_layer import ToolLayer
from agents.lead_store import LeadStore
from core.intent_recognizer import Planner, PlannerOutput

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SkillStatus:
    """单个 Skill 的编排状态"""
    name:   str       # Skill 名称
    status: str       # "completed" | "pending"
    reason: str = ""  # pending 的原因


@dataclass
class ResponseInput:
    """传给 Response Agent 的完整输入"""
    instructions:  List[str]            # 所有可执行 Skill 的 instruction
    knowledge:     List[Dict[str, Any]]  # RAG 检索结果
    slots:         Dict[str, Any]       # 当前会话槽位
    emotion:       str                  # 用户情绪
    completed:     List[str]            # 已完成的 Skill 列表
    pending:       List[Dict[str, Any]]  # 待处理的 Skill（含缺失原因）
    user_message:  str                  # 用户原始输入
    user_profile:  str = ""             # 用户画像（来自 MemoryManager）
    history:       str = ""             # 对话历史


@dataclass
class OrchestratorResult:
    """Orchestrator 处理结果"""
    response:       str
    primary_intent: str
    sub_tasks:      List[str]
    emotion:        str
    skill_statuses: List[SkillStatus]
    need_rag:       bool
    latency_ms:     float


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

class Orchestrator:
    """
    编排层核心。

    纯代码，不做任何 LLM 调用。负责：
      编排 Skill → 检查状态 → 收集 Tool → 构建 Response Input
    """

    def __init__(self, tool_layer: ToolLayer):
        self.tool_layer = tool_layer

    async def orchestrate(
        self,
        planner_output: PlannerOutput,
        slot_manager: SlotManager,
        memory_context: str = "",  # 来自 MemoryManager.to_prompt_text()
    ) -> ResponseInput:
        """
        执行编排流程。

        步骤：
          1. 应用 slot_ops → 更新 Slot Manager
          2. 遍历 sub_tasks → 匹配 Skill → 检查 slot + emotion
          3. 收集 required_tools → 执行 Tool Layer
          4. 构建 Response Agent 输入
        """
        # 1. 更新槽位
        slot_ops = [
            SlotOp(
                op=SlotOpType.SET if o.op.value == "SET" else SlotOpType.DELETE,
                slot=o.slot,
                value=o.value,
            )
            for o in planner_output.slot_ops
        ]
        slot_manager.apply(slot_ops)
        slots = slot_manager.all

        # 2. 遍历 sub_tasks（后台始终追加 LEAD_CAPTURE）
        if "LEAD_CAPTURE" not in planner_output.sub_tasks:
            planner_output.sub_tasks.append("LEAD_CAPTURE")
        completed: List[str] = []
        pending: List[Dict[str, Any]] = []
        all_instructions: List[str] = []
        all_tools: set = set()

        for task in planner_output.sub_tasks:
            skill = SkillRegistry.get(task)
            if skill is None:
                logger.debug(f"编排: 跳过未知 sub_task={task}")
                continue

            # 检查 emotion
            if not skill.check_emotion(planner_output.emotion):
                pending.append({"skill": task, "reason": f"情绪 '{planner_output.emotion}' 不适合执行此任务"})
                continue

            # 检查 slots
            missing = skill.check_slots(slots)
            if missing:
                pending.append({"skill": task, "missing": missing})
                continue

            # 可执行
            completed.append(task)
            all_instructions.append(skill.instruction)
            all_tools.update(skill.required_tools)

        # 3. 统一执行 Tool Layer
        tool_results: Dict[str, Any] = {}
        if all_tools:
            tool_results = await self.tool_layer.exec_tools(
                required_tools=list(all_tools),
                user_query=planner_output.primary_intent,
                sub_tasks=planner_output.sub_tasks,
                slots=slots,
            )

        # 4. 构建 Response 输入
        return ResponseInput(
            instructions=all_instructions,
            knowledge=tool_results.get("rag", []),
            slots=slots,
            emotion=planner_output.emotion,
            completed=completed,
            pending=pending,
            user_message="",
            user_profile=memory_context,
            history="",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Response Agent
# ═══════════════════════════════════════════════════════════════════════════════

class ResponseAgent:
    """
    生成层 — 根据 Instruction + Context 生成回复。

    唯一的职责：生成自然语言回复。
    不做逻辑判断（那是 Orchestrator 的事），不做理解（那是 Planner 的事）。
    """

    # 统一回复规范（始终附加在 system prompt 尾部）
    _BASE_INSTRUCTION = """
回复规范：
1. 语气专业亲和，使用中文
2. 先安抚后回复（用户情绪差时，先共情再解决问题）
3. 使用 Knowledge 中的内容回答，没有知识来源的不要编造
4. 如果多个任务，回复时要自然过渡，不要生硬分段
5. 如果 pending 中有等待的信息，在回复末尾自然追问
6. 不要在知识未覆盖的领域提供具体承诺（如具体价格、到货时间）
7. 不要直接输出 JSON 或内部数据格式
"""

    def __init__(self, client: AsyncAnthropic, model: str):
        self._client = client
        self._model = model

    async def generate(self, input_data: ResponseInput) -> str:
        """
        生成回复。

        输入：
            ResponseInput（Instruction + Context）
        输出：
            回复文本
        """
        # 构建 System Prompt
        system_parts = []

        # 1) 角色定义
        system_parts.append("你是 EchoMind 智能客服助手。以下是你要完成的任务：")

        # 2) Skill Instructions
        if input_data.instructions:
            system_parts.append("【任务】")
            for i, instr in enumerate(input_data.instructions, 1):
                system_parts.append(f"{i}. {instr}")

        # 3) Pending 提示
        if input_data.pending:
            system_parts.append("\n【待办】")
            for p in input_data.pending:
                if "missing" in p:
                    system_parts.append(f"- {p['skill']}: 缺少 {', '.join(p['missing'])}，在回复末尾自然追问")
                else:
                    system_parts.append(f"- {p['skill']}: {p.get('reason', '暂不处理')}")

        # 4) 统一规范
        system_parts.append(self._BASE_INSTRUCTION)

        system_prompt = "\n".join(system_parts)

        # 构建 User Message
        msg_parts = []

        # 用户原始消息
        if input_data.user_message:
            msg_parts.append(f"用户说: {input_data.user_message}")

        # Knowledge
        if input_data.knowledge:
            kb_text = "\n".join(
                f"  [{i+1}] {c.get('content', c.get('document', ''))[:300]}"
                for i, c in enumerate(input_data.knowledge)
            )
            msg_parts.append(f"\n【知识】\n{kb_text}")

        # Slots
        if input_data.slots:
            slots_text = json.dumps(input_data.slots, ensure_ascii=False)
            msg_parts.append(f"\n【信息】\n{slots_text}")

        # Emotion
        msg_parts.append(f"\n【情绪】\n{input_data.emotion}")

        # 用户画像 / 历史（来自 MemoryManager）
        if input_data.user_profile:
            msg_parts.append(f"\n【背景】\n{input_data.user_profile}")

        user_content = "\n".join(msg_parts)

        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                temperature=0.3,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            return resp.content[0].text
        except Exception as ex:
            logger.error(f"Response Agent 生成失败: {ex}")
            return "抱歉，我暂时无法回答您的问题，请稍后重试。"


# ═══════════════════════════════════════════════════════════════════════════════
# 统一入口（串联完整链路）
# ═══════════════════════════════════════════════════════════════════════════════

class AgentEngine:
    """
    Agent 引擎 — 串联 Planner → Orchestrator → Response Agent 的完整链路。

    这是新架构的统一入口，替代旧的 AgentOrchestrator。

    用法：
        engine = AgentEngine(api_key=..., model=..., knowledge_base=kb)
        result = await engine.run(
            message="我想买M8",
            user_id="u1",
            conv_id="c1",
            memory_context=memory.to_prompt_text(),
            history=[...],
        )
    """

    def __init__(
        self,
        knowledge_base=None,
        redis_url: str = "redis://localhost:6379/0",
    ):
        client = AsyncAnthropic(
            api_key="sk-92f09f3ada494ecd8390763ff293906b",
            base_url="https://api.deepseek.com/anthropic",
        )

        from agents.slot_manager import SlotManager

        self.planner = Planner(
            api_key="sk-92f09f3ada494ecd8390763ff293906b",
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-chat",
        )
        self.tool_layer = ToolLayer(knowledge_base) if knowledge_base else None
        self.orchestrator = Orchestrator(tool_layer=self.tool_layer)
        self.response_agent = ResponseAgent(client, "deepseek-chat")
        self.model = "deepseek-chat"
        self.lead_store = LeadStore(redis_url)

        # SlotManager 使用 Redis 后端（跨会话持久化）
        # getattr 保护：LeadStore 连接失败时 _redis 可能未设置
        self._redis_client = getattr(self.lead_store, "_redis", None)

    def _get_slot_manager(self, user_id: str, conv_id: str) -> SlotManager:
        """获取会话级 SlotManager（Redis 后端，跨会话持久化）。"""
        from agents.slot_manager import SlotManager
        return SlotManager(
            redis_client=self._redis_client,
            redis_key=f"slot:{user_id}:{conv_id}",
        )

    def reset_slots(self, user_id: str, conv_id: str) -> None:
        """重置指定用户的会话槽位。"""
        sm = self._get_slot_manager(user_id, conv_id)
        sm.reset()

    async def run(
        self,
        message: str,
        conv_id: str = "",
        user_id: str = "",
        memory_context: str = "",
        history: Optional[List[Dict[str, str]]] = None,
    ) -> OrchestratorResult:
        """
        完整链路：理解 → 编排 → 生成
        """
        t0 = time.monotonic()

        # 获取会话级 SlotManager
        slot_manager = self._get_slot_manager(user_id, conv_id)

        # 0. 跨会话冷却：Redis TTL 天然跨会话，不需要额外注入逻辑
        if user_id and self.lead_store.is_in_cooldown(user_id):
            slot_manager.set("lead_refused", True)

        # 1. Planner（理解）
        planner_output = await self.planner.plan(
            message=message,
            history=history,
            existing_slots=slot_manager.all,
        )

        # 2. Orchestrator（编排）
        response_input = await self.orchestrator.orchestrate(
            planner_output=planner_output,
            slot_manager=slot_manager,
            memory_context=memory_context,
        )
        response_input.user_message = message

        # 2.5 留资持久化（Redis） + 拒绝留资记录（Redis TTL）
        if user_id:
            slots = slot_manager.all
            # 收集到留资信息 → 写入 Redis（setex 无 TTL，永久保留）
            if slots.get("phone") or slots.get("wechat"):
                self.lead_store.save_lead_from_slots(user_id, slots)
            # CONTACT_NO 完成 → 写入 Redis 带 24h TTL，自动过期 = 时间窗口
            if "CONTACT_NO" in response_input.completed:
                self.lead_store.record_refusal(user_id)

        # 3. Response Agent（生成）
        response = await self.response_agent.generate(response_input)

        total_ms = (time.monotonic() - t0) * 1000

        return OrchestratorResult(
            response=response,
            primary_intent=planner_output.primary_intent,
            sub_tasks=planner_output.sub_tasks,
            emotion=planner_output.emotion,
            skill_statuses=[
                SkillStatus(name=s, status="completed")
                for s in response_input.completed
            ] + [
                SkillStatus(name=p.get("skill", ""), status="pending", reason=str(p.get("missing", p.get("reason", ""))))
                for p in response_input.pending
            ],
            need_rag=bool(response_input.knowledge),
            latency_ms=total_ms,
        )
