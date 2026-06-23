# Multi-Intent 评估方案

## 背景

旧架构每次只输出一个意图标签，评估用 accuracy + macro F1 即可：

```
输入: "M8多少钱"  →  输出: PRICE      ↔  标注: PRICE      ✓
```

新架构输出多维度信息（sub_tasks + slot_ops），评估需要升级。

## 评估维度（两部分，不评无关的）

| 维度 | 是否评测 | 原因 |
|------|---------|------|
| sub_tasks（多标签） | ✅ | 核心能力——Planner 是否正确识别了用户的多个意图 |
| slot_ops（槽位提取） | ✅ | 核心能力——Planner 是否正确提取了实体值 |
| primary_intent | ❌ | sub_tasks 覆盖了它，是冗余信息 |
| emotion | ❌ | 只影响 LEAD_CAPTURE 一个 Skill 的开关，不值得单独上指标 |
| end-to-end 响应质量 | ❌ | 已有 LLM-as-Judge，与本评估器解耦 |

## 测试用例格式

每行一条 JSON，多轮场景不纳入，每个用例独立测试。

```jsonl
# tests/multi_intent_eval.jsonl
{"query": "M8什么配置",                      "sub_tasks": ["PRODUCT"],                   "slots": {"model": "M8"}}
{"query": "多少钱一个月",                    "sub_tasks": ["PRICE"],                     "slots": {}}
{"query": "M8怎么样，贵不贵？",               "sub_tasks": ["PRODUCT", "PRICE"],           "slots": {"model": "M8"}}
{"query": "预算20万，M8能分期吗",             "sub_tasks": ["PRICE", "FINANCE"],          "slots": {"model": "M8", "budget": "20万"}}
{"query": "我要买，怎么下单？",               "sub_tasks": ["PURCHASE", "LEAD_CAPTURE"],   "slots": {}}
{"query": "等了这么久没人理我",               "sub_tasks": ["COMPLAINT"],                 "slots": {"issue": "无人响应"}}
{"query": "13712345678",                    "sub_tasks": ["LEAD_CAPTURE"],               "slots": {"phone": "13712345678"}}
{"query": "不方便留电话",                    "sub_tasks": ["CONTACT_NO"],                 "slots": {"lead_refused": true}}
{"query": "全款多少钱",                      "sub_tasks": ["PRICE"],                     "slots": {}}     ← 不测多轮，不继承历史 context_slots
```

不支持的场景：
- 不测多轮对话（太长、不可控）
- 不测 context_slots / history 继承（要测就新写一条带上下文的 query）
- 不测 Slot 值归一化（"20万" vs "200000" —— Planner 提取原文，精确匹配即可）

## 评估指标

### 1. Sub-task 多标签分类

每个 sub_task（PRODUCT / PRICE / FINANCE / COMPLAINT / LEAD_CAPTURE / CONTACT_NO / PURCHASE / GREETING）独立算混淆矩阵，再做宏平均。

```
               实际有    实际无
预测有           TP       FP
预测无           FN       TN
```

**Per-sub_task 指标**：

```
Precision_i = TP_i / (TP_i + FP_i)
Recall_i    = TP_i / (TP_i + FN_i)
F1_i        = 2 * P_i * R_i / (P_i + R_i)
```

**宏平均**：

```
Macro Precision = avg(Precision_i)  对所有 i
Macro Recall    = avg(Recall_i)     对所有 i
Macro F1        = avg(F1_i)         对所有 i
```

**Exact Match Rate** —— 补充指标：

```
EMR = count(sub_tasks 完全等于标准) / total_cases
```

sub_task 集合完全一致才算 1，漏一个或多一个都算 0。比 macro-F1 更严，两者互补展示。

---

#### 计算示例

3 条用例，标准 sub_task 只涉及 PRICE / FINANCE / COMPLAINT 三类：

```
用例1: 实际 [PRICE]          预测 [PRICE, FINANCE]
用例2: 实际 [PRICE, FINANCE] 预测 [PRICE]
用例3: 实际 [COMPLAINT]      预测 [COMPLAINT]

PRICE:     TP=2 (用例1,2)  FP=0  FN=0            → P=1.0  R=1.0  F1=1.0
FINANCE:   TP=0            FP=1 (用例1)  FN=1 (用例2)  → P=0.0  R=0.0  F1=0.0
COMPLAINT: TP=1            FP=0  FN=0            → P=1.0  R=1.0  F1=1.0

Macro F1 = (1.0 + 0.0 + 1.0) / 3 = 0.667
EMR     = 1/3 = 0.333  (只有用例 3 完全正确)
```

### 2. Slot 提取

按 slot name 匹配，value 精确匹配（字符串等值比较）。

```
预测: SET model=M8, SET budget=20万
标准: SET model=M8, SET budget=10万

model:  name 匹配 + value 匹配 → TP
budget: name 匹配但 value 不等 → FP（多预测了一个错值）+ FN（漏了标准值）
```

**Per-slot 指标**（和 sub_task 同样的公式）：

```
Slot Precision = TP / (TP + FP)   按 slot name
Slot Recall    = TP / (TP + FN)
Slot F1        = 2 * P * R / (P + R)
Macro Slot F1  = avg(Slot F1_i)
```

**Slot Exact Match**（补充）：

```
Slot EMR = 所有槽位 name + value 完全匹配的比例
```

**注意**：`lead_refused` 是推断槽位（用户没说"lead_refused"这个词），不在 Slot 评估范围内。只在 `slots` 字段中标注，不参与 slot 指标计算。Sub-task 维度的 `CONTACT_NO` 正确即可证明行为正确。

### 3. 整体评分卡片

```
==========================================
Multi-Intent 评估报告
==========================================
测试用例:  120 条

--- Sub-task ---
Macro Precision:  0.923
Macro Recall:     0.891
Macro F1:         0.907
Exact Match Rate: 0.783

--- Slot ---
Slot Macro F1:    0.856
Slot Exact Match: 0.742
==========================================
```

## 旧 Golden Set 兼容

旧 363 条标注的是 `IntentCategory`，可以通过 `_INTENT_TO_SUBTASKS` 映射自动转成 sub_tasks：

```python
# 旧标注 → 新格式
"purchase"    →  sub_tasks: ["PURCHASE", "LEAD_CAPTURE"]
"product_inq" →  sub_tasks: ["PRODUCT"]
"contact_give" → sub_tasks: ["LEAD_CAPTURE"]
```

但因旧数据不含 slot 标注，只能测 sub_task 维度。Slot 维度需要新标注。

## API 设计

```
POST /eval/multi
{
    "cases": [
        {"query": "M8什么配置", "sub_tasks": ["PRODUCT"], "slots": {"model": "M8"}},
        ...
    ]
}

Response:
{
    "total_cases": 120,
    "sub_task": {
        "macro_precision": 0.923,
        "macro_recall": 0.891,
        "macro_f1": 0.907,
        "exact_match_rate": 0.783,
        "per_sub_task": {
            "PRODUCT": {"precision": 0.95, "recall": 0.92, "f1": 0.93, "tp": 35, "fp": 2, "fn": 3},
            ...
        }
    },
    "slot": {
        "macro_f1": 0.856,
        "exact_match": 0.742,
        "per_slot": {
            "model": {"precision": 0.94, "recall": 0.91, "f1": 0.92, "tp": 30, "fp": 2, "fn": 3},
            ...
        }
    },
    "cases": [
        {
            "query": "M8什么配置",
            "expected_sub_tasks": ["PRODUCT"],
            "predicted_sub_tasks": ["PRODUCT"],
            "sub_task_ok": true,
            "expected_slots": {"model": "M8"},
            "predicted_slots": {"model": "M8"},
            "slot_ok": true
        },
        ...
    ]
}
```

## 实现文件

- `evaluation/multi_intent_evaluator.py` — 评估器核心逻辑
- `api/main.py` — 新增 `POST /eval/multi` 路由
- `tests/multi_intent_eval.jsonl` — 测试用例（未来由你编写）
