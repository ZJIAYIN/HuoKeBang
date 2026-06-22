"""
本地意图识别测试脚本 — 训练完成后用。

使用方式:
  # 先确保 Ollama 已部署模型
  # ollama create echomind-intent -f tests/Modelfile
  python tests/test_local_intent.py

输出: 测试集中所有条目的预测结果 + 准确率统计
"""
import json
import pathlib
from typing import Any, Dict, List

# ── 配置 ──────────────────────────────────────────────────────────────────────

TEST_QUERIES = [
    # (query, expected_intent)
    ("你好，在吗", "greeting"),
    ("你们这个产品多少钱", "price_inq"),
    ("太贵了，能便宜点吗", "price_inq"),
    ("我想下单", "purchase"),
    ("我要投诉你们客服", "complaint"),
    ("我的手机号是13800138000", "contact_give"),
    ("暂时不需要，谢谢", "contact_no"),
    ("你们是不是机器人", "chitchat"),
    ("这个功能到底有没有用啊", "product_inq"),
]


def test_ollama(queries: List[tuple]) -> None:
    """用本地 Ollama 模型测试意图识别效果。"""
    import ollama

    model_name = "echomind-intent"
    print(f"测试模型: {model_name}\n")

    correct = 0
    for i, (query, expected) in enumerate(queries, 1):
        try:
            resp = ollama.chat(
                model=model_name,
                messages=[{"role": "user", "content": query}],
                options={"temperature": 0.1, "max_tokens": 128},
            )
            reply = resp["message"]["content"]
            print(f"[{i}] Q: {query}")

            # 尝试解析 JSON
            try:
                result = json.loads(reply)
                predicted = result.get("intent", "?")
            except json.JSONDecodeError:
                predicted = reply.strip()

            ok = "✅" if predicted == expected else "❌"
            print(f"    predict: {predicted}  expected: {expected}  {ok}")

            if predicted == expected:
                correct += 1
            print()

        except Exception as e:
            print(f"[{i}] Q: {query}  → 调用失败: {e}\n")

    total = len(queries)
    print(f"正确率: {correct}/{total} = {correct / total:.1%}")


def test_vs_remote(queries: List[tuple]) -> None:
    """对比本地模型 vs 远程 Few-shot LLM 的效果。"""
    import os
    import sys
    _ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

    from core.intent_recognizer import IntentRecognizer

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    recognizer = IntentRecognizer(api_key=api_key)

    print(f"[远程 Few-shot LLM 对比]\n")
    correct = 0
    for i, (query, expected) in enumerate(queries, 1):
        import asyncio
        result = asyncio.run(recognizer.recognize(query))
        predicted = result.intent.value
        ok = "✅" if predicted == expected else "❌"
        print(f"[{i}] {query}")
        print(f"    remote: {predicted}  expected: {expected}  {ok}")
        if predicted == expected:
            correct += 1
        print()

    total = len(queries)
    print(f"远程 LLM 正确率: {correct}/{total} = {correct / total:.1%}")


def main() -> None:
    """主函数。"""
    print("请先确保已运行: ollama create echomind-intent -f tests/Modelfile")
    print("=" * 50)
    test_ollama(TEST_QUERIES)


if __name__ == "__main__":
    main()
