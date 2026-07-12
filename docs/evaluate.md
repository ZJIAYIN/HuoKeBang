# Multi-Intent 评估方案（简化版）

## 背景

旧架构每次只输出一个意图标签，评估用 accuracy + macro F1 即可。

新架构输出 multi-intent（sub_tasks 列表），但多意图本质上就是多个单意图的组合，
因此不需要搞复杂的 slot 级评估。

**简化原则**：每类意图独立算 F1（当作二分类），再算多意图集合的 Exact Match Rate。

## 评估维度

| 维度 | 是否评测 | 原因 |
|------|---------|------|
| sub_tasks（多标签）| ✅ | 核心能力——Planner 是否正确识别了用户的多个意图 |
| slot_ops（槽位提取）| ❌ | **已移除**——槽位覆盖率意义不大，多意图本身才是核心指标 |
| primary_intent | ❌ | sub_tasks 覆盖了它，是冗余信息 |
| emotion | ❌ | 只影响 LEAD_CAPTURE 一个 Skill 的开关，不值得单独上指标 |

## 测试用例格式

每行一条 JSON，每个用例独立测试。

```jsonl
# tests/test.jsonl
{"query": "M8什么配置",                      "sub_tasks": ["PRODUCT"]}
{"query": "你好呀，我想了解一下新款M9的落地价和配置", "sub_tasks": ["GREETING", "PRODUCT", "PRICE"]}
{"query": "我不想留联系方式，只想了解价格",  "sub_tasks": ["CONTACT_NO", "PRICE"]}
```

注意：
- `slots` 字段不再参与评估，如果旧数据中有此字段会被自动忽略。
- 不测多轮对话（太长、不可控）。
- 不测 context_slots / history 继承。

## 评估指标

### 1. 每类意图独立二分类

每个 sub_task（GREETING / PRODUCT / PRICE / COMPLAINT / LEAD_CAPTURE / CONTACT_NO / CONTACT_FIX / PURCHASE）独立算混淆矩阵，再做宏平均。

```
               实际有    实际无
预测有           TP       FP
预测无           FN       TN
```

**Per-intent 指标**：

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

### 2. Exact Match Rate（补充指标）

```
EMR = count(sub_tasks 集合完全一致) / total_cases
```

sub_task 集合完全一致才算 1，漏一个或多一个都算 0。比 macro-F1 更严，两者互补展示。

### 3. 整体评分卡片

```
==========================================
Multi-Intent 评估报告
==========================================
测试用例:  120 条

Macro Precision:  0.923
Macro Recall:     0.891
Macro F1:         0.907
Exact Match Rate: 0.783

--- 每类意图 ---
GREETING:    P=0.97  R=0.95  F1=0.96  tp=38 fp=1  fn=2
PRICE:       P=0.92  R=0.90  F1=0.91  tp=72 fp=6  fn=8
PRODUCT:     P=0.95  R=0.88  F1=0.91  tp=44 fp=2  fn=6
COMPLAINT:   P=0.88  R=0.85  F1=0.86  tp=17 fp=2  fn=3
LEAD_CAPTURE: P=0.90  R=0.92  F1=0.91  tp=23 fp=3  fn=2
CONTACT_NO:  P=0.93  R=1.00  F1=0.96  tp=14 fp=1  fn=0
PURCHASE:    P=0.87  R=0.83  F1=0.85  tp=20 fp=3  fn=4
==========================================
```

## API 设计

```
POST /eval/multi
{
    "cases": [
        {"query": "M8什么配置", "sub_tasks": ["PRODUCT"]},
        ...
    ]
}

Response:
{
    "total_cases": 120,
    "macro_avg": {
        "precision": 0.923,
        "recall": 0.891,
        "f1": 0.907
    },
    "exact_match_rate": 0.783,
    "per_intent": {
        "PRICE": {"precision": 0.92, "recall": 0.90, "f1": 0.91, "tp": 72, "fp": 6, "fn": 8},
        "PRODUCT": {"precision": 0.95, "recall": 0.88, "f1": 0.91, "tp": 44, "fp": 2, "fn": 6},
        ...
    },
    "cases": [
        {
            "query": "M8什么配置",
            "expected_sub_tasks": ["PRODUCT"],
            "predicted_sub_tasks": ["PRODUCT"],
            "sub_task_ok": true,
            "latency_ms": 12.3
        },
        ...
    ]
}
```

## 实现文件

- `evaluation/multi_intent_evaluator.py` — 评估器核心逻辑
- `api/main.py` — 路由 `POST /eval/multi`
- `tests/test.jsonl` + `tests/test2.jsonl` — 测试用例

## 旧 Golden Set 兼容

旧 363 条标注的是 `IntentCategory`，可以通过 `_INTENT_TO_SUBTASKS` 映射自动转成 sub_tasks：

```python
"purchase"     → sub_tasks: ["PURCHASE", "LEAD_CAPTURE"]
"product_inq"  → sub_tasks: ["PRODUCT"]
"contact_give" → sub_tasks: ["LEAD_CAPTURE"]
```

因旧数据不含 slot 标注且 slot 评估已移除，可完整兼容。
