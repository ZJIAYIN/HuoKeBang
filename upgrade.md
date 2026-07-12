# EchoMind 降级体系升级方案

> 本文档系统性地记录了 EchoMind 在降级/兜底方面的薄弱环节分析，以及针对 Planner 层的高优改造方案。

---

## 一、现状总览

### 1.1 做得好的（现有降级设计）

| 组件 | 降级机制 |
|------|---------|
| **SlotManager** | Redis 不可用 → 自动降级内存 dict |
| **MCPToolManager** | 3 态熔断器 + fallback 回调 + 超时 + TTL 缓存 |
| **KnowledgeBase** | ChromaDB 服务不可用 → PersistentClient 本地模式 |
| **ResponseAgent** | LLM 失败 → "抱歉，我暂时无法回答..." |
| **ToolLayer.exec_rag** | LLM 改写/重排失败 → 降级直接混合检索 |
| **Planner** | LLM 返回非 JSON → 正则提取 + 字段默认值 |
| **InjectionDetector** | 失败 → 降级放行（安全优先）|
| **天气 API** | 失败 → 返回 None，ResponseAgent 安全跳过 |

### 1.2 严重缺失（系统可能直接崩溃）

| # | 位置 | 问题 | 影响 |
|---|------|------|------|
| P0-1 | `lead_store.py:40` | `redis.from_url()` 裸调用，无 try-except | Redis 连不上 → AgentEngine 启动崩溃 |
| P0-2 | `memory/conversation_memory.py:97` | 同上，`redis.from_url()` 无降级 | MemoryManager 初始化失败 → 服务 503 |
| P0-3 | `orchestrator.py:410` | tool_layer 为 None 时 `exec_tools()` 触发 AttributeError | 无 KnowledgeBase 时直接崩溃 |
| P0-4 | `api/main.py` | 没有 `@app.exception_handler` 全局异常拦截 | 500 原始暴露，无用户友好提示 |
| P0-5 | `orchestrator.py:393` 等 | API Key 硬编码在 6 个文件中 | 轮换必须改代码，泄露不可远程吊销 |
| P0-6 | `memory/conversation_memory.py:382` | `json.loads(raw)` 无 try-except | Redis 单条消息损坏 → 整个 `get_context()` 抛异常 |

### 1.3 中等缺失（能力降级不彻底）

| # | 位置 | 问题 |
|---|------|------|
| P1-1 | `AgentEngine.run()` | 无整体超时，LLM 阻塞 → 请求无限挂起 |
| P1-2 | `ResponseAgent.generate()` | 单次 try-except 无重试，临时抖动直接失败 |
| P1-3 | `Planner._llm_plan()` | 同上，LLM 调用无重试 |
| P1-4 | `tool_layer.py:160` | RAG 降级也失败 → 返回空 `[]`，无降级标记 |
| P1-5 | `orchestrator.py:160` | 未知 sub_task 静默跳过，无反馈 |
| P1-6 | `mcp/tool_manager.py:377` | 缓存上限用 dict 手动驱逐，高并发 OOM 风险 |
| P1-7 | `injection_detector.py:158` | 失败降级放行但无告警 |
| P1-8 | `knowledge_base.py:450` | 降级扫描模式全表查询，大知识库内存暴涨 |

### 1.4 轻量缺失（边界体验）

| # | 位置 | 问题 |
|---|------|------|
| P2-1 | `lead_store.py:121` | `clear_user()` 用 `keys()`，O(n) 阻塞 |
| P2-2 | `orchestrator.py:446` | `slot_manager.all` 内联调用无本地快照 |
| P2-3 | `api/main.py` | 注入拦截后 `latency_ms: 0.0` |
| P2-4 | Skills | `Tool.CRM` / `Tool.CALCULATOR` 预留无实现 |

---

## 二、Planner 降级方案（高优改造）

### 2.1 问题定义

当前 Planner 在 LLM 失败时统一降级为：

```python
return PlannerOutput(
    primary_intent="chitchat",
    sub_tasks=["GREETING"],
    slot_ops=[],
    emotion="neutral",
    confidence=0.0,
    reasoning=f"Planner 失败: {ex}",
)
```

问题：无论用户问什么，LLM 异常时永远返回问候意图。**意图全丢、槽位全丢、情绪全丢。**

### 2.2 改造目标

引入 **PlannerFallback 降级层** —— 纯代码、不调 LLM，结合语义相似度 + 关键词加权匹配，输出和正常 Planner 完全一致的 `PlannerOutput`。

### 2.3 整体链路

```
用户消息
    │
    ├── LLM Planner (主流程)
    │      ├── 成功 → 正常 PlannerOutput
    │      ├── 429 限流 / 超时 → 触发降级
    │      ├── JSON 解析失败 → 触发降级
    │      └── 其他异常 → 触发降级
    │
    └── PlannerFallback (降级层) ← 纯代码，零 LLM 调用
           │
           ├── 1. 语义相似度匹配（BGE 向量）
           │     对 few-shot 示例消息计算 cosine similarity
           │
           ├── 2. 关键词加权匹配（BM25 风格）
           │     每个 Intent 预定义关键词词典 → 命中率打分
           │
           ├── 3. 加权融合
           │     score = sim × 0.6 + keyword × 0.4
           │     ├── > 0.80 → 高置信度，用该 Intent
           │     ├── > 0.55 → 中置信度，用该 Intent + 标记降级
           │     └── ≤ 0.55 → 默认 CHITCHAT
           │
           ├── 4. 正则槽位提取
           │     phone / wechat / model / budget / location / issue
           │
           └── 5. 代码拼接完整 PlannerOutput
```

### 2.4 详细设计

#### 2.4.1 语义相似度匹配

复用项目已有的 `BGEChineseEmbeddingFunction`（`mcp/knowledge_base.py`），fastembed ONNX 推理，无需 PyTorch。

```python
# 初始化时预计算 few-shot 示例的向量
self._embed_model = BGEChineseEmbeddingFunction()
self._fs_messages = [msg for _, _, _, msg, _ in _FEWSHOT]
self._fs_intents  = [cat.value for cat, _, _, _, _ in _FEWSHOT]
self._fs_emotions = [emo for _, _, emo, _, _ in _FEWSHOT]
self._fs_embeddings = list(self._embed_model.embed(self._fs_messages))

# 降级时：用户消息向量化 → 算与所有示例的 cosine similarity → Top-3
query_vec = list(self._embed_model.embed([message]))[0]
sims = cosine_similarity(query_vec, self._fs_embeddings)
top3_idx = sims.argsort()[-3:][::-1]
```

#### 2.4.2 关键词加权匹配

```python
INTENT_KEYWORDS = {
    "GREETING":     ["你好", "在吗", "嗨", "hello", "hi", "早上好", "晚上好", "在不在"],
    "PRODUCT":      ["配置", "车型", "怎么样", "参数", "功能", "介绍", "M8", "M9", "M7", "问界"],
    "PRICE":        ["多少钱", "价格", "贵", "便宜", "报价", "价位", "预算", "多少"],
    "FINANCE":      ["分期", "贷款", "首付", "月供", "利率", "金融", "利息", "按揭"],
    "PURCHASE":     ["买", "下单", "订购", "购买", "提车", "订", "怎么买", "门店"],
    "COMPLAINT":    ["投诉", "差", "垃圾", "骗子", "滚", "服务差", "不满", "态度", "没人理", "等了"],
    "LEAD_CAPTURE": ["电话", "微信", "联系", "号码", "加我", "加微", "手机", "联系方式"],
    "CONTACT_NO":   ["不方便", "不留", "不需要", "算了", "不要了", "别", "不必"],
    "WEATHER":      ["天气", "下雨", "温度", "多云", "晴", "台风", "多少度", "冷", "热", "气温"],
}

def keyword_score(self, message: str) -> Dict[str, float]:
    """对每个 intent 统计关键词命中率 = 命中数 / 该 intent 总关键词数"""
    scores = {}
    for intent, kws in INTENT_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in message)
        scores[intent] = hits / len(kws)
    return scores
```

#### 2.4.3 加权融合公式

```python
def fuse(self, message: str, sim_scores: np.ndarray, kw_scores: Dict[str, float]) -> List[Tuple[str, float]]:
    """
    加权融合策略：
      - 语义分数：取与每个 intent 的所有示例最大相似度（归一化到 0-1）
      - 关键词分数：命中率
      - 融合：score = sim × 0.6 + keyword × 0.4
    """
    SIM_WEIGHT, KW_WEIGHT = 0.6, 0.4
    results = []
    valid_intents = {c.value for c in IntentCategory}

    # 对 intent 聚合语义分数（取每个 intent 对应的示例中最高相似度）
    intent_sim = defaultdict(float)
    for i, sim in enumerate(sim_scores):
        intent = self._fs_intents[i]
        intent_sim[intent] = max(intent_sim[intent], sim)

    for intent in valid_intents:
        sim = intent_sim.get(intent, 0.0)
        kw = kw_scores.get(intent, 0.0)
        total = sim * SIM_WEIGHT + kw * KW_WEIGHT
        results.append((intent, total))

    return sorted(results, key=lambda x: -x[1])
```

置信度判断：

| 融合分数 | 行为 |
|---------|------|
| > 0.80 | 高置信度 → 直接用该 Intent |
| > 0.55 | 中置信度 → 用该 Intent + reasoning 标记降级 |
| ≤ 0.55 | 低置信度 → 默认 CHITCHAT |

#### 2.4.4 正则槽位提取

```python
SLOT_PATTERNS = {
    "phone":    re.compile(r"(1[3-9]\d)\s*(\d{4})\s*(\d{4})"),
    "wechat":   re.compile(r"(?:微信|wechat|vx|VX)[：:\s]*([a-zA-Z0-9_-]{6,20})"),
    "model":    re.compile(r"(?:问界\s*)?(M\d{1,2})"),
    "budget":   re.compile(r"(\d+[\.\d]*)\s*万"),
    "location": re.compile(r"(北京|上海|广州|深圳|杭州|成都|武汉|南京|重庆|"
                           r"苏州|西安|长沙|天津|郑州|东莞|青岛|沈阳|宁波|昆明)"),
    "issue":    re.compile(r"(等了|没人理|服务差|质量|问题|故障|异常|出错|无法|不能|坏了)"),
}
```

提取逻辑：

1. 按模式遍历，每个 slot 只取第一个匹配
2. `lead_refused` 特殊处理：如果 `CONTACT_NO` 是 Top-1 intent，自动将 `lead_refused` 设为 `true`
3. 手机号提取后做格式校验（同现有 `Orchestrator` 逻辑）

#### 2.4.5 情绪继承

情绪通过 few-shot 示例的情绪映射 + 关键词加权得到：

```python
# 从 Top-3 最相似示例取情绪
top3_emos = [self._fs_emotions[i] for i in top3_idx]
best_emotion = max(set(top3_emos), key=top3_emos.count)  # 取众数

# 特殊规则
if any(kw in message for kw in INTENT_KEYWORDS["COMPLAINT"]):
    best_emotion = "negative"
```

### 2.5 改造涉及的文件

| 文件 | 改动 |
|------|------|
| `core/intent_recognizer.py` | 新增 `PlannerFallback` 类；`Planner._llm_plan()` 的 except 块接入降级 |
| （新文件）`core/planner_fallback.py` | `PlannerFallback` 独立实现，方便测试和迭代 |
| `mcp/knowledge_base.py` | 将 `BGEChineseEmbeddingFunction` 提取到公共位置（可选） |
| `tests/test.jsonl` | 扩充测试用例覆盖降级场景 |

### 2.6 评估方法

利用现有的 `evaluation/multi_intent_evaluator.py`：

```
# 切换 Planner 为 Fallback 模式运行评估
evaluator = MultiIntentEvaluator(fallback_planner)
report, _ = await evaluator.eval(test_cases, detail=True)
```

评估指标：

- **Macro F1**：降级后的意图分类准确率
- **Slot Exact Match Rate**：正则提取的槽位准确率
- **Degradation Rate**：触发降级时的正确率（fallback planner 对了多少次）

---

## 三、实施路线图

### Phase 1 — P0 修复（系统不崩溃）✅ 已完成

- [x] `lead_store.py`：`redis.from_url()` 加 try-except + socket_timeout，降级后 _redis=None
- [x] `conversation_memory.py`：同上 + 所有 Redis 操作加 None 守卫 + json.loads try-except
- [ ] `api/main.py`：加 `@app.exception_handler` 全局兜底
- [ ] `AgentEngine.run()`：加 `asyncio.wait_for()` 整体超时
- [ ] `orchestrator.py`：tool_layer 为 None 时跳过工具调用

### Phase 2 — PlannerFallback ✅ 已完成

- [x] `core/embedding_manager.py`：BGE 嵌入模型单例管理
- [x] `core/planner_fallback.py`：PlannerFallback 降级层
  - [x] 语义相似度匹配（BGE 向量）
  - [x] 关键词加权匹配（归一化命中率）
  - [x] 加权融合（sim×0.6 + keyword×0.4）+ 置信度分档
  - [x] 正则槽位提取（phone/wechat/model/budget/location/issue）
  - [x] 启发式兜底（纯数字手机号→contact_give）
  - [x] 情绪默认 neutral
  - [x] 代码构造 PlannerOutput
- [x] `core/intent_recognizer.py`：在 `_llm_plan()` except 块接入降级
- [ ] 单元测试 + 评估（用现有 evaluator 跑准确率）

### Phase 3 — P1 修复（体验优化）

- [ ] Planner + ResponseAgent 加指数退避重试
- [ ] `ToolLayer.exec_rag` 全链路失败返回降级标记
- [ ] `injection_detector.py` 降级时推 Prometheus 告警

### Phase 4 — P2 迭代

- [ ] API Key 从环境变量读取
- [ ] 缓存上限用 `cachetools` 替代手动驱逐
- [ ] `lead_store.py` 的 `keys()` 替换为 `scan()`

---

## 四、设计原则

1. **降级可观测**：每次降级输出 `confidence` 分数 + `reasoning` 文本，让调用方知道"这是降级结果"
2. **降级透明的给用户**：ResponseAgent 的 prompt 里注入 `[降级标记]`，使其回复更谨慎
3. **降级可评估**：用现有的 multi_intent_evaluator 对降级层做量化测试
4. **纯代码优先**：降级层不调 LLM，不依赖网络，零外部依赖

---

## 五、风险和注意事项

1. **正则槽位提取的局限性**：仅覆盖常见的槽位表达方式（如手机号 11 位规则），用户超常规表达（如"打给我 138...88"中的不规则分段）可能漏提
2. **few-shot 示例的覆盖度**：当前 only 14 条示例，随着业务扩展需要补充更多训练样本提升语义匹配准确率
3. **关键词的领域适配**：关键词词典目前人工维护，后续可以考虑从对话日志中自动挖掘高频词
