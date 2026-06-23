# EchoMind — 留资型智能客服系统

> **一句话：** 一个面向汽车购车场景的留资型智能客服系统，用户在看车选车过程中提出各种问题，系统实时判断对方是否为潜在客户，在回答中自然引导留资，同时将对话数据沉淀下来持续优化模型。

---

## 目录

- [系统定位](#系统定位)
- [技术栈](#技术栈)
- [架构总览](#架构总览)
  - [三层架构：理解 → 编排 → 生成](#三层架构理解--编排--生成)
  - [为什么拆三层？](#为什么拆三层)
- [Planner（理解层）](#planner理解层)
  - [多标签子任务](#多标签子任务)
  - [槽位提取与 Diff 模式](#槽位提取与-diff-模式)
  - [情绪识别](#情绪识别)
- [编排层（纯代码）](#编排层纯代码)
  - [Slot Manager](#slot-manager)
  - [Orchestrator](#orchestrator)
  - [Skill 可插拔架构](#skill-可插拔架构)
  - [现有 Skill 一览](#现有-skill-一览)
  - [Tool Layer（工具层）](#tool-layer工具层)
- [Response Agent（生成层）](#response-agent生成层)
  - [动态 System Prompt 组装](#动态-system-prompt-组装)
  - [一次生成完整回复](#一次生成完整回复)
- [留资引导与冷却时间窗口](#留资引导与冷却时间窗口)
- [记忆系统（三级架构）](#记忆系统三级架构)
  - [工作记忆（Redis）](#工作记忆redis)
  - [情景记忆（ChromaDB）](#情景记忆chromadb)
  - [用户画像（ChromaDB）](#用户画像chromadb)
- [RAG 检索链路](#rag-检索链路)
  - [文档切割策略](#文档切割策略)
  - [混合检索（向量 + BM25 → RRF）](#混合检索向量--bm25--rrf)
  - [MCP 工具框架](#mcp-工具框架)
  - [文档格式支持与表格处理](#文档格式支持与表格处理)
- [评估体系](#评估体系)
  - [多标签意图 + 槽位提取评估](#多标签意图--槽位提取评估)
  - [LLM-as-Judge 对话质量评估](#llm-as-judge-对话质量评估)
  - [数据闭环](#数据闭环)
- [API 接口](#api-接口)
- [部署架构](#部署架构)
- [技术选型 FAQ](#技术选型-faq)
- [项目演进路线](#项目演进路线)
- [开发者指南](#开发者指南)

---

## 系统定位

EchoMind 是一个面向**汽车购车场景**的留资型智能客服系统。

**核心矛盾：** 用户说一句话可能同时包含多个诉求——"M8 多少钱？对了之前投诉处理了吗？我留个电话 138xxxx"。传统单意图路由（一个 query → 一个 Agent）只能处理其中一个，而多 Agent 顺序执行又会导致回复风格割裂、Token 浪费。

**解法：** 把系统拆成 **理解 → 编排 → 生成** 三层——LLM 只做理解和生成，中间的状态管理、技能编排、工具调度全部由程序确定性完成。

---

## 技术栈

| 维度 | 方案 |
|------|------|
| **语言/框架** | Python 3.12 + FastAPI + Anthropic SDK |
| **LLM** | DeepSeek-Chat（兼容 Anthropic 协议），本地 Ollama（qwen2.5-7b）降级兜底 |
| **向量数据库** | ChromaDB（主力，内置 all-MiniLM-L6-v2）+ Milvus（docker-compose 已部署，预留）|
| **工作记忆** | Redis 7（AOF 持久化）|
| **关键词检索** | Jieba + 纯 Python BM25（手写倒排索引）|
| **监控** | Prometheus |
| **部署** | Docker Compose（6 个服务）|
| **代码规模** | ~2000 行核心 Python 代码，15 个模块文件 |

---

## 架构总览

### 三层架构：理解 → 编排 → 生成

```
用户输入
   │
   ▼
┌─────────────────────────────────────────────────────┐
│ MemoryManager                                         │
│ 拼接历史 + 压缩摘要 + 用户画像（已有，不动）            │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│ ① Planner（LLM）—— 理解层                             │
│  输出：                                                │
│  ├─ primary_intent（主意图，全量）                     │
│  ├─ sub_tasks（子任务列表，全量）                      │
│  ├─ slot_ops（槽位变更，增量 Diff）                    │
│  └─ emotion（情绪）                                   │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│ ② 编排层（纯代码）                                     │
│                                                       │
│  ┌──────────────────┐    ┌────────────────────────┐  │
│  │ Slot Manager     │    │ Orchestrator           │  │
│  │ session.apply()  │    │ ├─ 遍历 sub_tasks      │  │
│  │ session.delete() │    │ ├─ 匹配 Skill 元数据    │  │
│  └──────────────────┘    │ ├─ 检查 required_slots │  │
│                          │ ├─ 收集 required_tools  │  │
│  ┌──────────────────┐    │ │   （set 去重）        │  │
│  │ Tool Layer       │    │ ├─ 检查 emotion 条件   │  │
│  │ RAG / CRM / ...  │◄───┤ └─ 合并 Instruction   │  │
│  └──────────────────┘    └────────────────────────┘  │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│ ③ Response Agent（LLM）—— 生成层                      │
│  输入：                                                │
│  ├─ 【Instruction】所有 Skill 的 instruction 合并     │
│  ├─ 【Knowledge】RAG 返回的 Chunks                    │
│  ├─ 【Slots】Session Slots                            │
│  ├─ 【Emotion】情绪                                   │
│  ├─ 【用户画像/历史】MemoryManager 提供               │
│  职责：一次生成完整回复                                 │
│  输出：回复文本                                        │
└─────────────────────────────────────────────────────┘
```

### 为什么拆三层？

| 层 | 类型 | 职责 | 为什么用 LLM / 代码 |
|---|---|---|---|
| Planner | LLM | 从自然语言中提取结构化信息 | 理解需要语义能力 |
| Orchestrator | 纯代码 | 状态管理、Skill 编排、工具调度 | 确定性逻辑不应交给 LLM |
| Response Agent | LLM | 根据指令和上下文生成流畅回复 | 生成需要文本能力 |

核心原则：
- **LLM 负责理解与生成**（它擅长的）
- **程序负责状态、编排与工具调用**（它擅长的）
- **职责不重叠，不互抢**
- **同一 Tool 只执行一次，结果多 Skill 共享**

---

## Planner（理解层）

> **对应的文件：** `core/intent_recognizer.py`（类 `Planner`）

Planner 的核心任务不是简单分类，而是把用户的一句话拆解成三个结构化输出。

### 多标签子任务（sub_tasks）

用户的 query 同时映射到**多个**子任务标签，每个标签对应一个 Skill：

```
输入: "M8 多少钱，能试驾吗，另外之前投诉处理好了没"
输出 sub_tasks: ["PRICE", "PRODUCT", "COMPLAINT"]
         ↓          ↓          ↓         ↓
         PriceSkill ProductSkill ComplaintSkill  →  三个 Skill 全部触发
```

- sub_tasks 是**全量**输出——每轮完整的子任务列表，不是增量
- 输出格式：`["PRICE", "PRODUCT", "COMPLAINT"]`
- Orchestrator 遍历列表，逐个匹配 Skill

### 槽位提取与 Diff 模式（slot_ops）

Planner 提取关键信息（车型、预算、电话等）时输出的是**增量 Diff**，而非全量状态：

```json
{
  "slot_ops": [
    {"op": "SET",    "slot": "model", "value": "M8"},
    {"op": "SET",    "slot": "budget", "value": "20万"},
    {"op": "DELETE", "slot": "budget"}    // 用户改口说"预算先不管了"
  ]
}
```

**为什么用 Diff？** 多轮后槽位可能积累十几个字段，如果让 LLM 每轮全量输出，它漏掉一个字段你无法判断是"用户要删除"还是"LLM 忘了写"。Diff 模式让 LLM 只说"这轮变化了什么"，状态由程序确定性维护。

输入的 few-shot 示例（14 条覆盖所有意图组合）：

| 示例消息 | sub_tasks | 槽位操作 |
|---------|-----------|---------|
| 你好 | `[GREETING]` | `[]` |
| M8有什么配置？ | `[PRODUCT]` | `[SET model=M8]` |
| 预算20万，M8能分期吗？ | `[PRICE, FINANCE]` | `[SET model=M8, SET budget=20万]` |
| 等了这么久没人理我 | `[COMPLAINT]` | `[SET issue=无人响应]` |
| 不方便留电话 | `[CONTACT_NO]` | `[SET lead_refused=true]` |

### 情绪识别

情绪不作为路由信号，而是由 Orchestrator 代码层判断其影响：

| 情绪 | 对编排的影响 |
|------|-------------|
| positive | LeadCaptureSkill 可执行 |
| neutral | LeadCaptureSkill 可执行 |
| skeptical | LeadCaptureSkill 不执行 |
| anxious | LeadCaptureSkill 不执行 |
| negative | LeadCaptureSkill 不执行，ComplaintSkill 切换安抚模式 |

关键设计：**情绪决策放在程序层而非 LLM 层**——确保行为确定性。

---

## 编排层（纯代码）

### Slot Manager

> **对应的文件：** `agents/slot_manager.py`

**职责：** 维护会话级的槽位状态，执行确定性 SET / DELETE 合并。

```python
sm = SlotManager(redis_client=redis, redis_key="slot:conv1")
sm.apply([SlotOp("SET", "model", "M8")])
sm.get("model")   # → "M8"
sm.all            # → {"model": "M8"}
```

**双后端设计：**
- **Redis Hash 模式**：跨会话持久化，7 天 TTL，多轮对话天然继承
- **内存 dict 降级**：Redis 不可用时退化为内存字典

**关键约束：** Slot Manager 是纯代码，不做任何 LLM 调用。LLM 说怎么改就怎么改，不做推论。

### Orchestrator

> **对应的文件：** `agents/orchestrator.py`（类 `Orchestrator` + `AgentEngine`）

**职责：** 接收 Planner 输出 → 编排 Skill → 收集 Tool → 准备 Response Agent 输入。

完整执行流程：

```
① 应用 slot_ops → 更新 Slot Manager
② 始终追加 LEAD_CAPTURE（留资时机由 Response Agent 判断）
③ 遍历 sub_tasks → 匹配 SkillRegistry
   ── 匹配不到 → 跳过
   ── 匹配到   → 进入下一步
④ 检查 emotion 条件
   ── 情绪差时不触发 LeadCaptureSkill
⑤ 检查 required_slots
   ── 缺失 → 加入 pending（提示 Response Agent 在回复末尾追问）
   ── 满足 → 进入下一步
⑥ 收集可执行 Skill 的 instruction → 合并
⑦ 收集所有 required_tools（set 去重）
⑧ 统一执行 Tool Layer → RAG 只查一次，结果共享
⑨ 构建 ResponseInput → 传给 Response Agent
```

### Skill 可插拔架构

> **对应的文件：** `agents/skills/` 目录 + `agents/skill_registry.py`

Skill 在架构中的角色是**纯元数据（Metadata-only）**，没有 `execute()` 方法。真正的执行者是 Response Agent（LLM）。

```python
class BaseSkill:
    """所有 Skill 的基类 — 纯元数据"""
    
    name: str                        # 标识，与 sub_task 对应
    required_slots: List[str]        # 必需的槽位列表
    optional_slots: List[str] = []   # 可选的槽位列表
    required_tools: List[Tool] = []  # 需要的工具列表
    instruction: str                 # 给 Response Agent 的指令

    @classmethod
    def check_slots(cls, slots) -> List[str]:
        """返回缺失槽位列表（空 = 全部满足）"""

    @classmethod
    def check_emotion(cls, emotion) -> bool:
        """情绪条件检查（默认通过，子类可覆盖）"""
```

**一个 Skill 声明三样东西：**
1. **required_slots** — 我需要什么槽位才能执行
2. **required_tools** — 我需要什么工具（RAG / CRM / ...）
3. **instruction** — 我希望 Response Agent 完成什么任务

Orchestrator 遍历 sub_tasks 时收集这些信息：
- 检查 `required_slots` 是否齐全 → 决定 `completed` / `pending`
- `set` 合并所有 `required_tools` → 统一触发 Tool Layer（去重）
- 把所有 `instruction` 拼接成 System Prompt → 交给 Response Agent

**新增一个 Skill 需要改什么：**
1. 新建 `skills/trade_in.py`，定义元数据（仅类属性）
2. 注册到 `SkillRegistry`
3. Planner Prompt 加一条 few-shot 示例
4. **Orchestrator 和 Response Agent 完全不用改**

### 现有 Skill 一览

| Skill | sub_task | 必需槽位 | 需要 RAG | 情绪限制 | Instruction 摘要 |
|-------|----------|---------|---------|---------|----------------|
| ProductSkill | `PRODUCT` | `model` | ✅ | 无 | 根据 Knowledge 回答产品配置/功能/特点，不编造 |
| PriceSkill | `PRICE` | `model` | ✅ | 无 | 回答价格/优惠/金融方案，无明确信息时提供参考区间 |
| FinanceSkill | `FINANCE` | `model`, `budget` | ✅ | 无 | 提供分期方案建议，结合预算给出首付/月供参考 |
| ComplaintSkill | `COMPLAINT` | 无（`issue` 非强制） | 可选 | 无 | 先安抚，不推销，说明会反馈给相关团队 |
| LeadCaptureSkill | `LEAD_CAPTURE` | `phone` | ❌ | 情绪差时不可执行 | 确认联系方式，告知稍后顾问联系；情绪好时可引导 |
| GreetingSkill | `GREETING` | 无 | ❌ | 无 | 友好回应，主动询问需求 |
| PurchaseSkill | `PURCHASE` | 无（`model` 非必需） | ✅ | 无 | 确认购买意向，推荐车型，配合 LeadCapture |
| ContactNoSkill | `CONTACT_NO` | 无 | ❌ | 无 | 尊重用户选择，绝不追问，自然转向其他话题 |

#### 特殊设计：`can_execute()` 的三层守门

以 `LeadCaptureSkill` 为例，它的执行逻辑有特殊三层判定：

```python
@classmethod
def can_execute(cls, slots, emotion) -> bool:
    # 第一层：情绪检查
    if not cls.check_emotion(emotion):
        return False
    # 第二层：用户主动给了 phone → 直接执行（确认感谢）
    if slots.get("phone"):
        return True
    # 第三层：没 phone 时检查冷却期 + 拒绝标记
    if cls._is_lead_refused(slots):
        return False
    return True
```

### Tool Layer（工具层）

> **对应的文件：** `agents/tool_layer.py`

**职责：** 为 Response Agent 提供执行所需的资源。由 Orchestrator 统一调度，多个 Skill 共享同一次执行结果。

```python
class ToolLayer:
    async def exec_tools(self, required_tools, user_query, sub_tasks, slots) -> Dict:
        # RAG 只执行一次
        if Tool.RAG in required_tools:
            results["rag"] = await self.exec_rag(...)
        # 预留：CRM / CALCULATOR
        return results
```

**Query Rewrite（规则式）：** 利用 Planner 提取的 slot 增强检索查询：

```python
# 用户说"多少钱" → Planner 已提取 model=M8
# Rewrite → "M8 多少钱" → 召回质量显著提升
```

当前为规则式（不调用 LLM），低延迟、零额外成本。

---

## Response Agent（生成层）

> **对应的文件：** `agents/orchestrator.py`（类 `ResponseAgent`）

**职责：** 接收 Instruction + Context，一次生成完整回复。**唯一的职责是生成自然语言回复**——不做意图判断，不做工具调用。

### 动态 System Prompt 组装

Response Agent 的 System Prompt 由多个 Skill 的 instruction 动态合并：

```
你是 EchoMind 智能客服助手。以下是你要完成的任务：

【任务】
1. 根据 Knowledge 回答车辆价格问题...
2. 先安抚用户情绪，说明会反馈给相关团队...
3. 用户提供了联系方式，确认并感谢...

【待办】
- PURCHASE: 缺少 model，在回复末尾自然追问车型偏好

回复规范：
1. 语气专业亲和
2. 先安抚后回复
3. 不编造知识...
```

### 一次生成完整回复

User Message 里的上下文：

```
用户说: 我想买 M8，预算 20 万

【知识】
[1] M8 官方指导价 19.98-22.98 万元...
[2] M8 搭载 2.0T 发动机...

【信息】
{"model": "M8", "budget": "20万"}

【情绪】
positive

【背景】
[会话摘要] 用户首次咨询...
[用户画像] {"preferences": [...], "entities": {...}}
```

**优势：** 回复风格天然一致、知识引用统一、不存在多 Agent 拼接时的语气冲突。

---

## 留资引导与冷却时间窗口

> **对应的文件：** `agents/lead_store.py` + `agents/skills/lead_capture.py` + `agents/orchestrator.py`

留资时机的判断嵌入到 Skill 机制 + Redis 冷却中：

### 为什么 LEAD_CAPTURE 永远追加？

Orchestrator 在编排时**始终追加** `LEAD_CAPTURE` 到 sub_tasks 列表中——不依赖 Planner 是否识别出。因为留资时机是在对话中动态判断的：用户问完价格、情绪不错时自然引导，此时 Planner 可能只输出 PRICE，没有 LEAD_CAPTURE。

但**追加不等于执行**——由 LeadCaptureSkill 自己守门。

### 三层守门 + Redis 冷却时间窗口

```
① 情绪检查：negative / skeptical → 不发起留资
② 槽位检查：已有 phone → 不再要（感谢确认即可）
③ 冷却检查：24h 内拒绝过 → 不发起
     │
     ▼
Redis: lead:user_id:refused（TTL=86400s）
Redis 天然跨会话，TTL 过期自动解除冷却
```

### 拒绝留资流程

```
用户说"不方便留电话"
  → Planner 识别出 CONTACT_NO
  → ContactNoSkill 给 instruction：尊重用户，绝不追问
  → AgentEngine 检测到 CONTACT_NO 完成
  → LeadStore.record_refusal(user_id) → Redis SETEX 24h
  → 下次同用户对话，is_in_cooldown() → True，LeadCaptureSkill 自动绕过
```

---

## 记忆系统（三级架构）

> **对应的文件：** `memory/conversation_memory.py`

三级记忆架构，模拟人类记忆机制：

```
┌────────────────────────────────────────────────────────────────┐
│                        三级记忆架构                              │
│                                                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ① 工作记忆 (Redis)                                     │   │
│  │  存储：最近 20 条消息，TTL 24h                           │   │
│  │  特点：毫秒级读写，支持自动压缩（满 15 条触发）             │   │
│  │  操作：lpush + lrange, 最新在前                          │   │
│  │  压缩：LLM 摘要旧消息 → 存入情景记忆 → 保留最近 5 条      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                               │                                 │
│                               ▼                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ② 情景记忆 (ChromaDB "episodic")                       │   │
│  │  存储：压缩后的历史对话片段，语义检索                      │   │
│  │  特点：跨会话检索，按语义相似度匹配                        │   │
│  │  查询：query_texts + where(user_id)                     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                               │                                 │
│                               ▼                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ③ 用户画像 (ChromaDB "user_profile")                   │   │
│  │  存储：LLM 提炼的用户偏好 + 关键实体                      │   │
│  │  更新：异步（asyncio.create_task），不阻塞主链路            │   │
│  │  格式：{"preferences": [...], "entities": {...}}          │   │
│  └─────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

### 工作记忆（Redis）

- **存储：** 每次 `add_message()` 将 `{role, content, ts, metadata}` lpush 到 Redis 列表
- **过期：** 24h TTL（Redis key 级自动过期，不需要手动清理）
- **压缩：** 列表长度达到 15 条时触发 `_compress()`：
  1. LLM 对旧消息生成 2-3 句摘要
  2. 摘要存 Redis（累积追加 `summary:{user_id}:{conv_id}`）
  3. 旧消息原文存入 ChromaDB episodic（语义检索，跨会话）
  4. 工作记忆只保留最近 5 条

### 情景记忆（ChromaDB）

- 每次压缩时写入，每条是一条独立记录（不做合并）
- 用当前 query 做语义搜索，召回之前聊过的相关内容
- 查询时加 `where={"user_id": user_id}` 过滤

### 用户画像（ChromaDB）

- 每次对话末尾异步更新：`asyncio.create_task(_memory.update_profile(...))`
- LLM 从当前对话中提炼 `preferences` + `entities` + `sentiment_history`
- 覆盖更新（先 delete 旧 doc，再 add 新 doc）
- **不阻塞主响应链路**

### 上下文融合（`to_prompt_text()`）

```
[会话摘要]         ← Redis summary key
用户首次咨询 M8，关注价格...

[相关历史]         ← ChromaDB episodic 语义检索
- 上次也问过 M8 的配置...

[用户画像]         ← ChromaDB user_profile
{"preferences": ["高性价比"], "entities": {"产品": ["M8"]}}

[最近对话]         ← 最近 N 条原始消息
user: M8 多少钱
assistant: 指导价 19.98 万起
```

---

## RAG 检索链路

> **对应的文件：** `mcp/knowledge_base.py` + `mcp/tool_manager.py`

完整的 RAG 链路：

```
用户查询 → 寒暄检测（跳过寒暄）
         → Query Rewrite（slots + sub_tasks → 增强 query）
         → 多角度子查询（LLM 改写为 3 个子查询并行召回）
         → 混合检索（向量 + BM25 并行执行，RRF 融合）
         → LLM 重排（对 Top-N 按相关性二次排序）
         → 返回 Top-K chunks
```

### 文档切割策略

**三阶段切割（手写 80 行代码，无 LangChain 依赖）：**

```
Phase 1：递归切分
  从粗到细逐级尝试分隔符：
  段落(\n\n) → 行(\n) → 。 → ！ → ？ → . → ! → ? → 空格 → 字符硬切
  每级切完看碎片大小，超过 500 字符降级到下一级继续

Phase 2：贪心凑块
  遍历碎片列表，尽可能把相邻碎片合并到 ~500 字符但不超出

Phase 3：Overlap
  相邻 chunk 之间重叠 50 字符，防止边界信息丢失
```

**chunk_size=500 的权衡：**
- 500 字符 ≈ 200-300 中文词，对于一个"知识点"刚刚好
- 比一句话丰富、比完整文档聚焦
- LLM 重排时能完整理解又不丢失重点

### 混合检索（向量 + BM25 → RRF）

**为什么混合检索？** 纯向量检索的问题：语义相似 ≠ 精准匹配。用户问"退款流程"，向量可能召回"退货政策""售后说明"，但"退款"这个精确关键词权重反而不高。

**BM25 关键词检索（纯 Python 手写）：**
- 分词：Jieba + 领域词典（注册了 '401错误'、'无理由退款'、'1-3个工作日' 等客服高频词）
- 参数：k1=1.5（词频饱和度），b=0.75（文档长度归一化）
- 服务端无依赖，纯 Python 实现，~100 行代码

**RRF 融合（Reciprocal Rank Fusion）：**
```python
RRF_score(d) = Σ 1 / (K + rank_r(d))
```
- K=60（默认值，BM25 论文在 TREC 数据集上的经验值）
- 不需要归一化向量分和 BM25 分——只依赖排名，天然适合异源结果合并

**LLM 重排（上层）：** RRF 融合后取 Top-N，LLM 对这些候选按相关性二次打分排序。

### MCP 工具框架

> **对应的文件：** `mcp/tool_manager.py`

工具调用可靠性链路：

```
用户调用工具
  → ① 缓存检查（TTL 缓存，相同参数直接返回）
  → ② 熔断检查（CLOSED → OPEN → HALF_OPEN 三态）
  → ③ 参数校验（JSON Schema）
  → ④ 执行 + 超时（asyncio.wait_for）
  → ⑤ 缓存写入
  → ⑥ 可选 LLM 重排
```

**查询改写功能**（`search_with_rewrite`）：

解决"召回不全"问题的重要方法。用户 query 用 LLM 扩写成 3 个不同角度的子查询，并行召回后合并去重，再用 LLM 重排取 Top-K。

### 文档格式支持与表格处理

| 格式 | 解析引擎 | 处理方式 |
|------|---------|---------|
| PDF | pdfplumber | 按页提取：文本 + 表格分别处理，超大表格整体入库不做切分 |
| Word (docx) | python-docx | 保持段落 + 表格原始顺序遍历合并 |
| JSON | 标准 json | 批量导入 `[{title, content}]` |
| TXT/MD | 标准文本 | 全文导入 |

**表格特殊处理：** PDF 和 Word 中的表格统一转为 Markdown 格式保留二维结构，LLM 能理解行列对应关系。大表格整体作为一个 chunk 不入切分（防止表头丢失）。

---

## 评估体系

### 多标签意图 + 槽位提取评估

> **对应的文件：** `evaluation/multi_intent_evaluator.py`

传统单标签 Accuracy 在客服场景中不适用——一个 query 可能同时命中 PRICE、PRODUCT、COMPLAINT 三个标签，用"猜对主要意图"会漏掉次要意图。

**评估维度：**

| 维度 | 指标 | 说明 |
|------|------|------|
| Sub-task 分类 | **Macro Precision / Recall / F1** | 每个 sub_task 独立算 TP/FP/FN，再取算术平均 |
| Sub-task 集合 | **Exact Match Rate** | sub_task 集合完全一致的用例占比 |
| Slot 提取 | **Slot Macro F1** | 每个 slot name 独立算，比较"槽位名 + 值是否完全匹配" |
| Slot 精确度 | **Slot Exact Match Rate** | 所有槽位值完全正确的用例占比 |

**Slots 评估黑名单：** `lead_refused` 排除在评估外——它是系统推断槽位（用户不会直接说"lead_refused"这个词），只看 CONTACT_NO 这个 sub_task 的识别准不准就够了。

**数据格式（JSONL）：**
```json
{"query": "M8多少钱，能分期吗",
 "sub_tasks": ["PRICE", "FINANCE"],
 "slots": {"model": "M8"}}
```

**144 条 Golden Test Set** 覆盖常见多意图组合（PRICE+PRODUCT、PRICE+COMPLAINT、GREETING+LEAD_CAPTURE 等）。

### LLM-as-Judge 对话质量评估

> **对应的文件：** `evaluation/evaluator.py`

**四维评分（0.0-1.0）：**

| 维度 | 问题 | 低分原因 |
|------|------|---------|
| relevance | 是否针对用户问题 | Agent 跑偏 |
| accuracy | 信息是否准确 | 幻觉/编造 |
| completeness | 是否完整解决需求 | 回答太短/遗漏 |
| helpfulness | 用户能否据此行动 | 太抽象 |

**Regresion 检测：** 每次评测结果与基线对比，退化 > 5% 自动标记。

### 数据闭环

线上每轮 Planner 输出（query + primary_intent + sub_tasks + emotion + slots）持久化到 `data/intent_logs.jsonl`：

```
处理请求 → 记录(Planner输出) → 抽 Bad Case → 人工修正 → 补充到测试集 → 模型微调
```

---

## API 接口

| 路由 | 方法 | 功能 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/chat` | POST | 主对话接口（完整链路：记忆 → Planner → Orchestrator → Tool Layer → Response Agent → 记忆写入） |
| `/search` | POST | RAG 检索演示（查询改写 + 并行召回 + 重排） |
| `/knowledge/add` | POST | 批量导入知识库文档 |
| `/knowledge/upload` | POST | 文件上传导入（PDF/DOCX/TXT/MD/JSON） |
| `/knowledge/stats` | GET | 知识库统计 |
| `/knowledge/list` | GET | 知识库片段列表 |
| `/knowledge/{doc_id}` | DELETE | 按文档 ID 删除 |
| `/knowledge` | DELETE | 清空知识库 |
| `/memory/profiles` | GET | 用户画像列表 |
| `/memory/episodic` | GET | 情景记忆列表 |
| `/eval/run` | POST | 运行旧架构端到端评测 |
| `/eval/multi` | POST | 运行多意图评测（sub_task + slot） |
| `/monitor` | GET | 监控摘要 |
| `/metrics` | GET | Prometheus 指标 |

---

## 部署架构

```
                      ┌─────────────┐
                      │   Nginx     │  :8084 → :8000
                      │   反向代理    │  限流 10r/s
                      └──────┬──────┘
                             │
                      ┌──────▼──────┐
                      │  EchoMind   │  FastAPI 服务
                      │  (主应用)    │  Core Python
                      └──┬───┬───┬──┘
                         │   │   │
              ┌──────────┘   │   └──────────┐
              ▼              ▼              ▼
        ┌─────────┐   ┌──────────┐   ┌──────────────┐
        │  Redis  │   │ ChromaDB │   │  Prometheus  │
        │ 工作记忆│   │向量数据库 │   │   指标采集    │
        │ (TTL 24h)│   │情景+画像  │   │   :9091      │
        └─────────┘   └──────────┘   └──────────────┘
```

**Docker Compose 服务（6 个容器）：**

| 服务 | 说明 |
|------|------|
| `redis` | Redis 7，AOF 持久化，Password 认证 |
| `chromadb` | 向量数据库（docker-compose.dev.yml 中独立） |
| `prometheus` | 指标采集与告警 |
| `echomind` | 主应用，FastAPI + Uvicorn |
| `nginx` | 反向代理 + 限流（10r/s）+ Gzip |
| `milvus` | 未来向量数据库（已部署，待迁移） |

**生产环境注意事项：**
- Nginx 反向代理 + 限流 10r/s（nginx.conf 中配置）
- Docker restart: unless-stopped 自动恢复
- Nginx + ChromaDB + Redis 健康检查链确保启动顺序
- 多阶段构建 Dockerfile（3.5GB → ~500MB）
- Milvus 整套（etcd + MinIO + Milvus + Attu）已部署在 docker-compose.yml 中，数据量上去后切换

---

## 技术选型 FAQ

### 为什么用 LLM 而非 BERT 做意图识别？

Planner 需要同时输出多标签 sub_tasks + slot_ops 序列 + emotion——这不是分类问题，而是**结构化生成**问题。BERT 输出的是类别概率分布，处理不了多标签组合 + 槽位提取。LLM 零样本能力让项目冷启动就能跑起来，同时通过 intent_logs.jsonl 积累数据，够量后蒸馏到小模型。

### 为什么选 ChromaDB 而非 Milvus？

项目早期没有 DBA 资源，ChromaDB 嵌入式运行、无服务依赖、SQLite 持久化，对于几千到几万个片段完全够用。docker-compose.yml 里已经部署了 Milvus（etcd + MinIO + Milvus + Attu），数据量上去了无缝切换。

### 为什么选 all-MiniLM-L6-v2 而非 BGE？

ChromaDB 内置模型不可替换。换 BGE 需要外部算向量再传 `embeddings` 参数，或切到 Milvus。在召回效果出现明显瓶颈之前不值得为了模型替换去动基础设施。

### 为什么不用 LangChain / LangGraph？

抽象太厚调试成本高、80% 场景用不到框架复杂度、依赖重 API 变动频繁。核心流程就三步（Planner → Orchestrator → Response Agent），手写灵活可控。如果后续需要多步 Agent 循环，LangGraph 是合理的选择。

### 为什么不用 Elasticsearch？

几千个 chunk 专门跑一个 ES 集群太重。纯 Python BM25 + jieba 能做到一样的事，代码不到 200 行。

---

## 项目演进路线

### Phase 1：定义与验证（设计冻结）
- [x] Skill 定义为纯元数据（无 execute）
- [x] Planner 输出格式（全量 sub_tasks + Diff slot_ops）
- [x] Slot Diff 合并策略（SET / DELETE）
- [x] Orchestrator 编排逻辑
- [x] Tool Layer 设计（RAG 统一执行）
- [x] Response Agent 输入结构
- [x] KBSearchSkill 删除，升级为 Tool

### Phase 2：核心数据流
- [x] `slot_manager.py` — Session Slot Store
- [x] `skill_registry.py` — Skill 注册
- [x] `skills/base.py` — BaseSkill 元数据基类
- [x] 6 个初始 Skill 定义
- [x] `tool_layer.py` — RAG 统一调度
- [x] Planner 新 Prompt
- [x] `orchestrator.py` — 完整串联

### Phase 3：Agent 改造
- [x] AgentEngine 统一入口
- [x] Planner → Slot Manager → Orchestrator → Tool Layer → Response Agent 完整链路
- [x] API 重写为新架构
- [ ] 多轮对话测试（槽位继承、改口、删除场景）

### Phase 4：评估与收敛
- [x] 多意图评估器（sub_task + slot 双维度）
- [ ] Golden Test Set 回归测试
- [ ] 新旧架构对比评估

### 下一步方向
- **知识图谱**：填充真实产品数据后，可引入 Graph RAG 处理跨实体多跳查询
- **小模型蒸馏**：积累足够 intent_logs.jsonl 数据后蒸馏本地模型降本
- **多副本部署**：单进程 → 多 uvicorn worker + Nginx 负载均衡
- **异步 Redis 客户端**：`redis.asyncio.Redis` 替代同步客户端
- **请求队列**：LLM API 限流保护，排队超时自动切 Ollama 降级

---

## 开发者指南

### 本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动依赖服务（Redis + ChromaDB）
docker compose -f docker-compose.dev.yml up -d

# 3. 启动应用
python api/main.py         # API 服务
python api/main.py --cli   # CLI 交互模式
```

### 新增一个 Skill

```python
# agents/skills/trade_in.py
from .base import BaseSkill, Tool

class TradeInSkill(BaseSkill):
    name = "TRADE_IN"
    required_slots = ["old_model", "old_year"]
    required_tools = [Tool.RAG]
    instruction = "根据 Knowledge 评估置换价格，给出参考方案"
```

1. 创建 `skills/trade_in.py`
2. 在 `SkillRegistry.init_defaults()` 中注册
3. 在 Planner 的 `_FEWSHOT` 中加一条 few-shot 示例
4. 完成。Orchestrator 和 Response Agent 不需要改一行代码。

### 运行评估

```bash
# 多意图评估（128 条 Golden Test Set）
curl -X POST http://localhost:8000/eval/multi

# 自定义测试用例
curl -X POST http://localhost:8000/eval/multi \
  -H "Content-Type: application/json" \
  -d '{"cases": [{"query": "M8多少钱", "sub_tasks": ["PRICE"], "slots": {"model": "M8"}}]}'

# 端到端评估（旧架构，行为对比）
curl -X POST http://localhost:8000/eval/run
```

### 导入知识库文档

```bash
# JSON 批量导入
curl -X POST http://localhost:8000/knowledge/add \
  -H "Content-Type: application/json" \
  -d '{"documents": [{"title": "M8 价格表", "content": "M8 指导价 19.98-22.98 万元..."}]}'

# 文件上传（PDF/DOCX/TXT/MD）
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@./product_catalog.pdf"
```

---

*EchoMind v2.0 — 架构设计文档，2026-06*
