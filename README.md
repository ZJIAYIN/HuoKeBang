# EchoMind — 企业级留资型智能客服系统

<p align="center">
  <strong>面向汽车购车场景的 LLM 智能客服 · 理解 → 编排 → 生成 三层架构</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-blue?logo=python" alt="Python 3.12"/>
  <img src="https://img.shields.io/badge/FastAPI-0.115-teal?logo=fastapi" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/Claude%20API-Compatible-8A2BE2" alt="Claude API"/>
  <img src="https://img.shields.io/badge/DeepSeek-Supported-4F46E5" alt="DeepSeek Support"/>
  <img src="https://img.shields.io/badge/Redis-7-A41E11?logo=redis" alt="Redis 7"/>
  <img src="https://img.shields.io/badge/ChromaDB-✓-yellow" alt="ChromaDB"/>
  <img src="https://img.shields.io/badge/RabbitMQ-4.0-FF6600?logo=rabbitmq" alt="RabbitMQ"/>
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker" alt="Docker Compose"/>
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"/>
</p>

---

## 📋 目录

- [项目简介](#项目简介)
- [核心亮点](#核心亮点)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [核心模块详解](#核心模块详解)
  - [Planner — 理解层](#planner--理解层)
  - [编排层 — 纯代码](#编排层--纯代码)
  - [Response Agent — 生成层](#response-agent--生成层)
  - [Skill 可插拔架构](#skill-可插拔架构)
  - [三级记忆系统](#三级记忆系统)
  - [RAG 检索链路](#rag-检索链路)
  - [体验券与留资转化](#体验券与留资转化)
  - [对话式体验券系统](#对话式体验券系统)
  - [评估体系](#评估体系)
  - [安全防护](#安全防护)
- [API 接口一览](#api-接口一览)
- [部署指南](#部署指南)
  - [前置条件](#前置条件)
  - [快速部署（Docker Compose）](#快速部署docker-compose)
  - [本地开发模式](#本地开发模式)
  - [配置说明](#配置说明)
  - [生产环境注意事项](#生产环境注意事项)
- [开发者指南](#开发者指南)
  - [项目结构](#项目结构)
  - [新增一个 Skill](#新增一个-skill)
  - [运行评估](#运行评估)
  - [导入知识库](#导入知识库)
- [项目演进路线](#项目演进路线)
- [FAQ](#faq)

---

## 项目简介

EchoMind 是一个面向**汽车购车场景**的企业级留资型智能客服系统。用户在看车选车过程中提出各种问题，系统实时判断对方是否为潜在客户，在回答中**自然引导留资**，同时将对话数据沉淀下来持续优化。

**核心矛盾：** 用户的自然语言往往包含多个诉求——"M8 多少钱？之前投诉处理了吗？我留个电话 138xxxx"。传统单意图路由只能处理其中一个，而多 Agent 顺序执行又会导致回复风格割裂、Token 浪费。

**核心解法：** 将系统拆分为 **理解 → 编排 → 生成** 三层——LLM 只负责**语义理解**和**文本生成**，中间的状态管理、技能编排、工具调度全部由**程序确定性完成**，保证行为一致、风格统一、零额外 Token 开销。

**项目规模：** ~8,700 行核心 Python 代码，20+ 模块文件，7 个 Docker 微服务，覆盖从意图识别、RAG 检索、多技能编排到留资引导、体验券发放、数据闭环评估的完整业务链路。

---

## 核心亮点

### 🎯 面向真实业务场景

- **专为汽车购车场景设计**：理解车型、价格、金融方案、投诉、试驾、留资等领域的复杂对话
- **留资转化是核心 KPI**：系统不是为了聊天而聊天，而是通过情感分析、时机判断，在用户最有意向的时刻自然引导留资
- **体验券促转化**：内置完整的体验券发放、领取、超时释放闭环，用权益激励留资转化

### 🏗 架构设计深度

- **理解 → 编排 → 生成 三层架构**：LLM 做语义理解和文本生成，纯代码做状态管理和技能编排，职责不重叠
- **Skill 可插拔架构**：新增业务能力只需定义元数据、注册、加 few-shot 示例 —— Orchestrator 和 Response Agent 零改动
- **Slot Diff 模式**：LLM 只输出增量槽位变更，状态由程序确定性维护，避免全量输出导致的丢失问题
- **三级记忆系统**：工作记忆（Redis）→ 情景记忆（ChromaDB）→ 用户画像（ChromaDB），模拟人类记忆机制

### 🚀 RAG 检索深度优化

- **查询改写**：利用 LLM 将用户 query 扩写为 3 个不同角度的子查询，并行召回，覆盖语义盲区
- **混合检索**：手写纯 Python BM25（Jieba 分词 + 领域词典）+ 向量检索并行执行，RRF 融合排序
- **LLM 重排**：对 Top-N 候选片段按相关性二次打分，保证注入上下文的都是高质量内容

### 📊 数据驱动与评估体系

- **多意图评估**：Macro Precision / Recall / F1 + Exact Match Rate，144 条 Golden Test Set 覆盖常见多意图组合
- **LLM-as-Judge 四维评分**：relevance / accuracy / completeness / helpfulness，自动 Regression 检测
- **数据闭环**：在线 Planner 五元组日志 → Bad Case 分析 → 人工修正 → 补充测试集 → 模型微调

### 🔧 生产级工程质量

- **原子化库存管理**：Redis Lua 脚本保证库存扣减 + 幂等检查原子性，无并发安全问题
- **可靠消息投递**：本地消息表 + 定时扫描 → RabbitMQ，由 TTL + DLX 死信队列驱动超时释放，Lua 补偿回滚
- **优先级限流**：优先丢弃低价值长尾请求，保障付费用户请求的响应能力
- **多模型兼容**：支持 Anthropic Claude / DeepSeek-Chat / Ollama 本地模型，API 级兼容，自动降级

---

## 技术栈

| 维度 | 方案 |
|------|------|
| **语言/框架** | Python 3.12 + FastAPI + Uvicorn |
| **LLM** | Anthropic Claude API / DeepSeek-Chat（兼容协议）/ Ollama 本地降级 |
| **向量数据库** | ChromaDB（主力，嵌入 all-MiniLM-L6-v2），Milvus 已部署待迁移 |
| **嵌入模型** | BAAI/bge-small-zh-v1.5（fastembed ONNX 轻量，384 维） |
| **工作记忆** | Redis 7（AOF 持久化，Password 认证）|
| **消息队列** | RabbitMQ 4.0（TTL + DLX 死信队列）|
| **业务数据库** | MySQL 8.0（体验券持久化 + Outbox 模式）|
| **关键词检索** | Jieba 分词 + 手写 BM25 倒排索引（纯 Python，~100 行）|
| **监控** | Prometheus 指标采集 + Nginx 反向代理 |
| **部署** | Docker Compose（7 个服务），多阶段构建（~500MB）|
| **嵌入式模型** | BAAI/bge-base-zh-v1.5（Docker 构建时预下载，避免运行时超时）|

---

## 系统架构

### 三层架构总览

```
                         ┌──────────────┐
                         │  用户输入     │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │ MemoryManager │
                         │ 拼接历史 +    │
                         │ 压缩摘要 +    │
                         │ 用户画像      │
                         └──────┬───────┘
                                │
                   ┌────────────▼────────────┐
                   │ ① Planner（LLM）—— 理解层 │
                   │                         │
                   │ ┌─────────────────────┐ │
                   │ │ Few-shot Prompt     │ │
                   │ │ → 14 条示例覆盖所有  │ │
                   │ │   意图组合          │ │
                   │ ├─────────────────────┤ │
                   │ │ 输出：              │ │
                   │ │ ├─ primary_intent   │ │
                   │ │ ├─ sub_tasks[ ]     │ │
                   │ │ ├─ slot_ops[ ]（Diff）│ │
                   │ │ └─ emotion          │ │
                   │ └─────────────────────┘ │
                   └────────────┬────────────┘
                                │
                   ┌────────────▼────────────┐
                   │ ② 编排层（纯代码）        │
                   │                         │
                   │ ┌──────┐ ┌────────────┐ │
                   │ │Slot  │ │Orchestrator│ │
                   │ │Mgr   │ │ 遍历sub_task│ │
                   │ │SET/  │ │ → 匹配Skill│ │
                   │ │DELETE│ │ → 检查情绪 │ │
                   │ └──────┘ │ → 检查槽位 │ │
                   │          │ → 收集Tool │ │
                   │ ┌──────┐ │ → 合并Ins- │ │
                   │ │Tool  │ │   truction │ │
                   │ │Layer │◄└────────────┘ │
                   │ │RAG/  │                │
                   │ │CRM/  │                │
                   │ │...   │                │
                   │ └──────┘                │
                   └────────────┬────────────┘
                                │
                   ┌────────────▼────────────┐
                   │ ③ Response Agent(LLM)——生成│
                   │                         │
                   │ 输入：                   │
                   │ ├─ Instruction(合并)     │
                   │ ├─ Knowledge(Chunks)    │
                   │ ├─ Slots(当前状态)       │
                   │ ├─ Emotion(情绪)        │
                   │ ├─ Memory(历史/画像)     │
                   │                         │
                   │ 职责：一次生成完整回复     │
                   │ 输出：回复文本 + 元数据    │
                   └─────────────────────────┘
```

### Docker 微服务架构

```
                         ┌──────────────┐
                         │   Nginx      │
                         │   :8084 → :8000│
                         │   反向代理    │
                         │   限流 10r/s │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │  EchoMind    │
                         │  FastAPI 服务  │
                         │  核心业务逻辑  │
                         └──┬───┬───┬───┘
                            │   │   │
              ┌─────────────┘   │   └──────────────┐
              ▼                 ▼                  ▼
       ┌─────────────┐  ┌──────────────┐  ┌──────────────┐
       │   Redis 7   │  │   ChromaDB  │  │   MySQL 8.0  │
       │  工作记忆    │  │  向量数据库   │  │  业务持久化   │
       │  库存/券     │  │  知识库/记忆  │  │  Outbox 表   │
       └─────────────┘  └──────────────┘  └──────┬───────┘
                                                  │
                                          ┌───────▼───────┐
                                          │   RabbitMQ    │
                                          │   消息队列     │
                                          │  TTL + DLX    │
                                          └───────┬───────┘
                                                  │
                                          ┌───────▼───────┐
                                          │ CouponWorker  │
                                          │ 超时消费/释放  │
                                          └───────────────┘

       ┌──────────────┐
       │  Prometheus  │
       │  指标采集     │
       └──────────────┘
```

---

## 核心模块详解

### Planner — 理解层

**对应文件：** `core/intent_recognizer.py`

Planner 的核心任务不是简单分类，而是把用户的一句话拆解成三个结构化输出：

**1. 多标签子任务（sub_tasks）**

用户的 query 可同时映射到多个子任务标签，每个标签对应一个 Skill：

```
输入: "M8 多少钱，能试驾吗，之前投诉处理好了没"
输出: ["PRICE", "PRODUCT", "COMPLAINT"]
         ↓         ↓            ↓
     PriceSkill  ProductSkill  ComplaintSkill  → 三个 Skill 全部触发
```

**2. Slot Diff 模式（slot_ops）**

Planner 提取关键信息时输出的是**增量 Diff**，而非全量状态：

```json
{
  "slot_ops": [
    {"op": "SET", "slot": "model", "value": "M8"},
    {"op": "SET", "slot": "budget", "value": "20万"},
    {"op": "DELETE", "slot": "budget"}
  ]
}
```

**为什么用 Diff？** 多轮后槽位可能积累十几个字段，让 LLM 每轮全量输出，漏掉一个字段无法判断是"用户要删除"还是"LLM 忘了"。Diff 模式让 LLM 只说"这轮变化了什么"，状态由程序确定性维护。

Few-shot 示例（14 条覆盖所有意图组合）：

| 示例消息 | sub_tasks | 槽位操作 |
|---------|-----------|---------|
| 你好 | `[GREETING]` | `[]` |
| M8有什么配置？ | `[PRODUCT]` | `[SET model=M8]` |
| 预算20万，M8能分期吗？ | `[PRICE, FINANCE]` | `[SET model=M8, SET budget=20万]` |
| 等了这么久没人理我 | `[COMPLAINT]` | `[SET issue=无人响应]` |

**3. 情绪识别**

情绪不作为路由信号，而是由编排层代码层判断其影响：

| 情绪 | 对编排的影响 |
|------|-------------|
| positive | CouponDecider 可触发体验券发放 |
| neutral | CouponDecider 可触发体验券发放 |
| skeptical | CouponDecider 不触发体验券发放 |
| anxious | CouponDecider 不触发体验券发放 |
| negative | CouponDecider 不触发发券，ComplaintSkill 切换安抚模式 |

关键设计：**情绪决策放在程序层而非 LLM 层**——确保行为确定性。

---

### 编排层 — 纯代码

编排层全部由纯代码实现，涵盖四个核心组件：

#### Slot Manager（`agents/slot_manager.py`）

会话级槽位状态管理，执行确定性 SET / DELETE 合并：

```python
sm = SlotManager(redis_client=redis, redis_key="slot:conv1")
sm.apply([SlotOp("SET", "model", "M8")])
sm.get("model")    # → "M8"
```

**双后端设计：** Redis Hash 模式（跨会话持久化，7 天 TTL） + 内存 dict 降级（Redis 不可用时自动退化）。

#### Orchestrator（`agents/orchestrator.py`）

编排引擎，接收 Planner 输出并驱动全流程：

```
① 应用 slot_ops → 更新 Slot Manager
② 构建任务列表：sub_tasks + auto_evaluate 技能自动追加
③ 遍历 sub_tasks → 匹配 SkillRegistry（灰度路由）
④ 检查 emotion 条件 + required_slots（check_skill）
⑤ 收集可执行 Skill 的 instruction → 合并
⑥ 收集所有 required_tools（set 去重，同一 Tool 只执行一次）
⑦ 统一执行 Tool Layer
⑧ CouponDecider 判断是否发体验券
⑨ 构建 ResponseInput → 传给 Response Agent
```

#### Tool Layer（`agents/tool_layer.py`）

统一工具调度层，为 Response Agent 提供执行资源：

```python
class ToolLayer:
    async def exec_tools(self, required_tools, user_query, sub_tasks, slots):
        # RAG 只执行一次，结果多 Skill 共享
        if Tool.RAG in required_tools:
            results["rag"] = await self.exec_rag(...)
```

**Query Rewrite（规则式）：** 利用 Planner 提取的 slot 增强检索查询，如用户说"多少钱" + `model=M8` → 改写为 "M8 多少钱"，召回质量显著提升。零 LLM 调用，零延迟。

#### 优先级限流（`core/rate_limiter.py` + `core/work_queue.py`）

```
高优先级（付费用户）→ FastAPI 路由直接处理
低优先级（免费/匿名）→ WorkQueue 排队，限并发 5
    ↓ 队列满
丢弃最旧的低优先级请求（保新不保旧）
```

---

### Response Agent — 生成层

**对应文件：** `agents/orchestrator.py`（类 `ResponseAgent`）

**唯一的职责是生成自然语言回复**——不做意图判断，不做工具调用。

#### 动态 System Prompt 组装

由多个 Skill 的 instruction 合并而成：

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

#### 单次生成完整回复

```
用户说: 我想买 M8，预算 20 万

【知识】
[1] M8 官方指导价 19.98-22.98 万元...
[2] M8 搭载 2.0T 发动机...

【信息】{"model": "M8", "budget": "20万"}
【情绪】positive
【背景】[会话摘要] 用户首次咨询...
         [用户画像] {"preferences": [...], "entities": {...}}
```

**优势：** 回复风格天然一致、知识引用统一、不存在多 Agent 拼接时的语气冲突。

---

### Skill 可插拔架构

**对应文件：** `agents/skills/` + `agents/skill_registry.py` + `agents/skill_watcher.py`

Skill 在架构中扮演**纯元数据**角色——没有 `execute()` 方法，真正的执行者是 Response Agent（LLM）。

```python
class BaseSkill:
    name: str                    # 标识，与 sub_task 对应
    required_slots: List[str]    # 必需的槽位列表
    optional_slots: List[str]    # 可选槽位
    required_tools: List[Tool]   # 需要的工具列表
    instruction: str             # 给 Response Agent 的指令
```

#### 现有 Skill 一览

| Skill | sub_task | 必需槽位 | 需要 RAG | 说明 |
|-------|----------|---------|---------|------|
| **GreetingSkill** | `GREETING` | 无 | ❌ | 友好回应，主动询问需求 |
| **ProductSkill** | `PRODUCT` | `model` | ✅ | 回答产品配置/功能/特点 |
| **PriceSkill** | `PRICE` | `model` | ✅ | 回答价格/优惠/金融方案 |
| **FinanceSkill** | `FINANCE` | `model`, `budget` | ✅ | 分期方案建议 |
| **ComplaintSkill** | `COMPLAINT` | 无 | 可选 | 先安抚，不推销 |
| **PurchaseSkill** | `PURCHASE` | 无 | ✅ | 推荐车型，引导留资 |
| **WeatherSkill** | `WEATHER` | 无 | ❌ | 查询天气信息 |

#### 灰度发布与热重载（`agents/skill_loader.py` + `agents/skill_watcher.py`）

- 支持语义化版本号（如 `v1.2.0`）和灰度发布
- 基于 `user_id` 一致性哈希实现版本路由，按流量比例放量
- 后台线程定时轮询 Skill 文件变更，触发热重载
- **增删改 Skill 无需重启进程**

---
### 三级记忆系统

**对应文件：** `memory/conversation_memory.py`

模拟人类记忆机制的三级架构：

```
┌────────────────────────────────────────────────────────────┐
│                     三级记忆架构                            │
│                                                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ① 工作记忆 (Redis)                                  │   │
│  │ 存储：最近 20 条消息，TTL 24h                        │   │
│  │ 特点：毫秒级读写，满 15 条自动压缩                     │   │
│  │ 压缩：LLM 摘要旧消息 → 存入情景记忆 → 保留最近 5 条   │   │
│  └────────────────────────┬────────────────────────────┘   │
│                           │                                 │
│                           ▼                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ② 情景记忆 (ChromaDB "episodic")                    │   │
│  │ 存储：压缩后的历史对话片段，语义检索                    │   │
│  │ 查询：query_texts + where(user_id)                   │   │
│  └────────────────────────┬────────────────────────────┘   │
│                           │                                 │
│                           ▼                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ③ 用户画像 (ChromaDB "user_profile")                │   │
│  │ 存储：LLM 提炼的用户偏好 + 关键实体                    │   │
│  │ 更新：异步（asyncio.create_task），不阻塞主链路         │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

**上下文融合输出（`to_prompt_text()`）：**

```
[会话摘要]          ← Redis summary key
用户首次咨询 M8，关注价格...

[相关历史]          ← ChromaDB episodic 语义检索
- 上次也问过 M8 的配置...

[用户画像]          ← ChromaDB user_profile
{"preferences": ["高性价比"], "entities": {"产品": ["M8"]}}

[最近对话]          ← 最近 N 条原始消息
user: M8 多少钱
assistant: 指导价 19.98 万起
```

---

### RAG 检索链路

**对应文件：** `mcp/knowledge_base.py` + `mcp/tool_manager.py`

#### 完整检索流程

```
用户查询 → 寒暄检测（跳过寒暄不检索）
         → Query Rewrite（slots + sub_tasks → 增强 query）
         → 多角度子查询（LLM 改写为 3 个子查询并行召回）
         → 混合检索（向量 + BM25 并行执行，RRF 融合）
         → LLM 重排（对 Top-N 按相关性二次排序）
         → 返回 Top-K chunks
```

#### 三阶段文档切割（手写，无 LangChain 依赖）

```
Phase 1：递归切分
  从粗到细逐级尝试分隔符：段落(\n\n) → 行(\n) → 。 → ！ → ？ → 字符硬切
  每级切完看碎片大小，超过 500 字符降级到下一级继续

Phase 2：贪心凑块
  遍历碎片列表，尽可能合并到 ~500 字符但不超出

Phase 3：Overlap
  相邻 chunk 之间重叠 50 字符，防止边界信息丢失
```

#### 混合检索

| 方式 | 实现 | 优势 |
|------|------|------|
| **向量检索** | all-MiniLM-L6-v2/BGE 语义嵌入 | 理解语义相似、同义词 |
| **BM25 检索** | 手写倒排索引 + Jieba + 领域词典 | 精确匹配关键词 |

**RRF 融合（Reciprocal Rank Fusion）：** 不依赖分数归一化，仅基于排名合并：
```
RRF_score(d) = Σ 1 / (K + rank_r(d))  （K=60）
```

#### MCP 工具框架（`mcp/tool_manager.py`）

```
用户调用工具
 → ① 缓存检查（TTL 缓存，相同参数直接返回）
 → ② 熔断检查（CLOSED → OPEN → HALF_OPEN 三态）
 → ③ 参数校验（JSON Schema）
 → ④ 执行 + 超时（asyncio.wait_for）
 → ⑤ 缓存写入
 → ⑥ 可选 LLM 重排
```

#### 支持的文档格式

| 格式 | 解析引擎 | 说明 |
|------|---------|------|
| PDF | pdfplumber | 文本+表格分别处理，大表格整体入库不切分 |
| Word (docx) | python-docx | 段落+表格按原始顺序合并 |
| JSON | 标准 json | 批量导入 `[{title, content}]` |
| TXT/MD | 标准文本 | 全文导入 |

---

### 体验券与留资转化

> 留资转化嵌入在体验券发放链路中，不设独立的留资引导 Skill。由 CouponDecider 决策发券时机、前端券卡片交互引导填表、CouponWorker 超时释放保证库存闭环。

**对应文件：** `agents/coupon_decider.py` + `agents/coupon_manager.py` + `agents/lead_store.py`

#### 决策与触达

CouponDecider 在 Orchestrator 编排完成后判断是否展示体验券，不插入 sub_tasks：

| 维度 | 规则 | 说明 |
|------|------|------|
| **对话深度** | 轮数 > 5 | 用户已进行有意义的对话 |
| **购买意向** | sub_tasks 含 PRICE 或 PRODUCT | 表现出购车兴趣 |
| **情绪闸门** | positive / neutral | 情绪差时不触发 |
| **冷却保护** | Redis TTL 24h | 已领取/已拒绝的用户自动跳过 |

决策通过 `show_coupon` 字段传给前端，由前端渲染券卡片。**对话继续正常进行，不阻塞回复流程。**

#### 留资提交与持久化

用户确认领券后填写表单（姓名、电话），留资信息写入 MySQL `coupon_lead` 表 + Redis 缓存。CouponWorker 在超时时查询 MySQL 判断是否已留资，决定扣减库存或释放订单。

LeadStore 提供独立的 Redis 留资缓存层，用于快速的跨会话留资状态查询，与体验券系统松耦合。

---

### 对话式体验券系统

**对应文件：** `agents/coupon_decider.py` + `agents/coupon_manager.py` + `agents/coupon_worker.py` + `agents/rmq_client.py` + `agents/outbox_scanner.py`

对话中自动判断发券时机，引导用户领取体验券并填写留资表单。

#### 交互流程

```
后端响应含 show_coupon=true
    ↓
前端解析 → 渲染券卡片
    │
    ├─ [确认领取] → POST /coupon/claim
    │                  ↓ 原子扣库存 + 幂等
    │              弹留资浮层（60s 倒计时）
    │                  │
    │          ┌───────┴──────────┐
    │     用户填表提交        倒计时结束
    │          │                   │
    │    POST /coupon/lead     自动释放库存
    │    锁定体验券             CouponWorker
    │
    └─ [暂不需要] → 隐藏卡片
```

#### 触发规则（CouponDecider）

| 条件 | 值 | 说明 |
|------|-----|------|
| 对话轮数 | > 5 轮 | 用户已进行有意义的对话 |
| 用户意图 | 含 PRICE 或 PRODUCT | 表现出购买意向 |
| 用户情绪 | positive / neutral | 情绪好时转化率高 |

#### 可靠投递 — 消息确认机制

```
用户领取体验券
    ↓
CouponManager.claim()
    ├─ Lua 脚本：原子扣库存 + 幂等检查（SETNX）
    └─ INSERT outbox（MySQL 本地消息表）
         ↓
    OutboxScanner（定时轮询，每秒扫描）
         ↓ 有未投递消息
    rmq_client.publish() → RabbitMQ coupon.delay 队列
         ↓ 投递成功
    UPDATE outbox SET status='sent'
```

#### 超时释放 — TTL + DLX 死信队列

```
coupon.delay（TTL=60s）
    ↓ 到期未留资
死信 → coupon.timeout
    ↓
CouponWorker 消费
    ├─ Lua release 脚本：归还库存 + 清除用户状态
    └─ 通知前端（可选）
```

#### 补偿与对账

- 出队时检查消息有效性（时间窗口内 + 状态匹配）
- 定期对账：扫描已领取但超时未留资的记录，批量补偿释放
- 库存不丢失：确认 → 发送 → 状态更新的递进确认

---

### 评估体系

**对应文件：** `evaluation/multi_intent_evaluator.py`

#### 多标签意图 + 槽位提取评估

传统单标签 Accuracy 在客服场景中不适用——一个 query 可能命中三个标签。

| 维度 | 指标 | 说明 |
|------|------|------|
| Sub-task 分类 | **Macro F1** | 每个 sub_task 独立算 TP/FP/FN，再取算术平均 |
| Sub-task 集合 | **Exact Match Rate** | sub_task 集合完全一致的用例占比 |
| Slot 提取 | **Slot Macro F1** | 每个 slot name 独立算，比较"槽位名+值"完全匹配 |
| Slot 精度 | **Slot Exact Match Rate** | 所有槽位值完全正确的用例占比 |

**144 条 Golden Test Set** 覆盖常见多意图组合。

#### LLM-as-Judge 对话质量评估

| 维度 | 评分范围 | 低分原因 |
|------|---------|---------|
| relevance | 0.0-1.0 | Agent 跑偏 |
| accuracy | 0.0-1.0 | 幻觉/编造 |
| completeness | 0.0-1.0 | 回答太短/遗漏 |
| helpfulness | 0.0-1.0 | 太抽象 |

**Regression 检测：** 每次评测结果与基线对比，退化 > 5% 自动标记。

#### 数据闭环

```
线上每轮 Planner 输出 → 持久化 intent_logs.jsonl
         ↓
    Bad Case 分析 → 人工修正
         ↓
    补充到 Golden Test Set
         ↓
    模型微调 / Prompt 优化
```

---

### 安全防护

**对应文件：** `security/injection_detector.py` + `security/injection_patterns.json`

针对 LLM 客服场景的特点安全防护：

| 防护层 | 检测内容 | 处理方式 |
|--------|---------|---------|
| **提示注入** | 系统指令覆盖、角色扮演诱导 | 正则 + 模式匹配，命中则拒绝请求 |
| **敏感信息探测** | 试探系统配置、API Key 信息 | JSON 规则库匹配 |
| **社工攻击** | 诱导系统说违规内容 | 上下文感知模式匹配 |

---

## API 接口一览

| 路由 | 方法 | 功能 |
|------|------|------|
| **`/health`** | GET | 健康检查 |
| **`/chat`** | POST | 主对话接口（完整链路：记忆 → Planner → Orchestrator → Tool Layer → Response Agent） |
| **`/search`** | POST | RAG 检索演示（查询改写 + 并行召回 + 重排） |
| **`/knowledge/add`** | POST | 批量导入知识库文档 |
| **`/knowledge/upload`** | POST | 文件上传导入（PDF/DOCX/TXT/MD/JSON） |
| **`/knowledge/stats`** | GET | 知识库统计 |
| **`/knowledge/list`** | GET | 知识库片段列表 |
| **`/knowledge/{doc_id}`** | DELETE | 按文档 ID 删除 |
| **`/knowledge`** | DELETE | 清空知识库 |
| **`/coupon/claim`** | POST | 用户领取体验券（原子扣库存 + 幂等检查）|
| **`/coupon/lead`** | POST | 用户提交留资表单，锁定体验券 |
| **`/coupon/stats`** | GET | 体验券系统统计 |
| **`/coupon/check`** | GET | 检查用户是否可领取体验券 |
| **`/memory/profiles`** | GET | 用户画像列表 |
| **`/memory/episodic`** | GET | 情景记忆列表 |
| **`/eval/run`** | POST | 运行端到端评测 |
| **`/eval/multi`** | POST | 运行多意图评测 |
| **`/monitor`** | GET | 监控摘要 |
| **`/metrics`** | GET | Prometheus 指标 |

---

## 部署指南

### 前置条件

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Docker | 24+ | 容器运行时 |
| Docker Compose | v2.20+ | 多容器编排 |
| Git | 2.30+ | 代码管理 |
| Python (本地开发) | 3.12+ | 本地运行调试 |

### 快速部署（Docker Compose）

```bash
# 1. 克隆仓库
git clone https://github.com/your-org/echomind.git
cd echomind

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，至少填写：
#   - ANTHROPIC_API_KEY（LLM API Key）
#   - REDIS_PASSWORD（建议修改默认值）
#   - 其他数据库密码（建议修改默认值）

# 3. 一键启动所有服务
docker compose up -d

# 4. 检查服务状态
docker compose ps

# 5. 验证部署
curl http://localhost:8000/health
# 预期输出: {"status": "ok", ...}

# 6. 可选：通过 Nginx 访问
curl http://localhost:8084/health
```

### 各服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| EchoMind 主应用 | `8000` | FastAPI 服务，API 入口 |
| Nginx 反向代理 | `8084` | 生产环境推荐入口 |
| Redis | `6379` | 工作记忆 / 库存 |
| ChromaDB | `8001` | 向量数据库 |
| MySQL | `3307` | 业务持久化 |
| RabbitMQ | `5672` / `15672` | 消息队列 / 管理后台 |
| Prometheus | `9090` | 指标采集 |

### 本地开发模式

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 启动基础设施（Redis + ChromaDB + MySQL + RabbitMQ）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# 3. 启动应用（两种方式）
python api/main.py         # API 服务（自动 reload）
python api/main.py --cli   # CLI 交互模式（调试用）

# 4. 初始化知识库（可选）
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@./data/product_catalog.pdf"
```

### 配置说明

关键环境变量（完整列表见 `.env.example`）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ANTHROPIC_API_KEY` | — | LLM API Key（必填）|
| `ANTHROPIC_MODEL` | `deepseek-chat` | 模型名称 |
| `ANTHROPIC_BASE_URL` | — | 兼容 API 的第三方地址 |
| `REDIS_PASSWORD` | `echomind123` | Redis 密码 |
| `RABBITMQ_PASS` | `echomind123` | RabbitMQ 密码 |
| `MYSQL_ROOT_PASSWORD` | `root123` | MySQL root 密码 |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | 嵌入模型 |
| `ENABLE_MONITORING` | `true` | 是否启用监控 |
| `RAG_CONFIDENCE_THRESHOLD` | `0.5` | RAG 置信度闸门 |

#### 多模型兼容配置

EchoMind 支持多种 LLM 后端，通过修改 `.env` 即可切换：

```bash
# 方案一：Anthropic Claude（默认）
ANTHROPIC_API_KEY=sk-...
# 无需设置 ANTHROPIC_BASE_URL

# 方案二：DeepSeek（兼容 Anthropic 协议）
ANTHROPIC_API_KEY=sk-...
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-chat

# 方案三：Ollama 本地模型（降级兜底）
# 无需 API Key，主 API 超时后自动降级到本地 ollama
```

### 生产环境注意事项

```yaml
# docker-compose.yml 中的关键生产配置
- restart: unless-stopped    # 所有服务自动恢复
- Nginx 反向代理 + 限流 10r/s
- Redis AOF 持久化 + Password 认证
- MySQL 8.0 持久卷挂载
- RabbitMQ 持久化
- 健康检查链确保启动顺序
```

**Build 镜像：**

```bash
# 构建生产镜像（多阶段构建，~500MB）
./build-image.sh

# 或者手动构建
docker build --target production -t echomind:latest .
```

**性能建议：**
- **单核 2GB RAM**：可运行全部服务（测试/演示环境）
- **4 核 8GB RAM**：建议配置，支持中等并发
- **SSD 存储**：MySQL + ChromaDB 对 IO 敏感
- **Milvus 切换**：数据量达百万级向量时，docker-compose.yml 中已部署 Milvus 全家桶（etcd + MinIO + Milvus + Attu），修改 Embedding 配置即可无缝切换

---

## 开发者指南

### 项目结构

```
echomind/
├── api/
│   └── main.py                 # FastAPI 应用入口 + 路由
├── agents/
│   ├── orchestrator.py         # 编排引擎 + Response Agent
│   ├── slot_manager.py         # 会话槽位管理
│   ├── tool_layer.py           # 统一工具调度
│   ├── skill_registry.py       # Skill 注册中心
│   ├── skill_loader.py         # Skill 加载器（灰度/版本）
│   ├── skill_watcher.py        # 文件变更热重载
│   ├── skills/                 # Skill 定义目录
│   │   ├── base.py             # BaseSkill 基类（元数据定义）
│   │   ├── greeting.md         # GreetingSkill
│   │   ├── price.md            # PriceSkill
│   │   ├── product.md          # ProductSkill
│   │   ├── finance.md          # FinanceSkill
│   │   ├── complaint.md        # ComplaintSkill
│   │   ├── purchase.md         # PurchaseSkill
│   │   ├── weather.md          # WeatherSkill
│   │   └── ...                 # 更多 Skill
│   ├── lead_store.py           # 留资冷却管理
│   ├── coupon_decider.py       # 体验券触发决策
│   ├── coupon_manager.py       # 体验券库存管理
│   ├── coupon_worker.py        # 体验券超时消费
│   ├── coupon_reconcile.py     # 体验券对账补偿
│   ├── rmq_client.py           # RabbitMQ 客户端
│   └── outbox_scanner.py       # 本地消息表轮询投递
├── core/
│   ├── intent_recognizer.py    # Planner（理解层）
│   ├── planner_fallback.py     # Planner 降级方案
│   ├── rate_limiter.py         # 优先级限流器
│   └── work_queue.py           # 请求排队
├── memory/
│   └── conversation_memory.py  # 三级记忆系统
├── mcp/
│   ├── knowledge_base.py       # 知识库管理 + 文档切分
│   └── tool_manager.py         # 工具调用框架（缓存/熔断/重排）
├── security/
│   ├── injection_detector.py   # 提示注入检测
│   └── injection_patterns.json # 注入模式规则库
├── evaluation/
│   └── multi_intent_evaluator.py # 多意图评估器
├── static/
│   ├── index.html              # 前端页面
│   ├── css/style.css           # 样式
│   └── js/                     # 前端脚本
├── config/
│   └── nginx/
│       ├── nginx.conf          # 生产 Nginx 配置
│       └── nginx.dev.conf      # 开发 Nginx 配置
├── sql/
│   └── init_coupon.sql         # 体验券表初始化
├── docs/                       # 设计文档
├── tests/                      # 测试数据
├── Dockerfile                  # 多阶段构建
├── docker-compose.yml          # 生产编排
├── docker-compose.dev.yml      # 开发覆盖
├── requirements.txt            # Python 依赖
└── .env.example                # 环境变量模板
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

1. 创建 `skills/trade_in.md`
2. 在 `SkillRegistry.init_defaults()` 注册
3. 在 Planner 的 `_FEWSHOT` 中添加一条 few-shot 示例
4. **完成。Orchestrator 和 Response Agent 不需要改一行代码。**

### 运行评估

```bash
# 多意图评估（144 条 Golden Test Set）
curl -X POST http://localhost:8000/eval/multi

# 自定义测试用例
curl -X POST http://localhost:8000/eval/multi \
  -H "Content-Type: application/json" \
  -d '{"cases": [{"query": "M8多少钱", "sub_tasks": ["PRICE"], "slots": {"model": "M8"}}]}'

# 端到端评估
curl -X POST http://localhost:8000/eval/run
```

### 导入知识库

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

## 项目演进路线

### ✅ Phase 1：架构定义与验证
- [x] 三层架构（理解 → 编排 → 生成）设计与冻结
- [x] Skill 定义为纯元数据（无 execute）
- [x] Planner 输出格式（全量 sub_tasks + Diff slot_ops）
- [x] Slot Diff 合并策略（SET / DELETE）
- [x] Orchestrator 编排逻辑
- [x] Tool Layer 设计（RAG 统一执行）

### ✅ Phase 2：核心数据流
- [x] `slot_manager.py` — 会话槽位状态管理
- [x] `skill_registry.py` — Skill 注册中心
- [x] BaseSkill 元数据基类 + 8 个 Skill 定义
- [x] `tool_layer.py` — 统一工具调度
- [x] Planner Prompt 设计（14 条 few-shot 示例）
- [x] `orchestrator.py` — 完整链路串联

### ✅ Phase 3：Agent 改造与记忆系统
- [x] 三级记忆系统（工作记忆/情景记忆/用户画像）
- [x] 历史压缩与摘要生成
- [x] 用户画像异步更新（不阻塞主链路）
- [x] 上下文融合输出
- [x] API 全面适配新架构

### ✅ Phase 4：体验券留资系统
- [x] CouponDecider — 触发规则判断
- [x] Redis Lua 原子扣库存 + 幂等检查
- [x] 本地消息表 + OutboxScanner 可靠投递
- [x] RabbitMQ TTL + DLX 超时释放 + Lua 补偿回滚
- [x] 24h 冷却 + 拒绝保护
- [x] 对账补偿机制

### ✅ Phase 5：RAG 与评估体系
- [x] 三阶段文档切割（手写，无框架依赖）
- [x] 混合检索（向量 + BM25 → RRF）
- [x] LLM 查询改写（3 角度子查询并行召回）
- [x] LLM 重排
- [x] 多意图评估器（Macro F1 + Exact Match Rate）
- [x] 144 条 Golden Test Set
- [x] LLM-as-Judge 四维评分
- [x] Regression 检测

### ✅ Phase 6：生产级加固
- [x] Skill 灰度发布 + 语义化版本号
- [x] 文件变更热重载（增删改 Skill 无需重启进程）
- [x] 优先级限流 + 请求排队
- [x] 提示注入检测
- [x] 多模型兼容（Claude / DeepSeek / Ollama）
- [x] Docker 多阶段构建（~500MB）
- [x] 全服务健康检查链

### 🔜 下一步方向
- **知识图谱**：引入 Graph RAG 处理跨实体多跳查询
- **小模型蒸馏**：积累足够 intent_logs.jsonl 后蒸馏本地模型降本
- **多副本部署**：单进程 → 多 uvicorn worker + Nginx 负载均衡
- **异步 Redis 客户端**：`redis.asyncio.Redis` 替代同步客户端
- **在线学习**：基于反馈数据在线微调留资时机模型

---

## FAQ

### 为什么用 LLM 而非 BERT 做意图识别？

Planner 需要同时输出多标签 sub_tasks + slot_ops 序列 + emotion——这不是分类问题，而是**结构化生成**问题。BERT 输出的是类别概率分布，处理不了多标签组合 + 槽位提取。LLM 零样本能力让项目冷启动就能跑起来，同时通过 intent_logs.jsonl 积累数据，够量后蒸馏到小模型。

### 为什么选 ChromaDB 而非 Milvus？

项目早期没有 DBA 资源，ChromaDB 嵌入式运行、无服务依赖、SQLite 持久化，对于几千到几万个片段完全够用。docker-compose.yml 里已经部署了 Milvus 全家桶（etcd + MinIO + Milvus + Attu），数据量上去了无缝切换。

### 为什么用 BGE 嵌入模型？

BGE 系列（BAAI/bge-xxx-zh-v1.5）是中文社区广泛验证的嵌入模型。通过 fastembed（ONNX 运行时）加载，无需 PyTorch 依赖，镜像体积小，推理速度快。384 维的 `bge-small-zh-v1.5` 在中文语义检索任务上表现优异，性能与 768 维模型相当但速度更快。

### 为什么不用 LangChain / LangGraph？

抽象太厚调试成本高、80% 场景用不到框架复杂度、依赖重 API 变动频繁。核心流程就三步（Planner → Orchestrator → Response Agent），手写灵活可控。如果后续需要多步 Agent 循环，LangGraph 是合理的选择。

### 为什么不用 Elasticsearch？

几千个 chunk 专门跑一个 ES 集群太重。纯 Python BM25 + Jieba 能做到一样的事，代码不到 200 行。

### 如何保障体验券库存不超发？

双重保障：
1. **Redis Lua 脚本**保证扣库存和幂等检查是原子操作，单机 Redis 下不会超发
2. **MySQL + Outbox 模式**保证不会因为消息丢失导致状态不一致
3. **对账补偿**定期扫描异常状态，批量修复不一致数据

### 支持哪些 LLM 模型？

支持所有兼容 Anthropic Messages API 的大模型：
| 模型 | 配置方式 | 说明 |
|------|---------|------|
| Claude 5 Sonnet/Opus | 默认 | 推荐主力模型 |
| DeepSeek-Chat | 改 `ANTHROPIC_BASE_URL` | 性价比之选 |
| Ollama 本地模型 | 自动降级 | API 超时 / 无网络时兜底 |

---

*EchoMind v3.0 — 企业级留资型智能客服系统*
*项目文档 · 最后更新 2026-07-13*
