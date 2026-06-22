"""
意图预标注脚本 —— 用当前 IntentRecognizer 对 raw query 池做预标注。

用 LLM few-shot 跑一遍 → 输出 predicted_intent / predicted_sentiment / confidence
标出疑似 bad case（predict ≠ target 或 置信度 < 阈值）→ 给人 review

使用方式：
  python tests/annotate_intent.py

输入: tests/intent_raw.jsonl
输出:
  tests/intent_annotated.jsonl  全量预标注结果（expected_* 留空，给人填）
  tests/intent_needs_review.jsonl  需要人 review 的条目列表
"""
import asyncio
import json
import os
import pathlib
import sys
from typing import Any, Dict, List

_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv()

from core.intent_recognizer import IntentRecognizer

# 置信度低于此值标记为 needs_review
CONFIDENCE_THRESHOLD = 0.5


def _load_raw(path: str) -> List[Dict[str, Any]]:
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


async def annotate(recognizer: IntentRecognizer,
                   cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """对每条 query 做预标注（无历史上下文，纯 query 分类）。"""
    results = []
    total = len(cases)

    for i, case in enumerate(cases):
        query = case["query"]

        result = await recognizer.recognize(query)  # 无 history

        predicted_intent = result.intent.value
        predicted_sentiment = result.sentiment.value
        confidence = round(result.confidence, 4)

        needs_review = (
            predicted_intent != case["target_intent"] or
            predicted_sentiment != case["target_sentiment"] or
            confidence < CONFIDENCE_THRESHOLD
        )

        results.append({
            "persona":            case["persona"],
            "query":              query,
            "split":              case.get("split", "train"),
            "target_intent":      case["target_intent"],
            "target_sentiment":   case["target_sentiment"],
            "predicted_intent":   predicted_intent,
            "predicted_sentiment": predicted_sentiment,
            "confidence":         confidence,
            "needs_review":       needs_review,
            "expected_intent":    None,
            "expected_sentiment": None,
        })

        if (i + 1) % 20 == 0 or (i + 1) == total:
            review_count = sum(1 for r in results if r["needs_review"])
            print(f"  进度: {i+1}/{total}  需 review: {review_count}")

    # 清缓存避免后续重跑时的干扰
    recognizer._cache.clear()
    return results


async def main():
    api_key = "sk-92f09f3ada494ecd8390763ff293906b"
    if not api_key:
        print("❌ 请设置 ANTHROPIC_API_KEY")
        return
    base_url = "https://api.deepseek.com/anthropic"
    model = "deepseek-chat"

    recognizer = IntentRecognizer(api_key=api_key, base_url=base_url, model=model)

    in_path = pathlib.Path(_ROOT) / "tests" / "intent_raw.jsonl"
    if not in_path.exists():
        print(f"❌ 数据集不存在: {in_path}")
        print("   请先运行: python tests/generate_intent_dataset.py")
        return

    print(f"数据集: {in_path}")
    print(f"模型: {model}\n")

    cases = _load_raw(str(in_path))
    print(f"加载 {len(cases)} 条\n")

    results = await annotate(recognizer, cases)

    # ---- 全量预标注结果 ----
    out_all = pathlib.Path(_ROOT) / "tests" / "intent_annotated.jsonl"
    with open(out_all, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ---- 需要人 review 的 ----
    out_review = pathlib.Path(_ROOT) / "tests" / "intent_needs_review.jsonl"
    review_items = [r for r in results if r["needs_review"]]
    with open(out_review, "w", encoding="utf-8") as f:
        for r in review_items:
            f.write(json.dumps({
                "persona":            r["persona"],
                "query":              r["query"],
                "split":              r["split"],
                "target_intent":      r["target_intent"],
                "target_sentiment":   r["target_sentiment"],
                "predicted_intent":   r["predicted_intent"],
                "predicted_sentiment": r["predicted_sentiment"],
                "confidence":         r["confidence"],
                "expected_intent":    None,
                "expected_sentiment": None,
            }, ensure_ascii=False) + "\n")

    # 统计
    total = len(results)
    review_count = len(review_items)
    target_match = sum(1 for r in results
                       if r["predicted_intent"] == r["target_intent"]
                       and r["predicted_sentiment"] == r["target_sentiment"])

    print(f"\n全量预标注: {out_all}")
    print(f"需 review:   {out_review}")
    print(f"\n=== 统计 ===")
    print(f"总数:           {total}")
    print(f"predict==target: {target_match}/{total} ({target_match/total:.1%})")
    print(f"需 review:       {review_count}/{total} ({review_count/total:.1%})")
    print(f"  其中置信度<{CONFIDENCE_THRESHOLD}: {sum(1 for r in review_items if r['confidence'] < CONFIDENCE_THRESHOLD)}")
    print(f"\n下一步: 打开 intent_needs_review.jsonl，逐条填 expected_intent / expected_sentiment")


if __name__ == "__main__":
    asyncio.run(main())
