"""
简化版 Multi-Intent 评估器

评估理念：
  多意图本质上就是多个单意图的组合。因此不搞复杂的 slot 级评估，
  只对每个意图类型算独立 F1（当作二分类），再算多意图集合的 Exact Match Rate。

评估维度：
  1. 每类意图的 Precision / Recall / F1（Macro 平均 → Macro F1）
  2. 多意图集合的 Exact Match Rate（预测的 sub_task 集合是否与标准完全一致）

用法：
    evaluator = MultiIntentEvaluator(planner)
    cases = [{"query": "M8多少钱", "sub_tasks": ["PRICE"]}, ...]
    report, details = await evaluator.eval(cases)
"""
import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.intent_recognizer import Planner, PlannerOutput

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PerLabelMetrics:
    """单标签的混淆矩阵和派生指标。"""
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class CaseResult:
    """单条用例的详细结果。"""
    query:               str
    expected_sub_tasks:  List[str]
    predicted_sub_tasks: List[str]
    sub_task_ok:         bool            # 集合完全匹配？
    latency_ms:          float = 0.0


@dataclass
class IntentEvalReport:
    """评估报告——核心指标。"""
    total_cases:         int                     # 总用例数

    # 每类意图的指标
    per_intent:          Dict[str, PerLabelMetrics] = field(default_factory=dict)
    intent_labels:       List[str] = field(default_factory=list)   # 意图名称列表

    # 宏观指标
    macro_precision:     float = 0.0
    macro_recall:        float = 0.0
    macro_f1:            float = 0.0

    # 精确匹配率（sub_task 集合完全一致）
    exact_match_rate:    float = 0.0

    # 耗时
    total_latency_ms:    float = 0.0

    # 详细结果
    case_details:        List[CaseResult] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# 评估器
# ═══════════════════════════════════════════════════════════════════════════════

class MultiIntentEvaluator:
    """
    简化版 Multi-Intent 评估器。

    测试用例格式：
        {"query": "M8多少钱", "sub_tasks": ["PRICE"]}

    注意：
        - 旧版格式中的 "slots" 字段会被忽略，不再参与评估。
        - 每类意图独立算二分类指标，最后取 Macro 平均。
    """

    def __init__(self, planner: Planner):
        self._planner = planner

    async def eval(
        self,
        cases: List[Dict[str, Any]],
        *,
        detail: bool = True,
    ) -> Tuple[IntentEvalReport, Optional[List[CaseResult]]]:
        """
        运行评估。

        参数：
            cases:  测试用例列表，每条的格式：
                    {"query": "用户问题", "sub_tasks": ["PRICE", "GREETING"]}
                    slots 字段可选，会被忽略。
            detail: 是否返回每条用例的明细

        返回：
            (report, case_details)
        """
        # ── 初始化混淆矩阵字典 ──
        per_intent_cm: Dict[str, PerLabelMetrics] = {}

        total_latency = 0.0
        exact_match_ok = 0
        case_details: List[CaseResult] = []

        # ── 主循环：逐条预测 + 统计 ──
        for c in cases:
            if "query" not in c:
                logger.warning(f"跳过无效用例（缺少 query）: {str(c)[:60]}")
                continue

            query = c["query"]
            expected = set(c.get("sub_tasks", []))

            # 调用 Planner 预测
            t0 = time.monotonic()
            output: PlannerOutput = await self._planner.plan(message=query)
            lat = (time.monotonic() - t0) * 1000
            total_latency += lat

            predicted = set(output.sub_tasks)

            # 更新混淆矩阵：对并集中的每个意图标签统计 TP/FP/FN
            all_labels = expected | predicted
            for label in all_labels:
                if label not in per_intent_cm:
                    per_intent_cm[label] = PerLabelMetrics()

                if label in expected and label in predicted:
                    per_intent_cm[label].tp += 1
                elif label in predicted and label not in expected:
                    per_intent_cm[label].fp += 1
                elif label not in predicted and label in expected:
                    per_intent_cm[label].fn += 1
                # else: 都不出现 → TN（不关心）

            # Exact Match：集合是否完全一致
            sub_ok = expected == predicted
            if sub_ok:
                exact_match_ok += 1

            # 记录明细
            if detail:
                case_details.append(CaseResult(
                    query=query,
                    expected_sub_tasks=sorted(expected),
                    predicted_sub_tasks=output.sub_tasks,
                    sub_task_ok=sub_ok,
                    latency_ms=lat,
                ))

        # ── 汇总：算每类意图的指标 + Macro 平均 ──
        N = len(cases)
        intent_labels = sorted(per_intent_cm.keys())
        f1s = [cm.f1 for cm in per_intent_cm.values()]

        report = IntentEvalReport(
            total_cases=N,
            per_intent=per_intent_cm,
            intent_labels=intent_labels,
            macro_precision=statistics.mean(
                [cm.precision for cm in per_intent_cm.values()]
            ) if per_intent_cm else 0.0,
            macro_recall=statistics.mean(
                [cm.recall for cm in per_intent_cm.values()]
            ) if per_intent_cm else 0.0,
            macro_f1=statistics.mean(f1s) if f1s else 0.0,
            exact_match_rate=exact_match_ok / N if N else 0.0,
            total_latency_ms=total_latency,
            case_details=case_details if detail else [],
        )

        return report, (case_details if detail else None)

    # ── 序列化辅助 ──

    @staticmethod
    def report_to_dict(report: IntentEvalReport, *, include_details: bool = True) -> Dict[str, Any]:
        """把 IntentEvalReport 转成纯 dict，便于 JSON 序列化。"""
        def _cm_dict(cm: PerLabelMetrics) -> Dict[str, Any]:
            return {
                "tp": cm.tp,
                "fp": cm.fp,
                "fn": cm.fn,
                "precision": round(cm.precision, 4),
                "recall": round(cm.recall, 4),
                "f1": round(cm.f1, 4),
            }

        d = {
            "total_cases": report.total_cases,
            "macro_avg": {
                "precision": round(report.macro_precision, 4),
                "recall": round(report.macro_recall, 4),
                "f1": round(report.macro_f1, 4),
            },
            "exact_match_rate": round(report.exact_match_rate, 4),
            "per_intent": {
                k: _cm_dict(v) for k, v in report.per_intent.items()
            },
            "intent_labels": report.intent_labels,
            "total_latency_ms": round(report.total_latency_ms, 1),
        }

        if include_details and report.case_details:
            d["cases"] = [
                {
                    "query": cd.query,
                    "expected_sub_tasks": cd.expected_sub_tasks,
                    "predicted_sub_tasks": cd.predicted_sub_tasks,
                    "sub_task_ok": cd.sub_task_ok,
                    "latency_ms": round(cd.latency_ms, 1),
                }
                for cd in report.case_details
            ]

        return d
