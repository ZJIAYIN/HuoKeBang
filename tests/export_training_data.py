"""
将 intent_annotated.jsonl 导出为 LLaMA-Factory / Unsloth 训练格式。

输出:
  tests/intent_train_alpaca.json    — Alpaca 格式（instruction/input/output）
  tests/intent_train_sharegpt.jsonl — ShareGPT 格式（messages 数组，含 history 占位）

使用方式:
  python tests/export_training_data.py

后续:
  1. 用 LLaMA-Factory 或 Unsloth 加载 intent_train_alpaca.json 训练
  2. 训练完后用 Ollama 部署推理
"""
import json
import pathlib
from collections import Counter
from typing import Any, Dict, List, Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════════════

INPUT_PATH = pathlib.Path(__file__).parent / "intent_annotated.jsonl"
OUTPUT_ALPACA = pathlib.Path(__file__).parent / "intent_train_alpaca.json"
OUTPUT_SHAREGPT = pathlib.Path(__file__).parent / "intent_train_sharegpt.jsonl"

# 合法标签集合（与 IntentCategory / Sentiment 枚举一致）
VALID_INTENTS = {
    "greeting", "product_inq", "price_inq", "purchase",
    "complaint", "contact_give", "contact_no", "contact_fix", "chitchat",
}
VALID_SENTIMENTS = {
    "positive", "neutral", "skeptical", "anxious", "negative",
}

# 系统指令（训练时拼接在 instruction 中）
SYSTEM_PROMPT = "你是一个专业的客服意图识别助手。请判断用户消息的意图和情绪，返回 JSON。"
INTENT_ZH = {
    "greeting": "打招呼",
    "product_inq": "咨询产品",
    "price_inq": "询问价格",
    "purchase": "购买意向",
    "complaint": "投诉反馈",
    "contact_give": "提供联系方式",
    "contact_no": "拒绝留资",
    "contact_fix": "更正联系方式",
    "chitchat": "闲聊",
}
SENTIMENT_ZH = {
    "positive": "正面",
    "neutral": "中性",
    "skeptical": "质疑",
    "anxious": "焦虑",
    "negative": "负面",
}


def _resolve_label(data: Dict[str, Any]) -> Dict[str, str]:
    """解析最终标注标签：优先取 expected_*，回退到 predicted_*。"""
    intent = data.get("expected_intent") or data.get("predicted_intent")
    sentiment = data.get("expected_sentiment") or data.get("predicted_sentiment")
    return {"intent": intent, "sentiment": sentiment}


def _validate_label(intent: str, sentiment: str) -> None:
    """验证标签是否在合法的意图和情绪集合中。"""
    if intent not in VALID_INTENTS:
        raise ValueError(f"非法意图标签: {intent}")
    if sentiment not in VALID_SENTIMENTS:
        raise ValueError(f"非法情绪标签: {sentiment}")


def _to_alpaca(data: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """转换为 Alpaca 格式。

    LLaMA-Factory 标准格式：
      {"instruction": "...", "input": "用户消息", "output": "意图和情绪"}
    """
    samples = []
    for item in data:
        query = item["query"]
        label = _resolve_label(item)
        _validate_label(label["intent"], label["sentiment"])

        # instruction: 系统指令 + 输出格式要求
        zh_i = INTENT_ZH.get(label["intent"], label["intent"])
        zh_s = SENTIMENT_ZH.get(label["sentiment"], label["sentiment"])

        samples.append({
            "instruction": SYSTEM_PROMPT,
            "input": query,
            "output": json.dumps({
                "intent": label["intent"],
                "intent_zh": zh_i,
                "sentiment": label["sentiment"],
                "sentiment_zh": zh_s,
            }, ensure_ascii=False),
        })
    return samples


def _to_sharegpt(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """转换为 ShareGPT 格式（用于支持多轮训练的框架）。

    每条样本构造简单的单轮对话，保留 persona 信息在 system 中。
    """
    samples = []
    for item in data:
        label = _resolve_label(item)
        _validate_label(label["intent"], label["sentiment"])

        zh_i = INTENT_ZH.get(label["intent"], label["intent"])
        zh_s = SENTIMENT_ZH.get(label["sentiment"], label["sentiment"])

        system = SYSTEM_PROMPT
        if item.get("persona"):
            system += f"\n用户画像：{item['persona']}"

        samples.append({
            "system": system,
            "conversations": [
                {"from": "human", "value": item["query"]},
                {
                    "from": "gpt",
                    "value": json.dumps({
                        "intent": label["intent"],
                        "intent_zh": zh_i,
                        "sentiment": label["sentiment"],
                        "sentiment_zh": zh_s,
                    }, ensure_ascii=False),
                },
            ],
        })
    return samples


def main() -> None:
    """主流程：读取标注数据 → 校验 → 导出 Alpaca + ShareGPT 格式。"""
    # 读取数据
    data: List[Dict[str, Any]] = []
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))

    print(f"加载标注数据: {len(data)} 条")

    # 统计标签来源
    from_expected = sum(1 for d in data if d.get("expected_intent") is not None)
    from_predicted = len(data) - from_expected
    print(f"  预期标签(expected): {from_expected}")
    print(f"  预测标签(predicted): {from_predicted}")

    # 统计最终标签分布
    from collections import Counter
    intent_dist: Counter = Counter()
    sentiment_dist: Counter = Counter()
    for d in data:
        label = _resolve_label(d)
        intent_dist[label["intent"]] += 1
        sentiment_dist[label["sentiment"]] += 1

    print(f"\n意图分布:")
    for k, v in sorted(intent_dist.items(), key=lambda x: -x[1]):
        print(f"  {k:15s} {v:3d} 条")
    print(f"\n情绪分布:")
    for k, v in sorted(sentiment_dist.items(), key=lambda x: -x[1]):
        print(f"  {k:15s} {v:3d} 条")

    # 导出 Alpaca 格式
    alpaca = _to_alpaca(data)
    with open(OUTPUT_ALPACA, "w", encoding="utf-8") as f:
        json.dump(alpaca, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Alpaca: {OUTPUT_ALPACA} ({len(alpaca)} 条)")

    # 导出 ShareGPT 格式
    sharegpt = _to_sharegpt(data)
    with open(OUTPUT_SHAREGPT, "w", encoding="utf-8") as f:
        for s in sharegpt:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"✅ ShareGPT: {OUTPUT_SHAREGPT} ({len(sharegpt)} 条)")

    # 打印一条样例
    print(f"\n--- Alpaca 样例 ---")
    print(json.dumps(alpaca[0], ensure_ascii=False, indent=2))

    print(f"\n下一步:")
    print(f"  方式 1 (推荐): 用 Unsloth 训练 → Ollama 部署")
    print(f"  方式 2: 用 LLaMA-Factory 训练 → Ollama 部署")
    print(f"  方式 3: 直接转 GGUF → Ollama 部署")
    print(f"\n  训练命令示例会在 README 更新，也可以现在告诉我你要用哪个框架")


if __name__ == "__main__":
    main()
