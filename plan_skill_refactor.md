# Skill 解耦重构计划（v2）

## 问题摘要

1. **`can_execute()` 是死代码** — 定义了但没人调，导致 `lead_refused` 检查从未生效
2. **LEAD_CAPTURE 无条件追加** — `orchestrator.py:123` 每轮硬编码追加，和 CONTACT_NO 产生矛盾
3. **槽位残留** — `lead_refused` 在用户更改联系方式后不会自动清除，依赖 LLM 发 DELETE
4. **耦合** — Orchestrator 硬编码了 LEAD_CAPTURE 的特殊逻辑

## 改动思路

三个机制让 Skill 自包含、Orchestrator 统一调度：

| 机制 | 用途 | 归属 |
|------|------|------|
| `can_execute()` | Skill 自己判断能否执行 | BaseSkill + 子类 |
| `get_pending_info()` | can_execute 返回 False 时，告知 Orchestrator 原因 | BaseSkill + 子类 |
| `auto_evaluate` | 标记为后台持续评估（取代硬编码追加） | BaseSkill 类属性 |

外加一条**槽位生命周期规则**（在 Orchestrator 的 slot_ops 应用之后）：
- `phone SET → 自动清除 lead_refused / lead_refused_at`

---

## 核心变化

### 相比之前讨论的变更

| 之前 | 现在 | 原因 |
|------|------|------|
| CONTACT_FIX + LEAD_CAPTURE + CONTACT_NO | 只留 LEAD_CAPTURE + CONTACT_NO | 生命周期规则覆盖了改号场景 |
| `_lead_confirmed` 内部状态追踪 | 不要 `_lead_confirmed` | LLM 有对话历史，不需要额外标记 |
| `on_execute()` 处理槽位副作用 | 统一交给槽位生命周期规则 | 集中管理，不散落在 skills 里 |

---

## 改动细节

### 文件 1：`agents/skills/base.py`

**目标**：添加 `auto_evaluate`、`can_execute()`、`get_pending_info()`。

```python
class BaseSkill:
    name: str = ''
    required_slots: List[str] = []
    optional_slots: List[str] = []
    required_tools: List[Tool] = []
    instruction: str = ''
    # ── 新增 ──
    auto_evaluate: bool = False  # True = 不在 sub_tasks 中也自动评估

    # ── check_slots / check_emotion 保持不变 ──
    @classmethod
    def check_slots(cls, slots) -> List[str]:
        return [s for s in cls.required_slots if s not in slots or slots[s] is None]

    @classmethod
    def check_emotion(cls, emotion) -> bool:
        return True

    # ── 新增：统一执行入口 ──
    @classmethod
    def can_execute(cls, slots: Dict[str, Any], emotion: str) -> bool:
        """
        判断技能是否可以执行。子类可完全重写。
        默认行为 = check_emotion + check_slots。
        """
        if not cls.check_emotion(emotion):
            return False
        return len(cls.check_slots(slots)) == 0

    # ── 新增：执行失败的原因诊断 ──
    @classmethod
    def get_pending_info(cls, slots: Dict[str, Any], emotion: str) -> Dict[str, Any]:
        """
        can_execute 返回 False 时的结构化原因。
        返回值中的字段：
          "reason"  → 字符串原因，展示给 LLM（如"情绪不适合"）
          "missing" → 缺失槽位列表，LLM 会自然追问
          "silent"  → True 时 Orchestrator 直接跳过，不给 LLM

        子类覆盖此方法时，须与 can_execute 的判断逻辑一致。
        """
        info: Dict[str, Any] = {}
        if not cls.check_emotion(emotion):
            info["reason"] = f"情绪 '{emotion}' 不适合执行此任务"
        elif missing := cls.check_slots(slots):
            info["missing"] = missing
        else:
            info["silent"] = True
        return info
```

### 文件 2：`agents/skills/lead_capture.py`

**目标**：`can_execute()` 真正生效，`auto_evaluate = True`，覆盖 `get_pending_info()`。

```python
class LeadCaptureSkill(BaseSkill):
    name = "LEAD_CAPTURE"
    required_slots = ["phone"]
    required_tools = []
    auto_evaluate = True     # 每轮自动评估

    instruction = (
        "用户提供了联系方式，确认并感谢。"
        "告知用户稍后会有顾问联系。"
    )

    @classmethod
    def check_emotion(cls, emotion: str) -> bool:
        blocked = {"angry", "very_negative", "negative", "skeptical"}
        return emotion.lower() not in blocked

    @classmethod
    def _is_lead_refused(cls, slots: Dict[str, Any]) -> bool:
        """保持不变：检查 lead_refused + 24h 冷却"""
        # ... 已有代码不变 ...

    @classmethod
    def can_execute(cls, slots: Dict[str, Any], emotion: str = "") -> bool:
        """
        执行条件（按优先级）：
        - 情绪差         → False
        - 拒绝冷却期内   → False
        - phone 缺失     → False（pending → LLM 追问）
        - phone 存在     → True（确认联系方式）
        """
        if not cls.check_emotion(emotion):
            return False
        if cls._is_lead_refused(slots):
            return False
        return len(cls.check_slots(slots)) == 0  # phone 必需

    @classmethod
    def get_pending_info(cls, slots: Dict[str, Any], emotion: str) -> Dict[str, Any]:
        """
        覆盖基类：把 lead_refused 放在 check_slots 前面判断，
        避免"拒绝留资但 phone 缺失"时被错误诊断为"缺少 phone"。
        """
        if not cls.check_emotion(emotion):
            return {"reason": f"情绪 '{emotion}' 不适合执行此任务"}
        if cls._is_lead_refused(slots):
            return {"silent": True}     # 拒绝了，安静跳过
        if not slots.get("phone"):
            return {"missing": ["phone"]}  # 缺 phone，LLM 追问
        return {"silent": True}
```

### 文件 3：`agents/skills/contact_fix.py`

**删除**。其职责被槽位生命周期规则覆盖。

### 文件 4：`agents/orchestrator.py`

**目标**：去掉硬编码追加，改为 `can_execute` + `get_pending_info` 的统一编排。新增槽位生命周期规则。

```python
async def orchestrate(self, planner_output, slot_manager, ...):

    # 1. 应用 slot_ops
    slot_manager.apply(slot_ops)

    # ── 槽位生命周期规则 ──
    # 当用户给了新手机号时，自动清除所有旧标记
    # 不依赖 Planner 发 DELETE，不依赖任何 skill
    for op in planner_output.slot_ops:
        if op.op.value == "SET" and op.slot in ("phone", "wechat"):
            slot_manager.delete("lead_refused")
            slot_manager.delete("lead_refused_at")

    slots = slot_manager.all

    # 2. 构建任务列表：显式 sub_tasks + auto_evaluate 技能
    all_tasks = list(planner_output.sub_tasks)
    for name, skill_cls in SkillRegistry.all().items():
        if skill_cls.auto_evaluate and name not in all_tasks:
            all_tasks.append(name)

    # 3. 统一评估循环
    completed: List[str] = []
    pending: List[Dict[str, Any]] = []
    all_instructions: List[str] = []
    all_tools: set = set()

    for task in all_tasks:
        skill = SkillRegistry.get(task)
        if skill is None:
            logger.debug(f"编排: 跳过未知 sub_task={task}")
            continue

        if skill.can_execute(slots, planner_output.emotion):
            completed.append(task)
            all_instructions.append(skill.instruction)
            all_tools.update(skill.required_tools)
        else:
            info = skill.get_pending_info(slots, planner_output.emotion)
            if info.get("silent"):
                continue          # 安静跳过，不打扰用户
            pending.append({"skill": task, **info})

    # 4. 统一执行 Tool Layer（不变）
    ...

    # 5. 构建 Response 输入（不变）
    ...
```

---

## 场景验证

### 场景 1：用户第一次留资

```
用户: "我电话138xxxx"
Planner: sub_tasks=["LEAD_CAPTURE"], slot_ops=[SET phone=138xxxx]
emotion=neutral

① slot_manager.apply → phone=138xxxx
   生命周期: phone SET → 清 lead_refused（无操作）

② all_tasks = ["LEAD_CAPTURE"]

③ LEAD_CAPTURE.can_execute:
   - 情绪 neutral → 通过
   - _is_lead_refused → False（无标记）
   - check_slots → phone 存在 → 通过
   → completed

④ LLM 收到 instruction: "用户提供了联系方式，确认并感谢。告知用户稍后会有顾问联系。"
结果: ✓ 确认联系方式
```

### 场景 2：用户拒绝留资

```
用户: "别打我电话"
Planner: sub_tasks=["CONTACT_NO"], slot_ops=[SET lead_refused=True]
emotion=neutral

① slot_manager.apply → lead_refused=True
   生命周期: 没有 SET phone → 不触发

② all_tasks = ["CONTACT_NO", "LEAD_CAPTURE"]

③ CONTACT_NO.can_execute → 通过 → completed
   LLM: "表示理解，尊重用户的选择，绝不追问"

④ LEAD_CAPTURE.can_execute:
   - 情绪 neutral → 通过
   - _is_lead_refused → True → return False
   → get_pending_info → {"silent": True}
   → continue（安静跳过）
结果: ✓ CONTACT_NO 表态，LEAD_CAPTURE 安静，无矛盾
```

### 场景 3：用户改手机号（之前拒绝过）

```
用户: "改成139xxxx"
Planner: sub_tasks=["LEAD_CAPTURE"], slot_ops=[SET phone=139xxxx]
emotion=neutral

① slot_manager.apply → phone=139xxxx
   生命周期: phone SET → 自动清 lead_refused + lead_refused_at
   → 此时 slots: phone=139xxxx（干净状态）

② all_tasks = ["LEAD_CAPTURE"]

③ LEAD_CAPTURE.can_execute:
   - 情绪 neutral → 通过
   - _is_lead_refused → False（已被生命周期清除）
   - check_slots → phone 存在 → 通过
   → completed

④ LLM 看到 instruction: "用户提供了联系方式，确认并感谢。"
   LLM 有对话历史，知道之前是拒绝、现在是改号 → 自然回复"已更新为139xxxx"
结果: ✓ 生命周期自动清理，LEAD_CAPTURE 确认新号
```

### 场景 4：用户说想改联系方式但没给新号

```
用户: "我想改一下联系方式"
Planner: sub_tasks=["LEAD_CAPTURE"], slot_ops=[DELETE phone]
emotion=neutral

① slot_manager.apply → phone 被删除
   生命周期: 没有 SET phone → 不触发

② all_tasks = ["LEAD_CAPTURE"]

③ LEAD_CAPTURE.can_execute:
   - 情绪 neutral → 通过
   - _is_lead_refused → False
   - check_slots → phone 不存在 → return False
   → get_pending_info → {"missing": ["phone"]}
   → pending

④ LLM 看到: "- LEAD_CAPTURE: 缺少 phone，在回复末尾自然追问"
   → "好的，请问您的新手机号是？"
结果: ✓ LLM 追问新号码
```

### 场景 5：用户闲聊，情绪好（没给过 phone）

```
用户: "今天天气不错"
Planner: sub_tasks=["GREETING"], slot_ops=[]
emotion=positive

① 生命周期: 无 phone 操作 → 不触发

② all_tasks = ["GREETING", "LEAD_CAPTURE"]

③ GREETING → completed

④ LEAD_CAPTURE.can_execute:
   - 情绪 positive → 通过
   - _is_lead_refused → False
   - check_slots → phone 不存在 → return False
   → get_pending_info → {"missing": ["phone"]}
   → pending

⑤ LLM 看到: "- LEAD_CAPTURE: 缺少 phone，在回复末尾自然追问"
结果: ✓ 自然引导留资
```

### 场景 6：用户投诉，情绪差

```
用户: "你们服务太差了"
Planner: sub_tasks=["COMPLAINT"], slot_ops=[], emotion=negative

① 生命周期: 不触发

② LEAD_CAPTURE.can_execute:
   - 情绪 negative → False
   → get_pending_info → {"reason": "情绪 'negative' 不适合执行此任务"}
   → pending

③ LLM 看到: "- LEAD_CAPTURE: 情绪 'negative' 不适合执行此任务"
   注意：COMPLAINT 的 instruction 已经说"不要推销产品或引导留资"
   → LLM 不会追问
结果: ✓ 投诉时不追问留资
```

---

## 改动的文件与行数估算

| 文件 | 变化 | 行数 |
|------|------|------|
| `agents/skills/base.py` | 添加 `auto_evaluate`, `can_execute()`, `get_pending_info()` | +15 |
| `agents/skills/lead_capture.py` | 重写 `can_execute()`, 覆盖 `get_pending_info()`, 加 `auto_evaluate=True` | ~+10 |
| `agents/skills/contact_fix.py` | 删除 | 全文件 |
| `agents/orchestrator.py` | 重构 subtask 循环 + 槽位生命周期规则 | ~+30 |

## 未改动的文件

`complaint.py`, `contact_no.py`, `finance.py`, `greeting.py`, `price.py`, `product.py`, `purchase.py`, `weather.py`, `skill_registry.py`, `slot_manager.py`
