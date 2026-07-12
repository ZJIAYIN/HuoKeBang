# Planner JSON 输出校验：现状分析与改进方案

## 现状

### 当前 Planner 的校验逻辑（`core/intent_recognizer.py`）

Planner 是系统的理解层，负责将用户自然语言转化为结构化 JSON。当前校验流程：

**第 1 步：JSON 提取**
```python
raw = resp.content[0].text
s, e = raw.find("{"), raw.rfind("}") + 1
data = json.loads(raw[s:e])
```

**第 2 步：字段级手动校验**
```python
primary = data.get("primary_intent", ...)
if primary not in valid_intents:
    primary = IntentCategory.CHITCHAT.value

sub_tasks = data.get("sub_tasks", [])
if not isinstance(sub_tasks, list):
    sub_tasks = [primary]

emotion = data.get("emotion", ...)
if emotion not in valid_emotions:
    emotion = Sentiment.NEUTRAL.value

# slot_ops 逐条校验
for op in raw_ops:
    try:
        op_type = SlotOpType(op.get("op", "SET"))
        ...
    except (ValueError, KeyError):
        continue
```

### 现存问题

| 问题 | 严重程度 | 说明 |
|------|---------|------|
| JSON 提取脆弱 | 高 | `find("{")` / `rfind("}")` 在 code fence、嵌套 JSON、多 JSON 时可能截错 |
| 无 Schema 校验 | 高 | 所有字段用手写 if-else 逐个检查，新增字段容易漏 |
| subtask 名称不校验 | 中 | LLM 可能输出 SkillRegistry 中不存在的名称，只在 Orchestrator 静默跳过 |
| confidence 未使用 | 中 | 字段解析了但没有任何阈值判断，低置信度输出照样执行 |
| 无重试机制 | 中 | 一次失败直接走 fallback，没有给 LLM 修正的机会 |
| 槽位值无业务校验 | 低-中 | 手机号格式、产品名存在性等没有代码层检查 |
| 无监控指标 | 低 | 没有统计 Planner 失败率、重试率，退化趋势不可见 |

---

## 改进方案

### 方案 A：基于 Pydantic 的 Schema 校验（推荐，低投入高收益）

在现有架构上增加一层声明式校验，替换手写 if-else。

```python
from pydantic import BaseModel, Field, field_validator

class SlotOpSchema(BaseModel):
    op: str = Field(pattern="^(SET|DELETE)$")
    slot: str = Field(min_length=1)
    value: Any = None

class PlannerOutputSchema(BaseModel):
    primary_intent: str
    sub_tasks: List[str]
    slot_ops: List[SlotOpSchema] = []
    emotion: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    reasoning: str = ""

    @field_validator("sub_tasks", each_item=True)
    @classmethod
    def subtask_must_exist(cls, v):
        if not SkillRegistry.has(v):
            raise ValueError(f"未知 sub_task '{v}'")
        return v
```

**收益**：新增字段只需加一行声明，校验逻辑集中不散落，错误信息可读。

**成本**：加一个 pydantic 依赖，~30 行代码。

---

### 方案 B：JSON 提取加固（低投入，必须做）

```python
@staticmethod
def _extract_json(raw: str) -> dict:
    # 1) 去掉 markdown code fence
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r'\s*```$', '', cleaned.strip(), flags=re.MULTILINE)
    # 2) 直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 3) 回退：找 { } 块
    s = cleaned.find("{"); e = cleaned.rfind("}")
    if s >= 0 and e > s:
        return json.loads(cleaned[s:e+1])
    raise ValueError("无法提取 JSON")
```

---

### 方案 C：Retry 机制（挽回格式错误，降低 fallback 率）

```python
for attempt in range(MAX_RETRIES + 1):
    try:
        data = await self._call_llm(...)
        validated = PlannerOutputSchema.model_validate(data)
        break
    except (json.JSONDecodeError, ValidationError) as e:
        if attempt < MAX_RETRIES:
            user_content += _build_error_feedback(str(e))
        else:
            return self._fallback(...)
```

**收益**：90% 的格式错误一次重试就能修复（LLM 很擅长修自己的格式）。

**成本**：最多多一次 LLM 调用（~200ms），但避免了 fallback 导致的体验降级。

---

### 方案 D：置信度阈值守卫（低投入，高安全性）

```python
if validated.confidence < CONFIDENCE_THRESHOLD:  # 如 0.4
    logger.warning(f"低置信度: {validated.confidence}")
    return self._low_confidence_fallback(validated)
```

收益：LLM 自己觉得不确定时，系统不盲目执行。

---

### 方案 E：使用模型原生的 Structured Output（改变依赖，收益最大）

| 方案 | 要求 | 保证程度 |
|------|------|---------|
| OpenAI `response_format: json_schema` | 换用 OpenAI | 生成阶段约束，格式一定对 |
| Claude / OpenAI tool calling | 换用对应 API | 同上 |
| DeepSeek OpenAI 兼容接口 + tool calling | 换 SDK | 同上 |
| Grammar-based（outlines） | 自部署模型 | 同上 |

**收益**：格式错误从源头消除，不需要 Retry。

**成本**：需要更换模型供应商或 SDK。

---

## 推荐优先级

```
短期（当前迭代，1-2天）:
  ✅ 方案 B: JSON 提取加固（修复 code fence 等问题）
  ✅ 方案 A: Pydantic Schema 校验（替换手写 if-else）
  ✅ 方案 D: confidence 阈值守卫（低投入高安全）

中期（下个迭代）:
  ✅ 方案 C: Retry 机制（进一步降低 fallback）

长期:
  ✅ 方案 E: 评估支持 Structured Output 的模型
```

---

## 与面试的关联

如果被问到"怎么保证结构化输出的准确性"，回答框架：

> **我们分三层来保证。**
>
> **第一层，Prompt 工程**——system prompt 明确给出 JSON schema 和枚举值列表，加上 10+ 条 few-shot 示例，temperature=0.1 确保输出稳定。
>
> **第二层，代码校验**——JSON 解析后经过 Pydantic Schema 校验（类型、枚举、必填字段）和业务校验（手机号格式、产品名存在性）。校验不通过的数据不会进入下游。
>
> **第三层，容错机制**——格式错误时触发 Retry，把错误信息喂回给 LLM 让它修正，90% 一次重试就能修复。所有重试失败则走安全 Fallback（默认 CHITCHAT/GREETING），系统不崩溃。
>
> **长期方向是迁移到支持 Structured Output 的模型**，从 token 生成阶段就约束输出，彻底消除格式错误。
