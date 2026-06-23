"""
Multi-Intent 评估器（MultiIntentEvaluator）

评估维度：
  1. Sub-task 多标签分类 → 每个 sub_task 独立 Precision/Recall/F1 → Macro F1 + Exact Match Rate
  2. Slot 提取 → 每个 slot name 独立 Precision/Recall/F1 → Macro F1 + Exact Match

用法：
    evaluator = MultiIntentEvaluator(planner)
    with open("tests/test.jsonl") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    report, detail = await evaluator.eval(cases)
"""
import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.intent_recognizer import Planner, PlannerOutput

logger = logging.getLogger(__name__)

# Slot 评估黑名单 —— 排除推断槽位（不在用户消息中明确出现）
_SLOT_EVAL_SKIP = {"lead_refused"}


# ═══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MultiIntentCase:
    """单条测试用例。"""
    query:     str
    sub_tasks: List[str]
    slots:     Dict[str, Any] = field(default_factory=dict)


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
    query:              str
    expected_sub_tasks: List[str]
    predicted_sub_tasks: List[str]
    sub_task_ok:        bool                    # 集合完全匹配？
    expected_slots:     Dict[str, Any]
    predicted_slots:    Dict[str, Any]
    slot_ok:            bool                    # 所有槽位完全匹配？
    latency_ms:         float = 0.0


@dataclass
class MultiEvalReport:
    """评估报告——核心指标。"""
    total_cases:          int                     # 总用例数

    # Sub-task 维度
    sub_task_macro_p:     float = 0.0
    sub_task_macro_r:     float = 0.0
    sub_task_macro_f1:    float = 0.0
    sub_task_exact_match: float = 0.0            # 集合完全匹配率
    sub_task_per_label:   Dict[str, PerLabelMetrics] = field(default_factory=dict)
    sub_task_labels:      List[str] = field(default_factory=list)

    # Slot 维度
    slot_macro_f1:        float = 0.0
    slot_exact_match:     float = 0.0
    slot_per_label:       Dict[str, PerLabelMetrics] = field(default_factory=dict)
    slot_labels:          List[str] = field(default_factory=list)

    # 耗时
    total_latency_ms:     float = 0.0

    # 详细结果（selective 时为 True 才带，减少传输）
    case_details:         List[CaseResult] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# 评估器
# ═══════════════════════════════════════════════════════════════════════════════

class MultiIntentEvaluator:
    """
    Multi-Intent 评估器。

    用法：
        planner = Planner(...)
        evaluator = MultiIntentEvaluator(planner)
        report, details = await evaluator.eval(cases)
    """

    def __init__(self, planner: Planner):
        self._planner = planner

    async def eval(
        self,
        cases: List[Dict[str, Any]],
        *,
        detail: bool = True,           # True=返回每条用例的明细（用于调试），False=只返回汇总指标
    ) -> tuple[MultiEvalReport, Optional[List[CaseResult]]]:
        """
        运行评估。

        参数：
            cases:  测试用例列表，每条的格式：
                    {"query": "用户问题", "sub_tasks": ["PRICE"], "slots": {"model": "M8"}}
            detail: 是否返回每条用例的明细

        返回：
            (report, case_details)
            - report:       汇总指标（Macro F1、Exact Match Rate 等）
            - case_details: 每条用例的明细（detail=False 时为 None）
        """

        # ──────────────────────────────────────────────────────────────────
        # 准备工作：建好空的混淆矩阵字典
        # sub_task_cm = {"PRICE": PerLabelMetrics(tp=0, fp=0, fn=0), ...}
        # slot_cm     = {"model": PerLabelMetrics(tp=0, fp=0, fn=0), ...}
        # 这两个字典随着逐条预测一边预测一边往里加新 label
        # ──────────────────────────────────────────────────────────────────
        sub_task_cm: Dict[str, PerLabelMetrics] = {}
        slot_cm: Dict[str, PerLabelMetrics] = {}

        # 计数器
        total_latency = 0.0       # 所有预测的总耗时（毫秒）
        exact_match_ok = 0        # sub_task 完全正确的用例数
        slot_exact_ok = 0         # slot 完全正确的用例数
        case_details: List[CaseResult] = []   # 每条用例的明细

        # ──────────────────────────────────────────────────────────────────
        # 主循环：逐条预测 + 逐条算 TP/FP/FN
        # ──────────────────────────────────────────────────────────────────
        for c in cases:
            # 跳过没有 query 的无效行
            if "query" not in c:
                logger.warning(f"跳过无效用例（缺少 query）: {str(c)[:60]}")
                continue

            # ---------- 2a. 解析当前用例 ----------
            mc = MultiIntentCase(
                query=c["query"],
                sub_tasks=c.get("sub_tasks", []),         # 标准答案：应该有哪些 sub_task
                slots={k: v for k, v in c.get("slots", {}).items()
                       if k not in _SLOT_EVAL_SKIP},     # 标准答案：应该提取哪些槽位
            )
            # _SLOT_EVAL_SKIP = {"lead_refused"} 不参与评估
            # 因为 lead_refused 是推断槽位（用户不会直接说"lead_refused"这个词），
            # 只看 CONTACT_NO 这个 sub_task 对不对就够了

            # ---------- 2b. 调用 Planner 预测 ----------
            t0 = time.monotonic()
            output: PlannerOutput = await self._planner.plan(message=mc.query)
            lat = (time.monotonic() - t0) * 1000          # 单条耗时（毫秒）
            total_latency += lat

            # Planner 输出的原始结果
            predicted_sub = output.sub_tasks               # 预测的 sub_task 列表

            # 提取预测的槽位：只取 SET 操作，跳过黑名单
            predicted_slots: Dict[str, Any] = {}
            for op in output.slot_ops:
                if op.slot not in _SLOT_EVAL_SKIP and op.op.value == "SET":
                    predicted_slots[op.slot] = op.value

            # ──────────────────────────────────────────────────────────────
            # 3. Sub-task 多标签分类
            #
            # 核心逻辑：对于每一个 sub_task 名字（比如 PRICE），看它在
            # 标准答案和预测结果中分别是否出现，然后决定 TP/FP/FN。
            #
            #  expected_sub = {"PRICE", "FINANCE"}     ← 标准答案
            #  predicted_sub_set = {"PRICE"}            ← 预测结果
            #
            #  all_sub_labels = {"PRICE", "FINANCE"}    ← 两者取并集
            #
            #  PRICE:   在 expected 且在 predicted → TP
            #  FINANCE: 在 expected 但不在 predicted → FN
            #  （如果 Planner 多猜了一个 PRODUCT，它也会出现在并集里，
            #    然后走 FP 分支）
            # ──────────────────────────────────────────────────────────────

            # 转成 set 方便比较
            expected_sub = set(mc.sub_tasks)
            predicted_sub_set = set(predicted_sub)

            # 并集 = 标准里有的 + 预测里有的（确保不遗漏 FP）
            all_sub_labels = expected_sub | predicted_sub_set

            for label in all_sub_labels:
                # 首次遇到这个 label 时，初始化混淆矩阵
                if label not in sub_task_cm:
                    sub_task_cm[label] = PerLabelMetrics()

                # 判断 TP / FP / FN
                if label in expected_sub and label in predicted_sub_set:
                    sub_task_cm[label].tp += 1            # 该出现且出现了 → 正确
                elif label in predicted_sub_set and label not in expected_sub:
                    sub_task_cm[label].fp += 1            # 不该出现但出现了 → 误报
                elif label not in predicted_sub_set and label in expected_sub:
                    sub_task_cm[label].fn += 1            # 该出现但没出现 → 漏报
                # else: 都不出现 → TN（不关心，不统计）

            # Exact Match：sub_task 集合完全一致？
            sub_ok = expected_sub == predicted_sub_set
            if sub_ok:
                exact_match_ok += 1

            # ──────────────────────────────────────────────────────────────
            # 4. Slot 提取评估
            #
            # 逻辑和 sub_task 一样，但比较的是"槽位 name + value 都相等"
            #
            #  expected_slots = {"model": "M8"}
            #  predicted_slots = {"model": "M8", "budget": "20万"}
            #
            #  all_slot_labels = {"model", "budget"}  ← 并集
            #
            #  model:  标准值=M8 预测值=M8  → TP
            #  budget: 标准没有 预测有     → FP
            # ──────────────────────────────────────────────────────────────

            expected_slots = mc.slots
            all_slot_labels = set(expected_slots.keys()) | set(predicted_slots.keys())

            slot_ok = True     # 这条用例所有 slot 都正确？
            for label in all_slot_labels:
                # 首次遇到这个 slot 名，初始化
                if label not in slot_cm:
                    slot_cm[label] = PerLabelMetrics()

                ev = expected_slots.get(label)    # 标准值
                pv = predicted_slots.get(label)   # 预测值

                # TP: 标准有值，预测有值，且相等
                if ev is not None and pv is not None and ev == pv:
                    slot_cm[label].tp += 1
                # FP: 预测有值，但（标准没这个值 或 值不对）
                elif pv is not None and (ev is None or pv != ev):
                    slot_cm[label].fp += 1
                    slot_ok = False
                # FN: 标准有值，但预测没有
                elif ev is not None and pv is None:
                    slot_cm[label].fn += 1
                    slot_ok = False
                # 值不匹配：标准有且预测有但值不一样 → 既是 FP 也是 FN
                elif ev is not None and pv is not None and ev != pv:
                    slot_cm[label].fn += 1
                    slot_cm[label].fp += 1
                    slot_ok = False
                # 都没有 → TN，跳过

            if slot_ok:
                slot_exact_ok += 1

            # ---------- 5. 记录单条明细（detail=True 时） ----------
            if detail:
                case_details.append(CaseResult(
                    query=mc.query,
                    expected_sub_tasks=mc.sub_tasks,       # 应该有哪些 sub_task
                    predicted_sub_tasks=predicted_sub,     # 实际预测了哪些
                    sub_task_ok=sub_ok,                    # 集合完全一致？
                    expected_slots=expected_slots,          # 应该提取哪些槽位
                    predicted_slots=predicted_slots,        # 实际提取了哪些
                    slot_ok=slot_ok,                        # 所有槽位值都对？
                    latency_ms=lat,                         # 本条耗时
                ))

        # ──────────────────────────────────────────────────────────────────
        # 6. 汇总：从混淆矩阵算出最终指标
        #
        #  Macro Precision = 所有 label 的 precision 取算术平均
        #  Macro Recall    = 所有 label 的 recall 取算术平均
        #  Macro F1        = 所有 label 的 F1 取算术平均
        #
        #  Exact Match Rate = 集合完全一致的用例数 / 总用例数
        # ──────────────────────────────────────────────────────────────────
        N = len(cases)
        sub_task_labels = sorted(sub_task_cm.keys())   # 所有出现过的 sub_task 名字
        slot_labels = sorted(slot_cm.keys())            # 所有出现过的 slot 名字
        sub_task_f1s = [cm.f1 for cm in sub_task_cm.values()]   # 每个 sub_task 的 F1
        slot_f1s = [cm.f1 for cm in slot_cm.values()]           # 每个 slot 的 F1

        report = MultiEvalReport(
            total_cases=N,                                 # 总共多少条用例

            # ── sub_task 指标 ──
            sub_task_macro_p=statistics.mean(              # Macro Precision
                [cm.precision for cm in sub_task_cm.values()]
            ) if sub_task_cm else 0.0,
            sub_task_macro_r=statistics.mean(              # Macro Recall
                [cm.recall for cm in sub_task_cm.values()]
            ) if sub_task_cm else 0.0,
            sub_task_macro_f1=statistics.mean(             # Macro F1
                sub_task_f1s
            ) if sub_task_f1s else 0.0,
            sub_task_exact_match=exact_match_ok / N if N else 0.0,  # Exact Match Rate
            sub_task_per_label=sub_task_cm,                # 每个 sub_task 的混淆矩阵
            sub_task_labels=sub_task_labels,               # sub_task 名字列表

            # ── slot 指标 ──
            slot_macro_f1=statistics.mean(                 # Slot Macro F1
                slot_f1s
            ) if slot_f1s else 0.0,
            slot_exact_match=slot_exact_ok / N if N else 0.0,      # Slot Exact Match Rate
            slot_per_label=slot_cm,                        # 每个 slot 的混淆矩阵
            slot_labels=slot_labels,                       # slot 名字列表

            total_latency_ms=total_latency,                # 所有预测总耗时
            case_details=case_details if detail else [],   # 每条用例的详细结果
        )

        return report, (case_details if detail else None)

    # ── 序列化辅助（把 dataclass 转 dict，便于 JSON 返回）────────────

    @staticmethod
    def report_to_dict(report: MultiEvalReport, *, include_details: bool = True) -> Dict[str, Any]:
        """把 MultiEvalReport 转成纯 dict，可直接 JSON 序列化。"""
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
            "sub_task": {
                "macro_precision": round(report.sub_task_macro_p, 4),
                "macro_recall": round(report.sub_task_macro_r, 4),
                "macro_f1": round(report.sub_task_macro_f1, 4),
                "exact_match_rate": round(report.sub_task_exact_match, 4),
                "labels": report.sub_task_labels,
                "per_label": {
                    k: _cm_dict(v) for k, v in report.sub_task_per_label.items()
                },
            },
            "slot": {
                "macro_f1": round(report.slot_macro_f1, 4),
                "exact_match_rate": round(report.slot_exact_match, 4),
                "labels": report.slot_labels,
                "per_label": {
                    k: _cm_dict(v) for k, v in report.slot_per_label.items()
                },
            },
            "total_latency_ms": round(report.total_latency_ms, 1),
        }

        if include_details and report.case_details:
            d["cases"] = [
                {
                    "query": cd.query,
                    "expected_sub_tasks": cd.expected_sub_tasks,
                    "predicted_sub_tasks": cd.predicted_sub_tasks,
                    "sub_task_ok": cd.sub_task_ok,
                    "expected_slots": cd.expected_slots,
                    "predicted_slots": cd.predicted_slots,
                    "slot_ok": cd.slot_ok,
                    "latency_ms": round(cd.latency_ms, 1),
                }
                for cd in report.case_details
            ]

        return d
