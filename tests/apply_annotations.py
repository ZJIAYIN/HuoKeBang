"""
Apply human-annotated expected_intent and expected_sentiment to the intent dataset.

Usage:
  python tests/apply_annotations.py
"""

import json
import pathlib
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())

# ═══════════════════════════════════════════════════════════════════════════════
# Human annotations: index -> (expected_intent, expected_sentiment)
#
# Guidelines:
# - Intent = PRIMARY communication goal of the query
# - Mixed greeting + content → content wins
# - "嗯"/"哦"/"好" single chars → chitchat/neutral
# - Strong curses/anger → negative
# - Doubt/questioning credibility → skeptical
# - Worry about money/risk → anxious
# ═══════════════════════════════════════════════════════════════════════════════

ANNOTATIONS: Dict[int, Tuple[str, str]] = {
    # === target=greeting/* ===
    0:  ("product_inq",  "positive"),
    1:  ("product_inq",  "positive"),
    2:  ("product_inq",  "skeptical"),
    3:  ("greeting",     "skeptical"),
    4:  ("chitchat",     "neutral"),
    5:  ("product_inq",  "positive"),
    6:  ("product_inq",  "positive"),
    7:  ("price_inq",    "anxious"),
    8:  ("price_inq",    "positive"),
    9:  ("purchase",     "positive"),
    10: ("price_inq",    "neutral"),
    11: ("product_inq",  "skeptical"),
    12: ("product_inq",  "neutral"),
    13: ("product_inq",  "skeptical"),
    14: ("greeting",     "positive"),
    15: ("product_inq",  "neutral"),
    16: ("price_inq",    "neutral"),
    17: ("product_inq",  "neutral"),
    18: ("product_inq",  "neutral"),
    19: ("price_inq",    "neutral"),

    # === target=product_inq/* ===
    20: ("product_inq",  "skeptical"),
    21: ("chitchat",     "neutral"),
    22: ("product_inq",  "positive"),
    23: ("product_inq",  "skeptical"),
    24: ("price_inq",    "positive"),
    25: ("price_inq",    "anxious"),
    26: ("price_inq",    "neutral"),
    27: ("price_inq",    "skeptical"),
    28: ("price_inq",    "neutral"),
    29: ("product_inq",  "anxious"),
    30: ("product_inq",  "skeptical"),
    31: ("price_inq",    "skeptical"),
    32: ("chitchat",     "neutral"),
    33: ("product_inq",  "anxious"),
    34: ("complaint",    "skeptical"),
    35: ("complaint",    "skeptical"),
    36: ("product_inq",  "neutral"),
    37: ("price_inq",    "skeptical"),
    38: ("complaint",    "negative"),
    39: ("product_inq",  "anxious"),
    40: ("chitchat",     "neutral"),
    41: ("complaint",    "negative"),
    42: ("complaint",    "skeptical"),
    43: ("price_inq",    "positive"),
    44: ("product_inq",  "anxious"),
    45: ("product_inq",  "neutral"),
    46: ("price_inq",    "anxious"),
    47: ("price_inq",    "neutral"),
    48: ("product_inq",  "skeptical"),
    49: ("price_inq",    "positive"),
    50: ("product_inq",  "neutral"),
    51: ("chitchat",     "neutral"),
    52: ("product_inq",  "positive"),
    53: ("product_inq",  "positive"),
    54: ("product_inq",  "skeptical"),
    55: ("purchase",     "positive"),

    # === target=price_inq/* ===
    56: ("price_inq",    "anxious"),
    57: ("price_inq",    "positive"),
    58: ("price_inq",    "positive"),
    59: ("price_inq",    "anxious"),
    60: ("chitchat",     "neutral"),
    61: ("price_inq",    "skeptical"),
    62: ("price_inq",    "anxious"),
    63: ("price_inq",    "skeptical"),
    64: ("price_inq",    "skeptical"),
    65: ("price_inq",    "anxious"),
    66: ("complaint",    "skeptical"),
    67: ("price_inq",    "anxious"),
    68: ("chitchat",     "neutral"),
    69: ("price_inq",    "negative"),
    70: ("price_inq",    "skeptical"),
    71: ("chitchat",     "neutral"),
    72: ("price_inq",    "anxious"),

    # === target=purchase/* ===
    73: ("purchase",     "skeptical"),
    74: ("purchase",     "skeptical"),
    75: ("price_inq",    "anxious"),
    76: ("chitchat",     "neutral"),
    77: ("price_inq",    "positive"),
    78: ("price_inq",    "anxious"),
    79: ("product_inq",  "anxious"),
    80: ("complaint",    "skeptical"),
    81: ("purchase",     "positive"),
    82: ("purchase",     "positive"),
    83: ("purchase",     "anxious"),
    84: ("purchase",     "anxious"),
    85: ("chitchat",     "neutral"),
    86: ("purchase",     "positive"),
    87: ("purchase",     "skeptical"),
    88: ("price_inq",    "skeptical"),
    89: ("product_inq",  "skeptical"),
    90: ("product_inq",  "skeptical"),
    91: ("complaint",    "negative"),
    92: ("product_inq",  "anxious"),
    93: ("product_inq",  "anxious"),
    94: ("product_inq",  "anxious"),
    95: ("price_inq",    "anxious"),
    96: ("product_inq",  "skeptical"),
    97: ("product_inq",  "neutral"),
    98: ("price_inq",    "anxious"),
    99: ("product_inq",  "skeptical"),
    100: ("chitchat",    "neutral"),
    101: ("price_inq",   "skeptical"),
    102: ("chitchat",    "neutral"),
    103: ("complaint",   "negative"),
    104: ("product_inq", "skeptical"),
    105: ("product_inq", "anxious"),
    106: ("price_inq",   "skeptical"),
    107: ("price_inq",   "anxious"),
    108: ("price_inq",   "anxious"),
    109: ("complaint",   "anxious"),
    110: ("product_inq", "anxious"),
    111: ("product_inq", "skeptical"),
    112: ("product_inq", "anxious"),
    113: ("product_inq", "anxious"),
    114: ("product_inq", "skeptical"),
    115: ("product_inq", "anxious"),

    # === target=complaint/* ===
    116: ("complaint",   "skeptical"),
    117: ("chitchat",    "neutral"),
    118: ("product_inq", "anxious"),
    119: ("product_inq", "skeptical"),
    120: ("product_inq", "skeptical"),
    121: ("price_inq",   "skeptical"),
    122: ("product_inq", "skeptical"),
    123: ("complaint",   "negative"),
    124: ("price_inq",   "skeptical"),
    125: ("product_inq", "skeptical"),
    126: ("price_inq",   "skeptical"),
    127: ("complaint",   "anxious"),
    128: ("chitchat",    "neutral"),
    129: ("product_inq", "skeptical"),
    130: ("complaint",   "negative"),
    131: ("complaint",   "negative"),
    132: ("complaint",   "negative"),
    133: ("price_inq",   "anxious"),
    134: ("product_inq", "anxious"),
    135: ("price_inq",   "anxious"),
    136: ("purchase",    "anxious"),
    137: ("chitchat",    "neutral"),
    138: ("price_inq",   "anxious"),
    139: ("complaint",   "anxious"),
    140: ("price_inq",   "neutral"),
    141: ("complaint",   "anxious"),
    142: ("product_inq", "neutral"),
    143: ("complaint",   "skeptical"),
    144: ("complaint",   "anxious"),
    145: ("complaint",   "anxious"),
    146: ("price_inq",   "skeptical"),
    147: ("complaint",   "anxious"),
    148: ("price_inq",   "skeptical"),
    149: ("chitchat",    "neutral"),
    150: ("product_inq", "anxious"),
    151: ("complaint",   "anxious"),
    152: ("price_inq",   "skeptical"),

    # === target=contact_give/* ===
    153: ("contact_give", "positive"),
    154: ("product_inq",  "positive"),
    155: ("contact_give", "negative"),
    156: ("contact_give", "positive"),
    157: ("contact_give", "positive"),
    158: ("contact_give", "positive"),
    159: ("purchase",     "positive"),
    160: ("contact_give", "skeptical"),
    161: ("contact_give", "negative"),
    162: ("price_inq",    "skeptical"),
    163: ("contact_give", "positive"),
    164: ("contact_give", "skeptical"),
    165: ("contact_give", "negative"),
    166: ("contact_give", "skeptical"),
    167: ("chitchat",     "neutral"),
    168: ("product_inq",  "positive"),
    169: ("price_inq",    "skeptical"),

    # === target=contact_no/* ===
    170: ("product_inq", "neutral"),
    171: ("product_inq", "neutral"),
    172: ("product_inq", "neutral"),
    173: ("chitchat",    "positive"),
    174: ("contact_no",  "neutral"),
    175: ("contact_give","neutral"),
    176: ("complaint",   "neutral"),
    177: ("price_inq",   "anxious"),
    178: ("contact_no",  "neutral"),
    179: ("complaint",   "skeptical"),
    180: ("chitchat",    "neutral"),
    181: ("contact_no",  "positive"),
    182: ("price_inq",   "skeptical"),
    183: ("contact_no",  "anxious"),
    184: ("complaint",   "negative"),
    185: ("contact_no",  "anxious"),
    186: ("contact_give","anxious"),
    187: ("contact_give","negative"),
    188: ("complaint",   "negative"),
    189: ("contact_no",  "anxious"),
    190: ("product_inq", "anxious"),
    191: ("complaint",   "anxious"),
    192: ("product_inq", "skeptical"),
    193: ("contact_no",  "anxious"),
    194: ("product_inq", "anxious"),
    195: ("price_inq",   "skeptical"),
    196: ("product_inq", "neutral"),
    197: ("contact_no",  "negative"),
    198: ("complaint",   "negative"),
    199: ("complaint",   "negative"),
    200: ("price_inq",   "skeptical"),
    201: ("complaint",   "negative"),
    202: ("chitchat",    "neutral"),
    203: ("product_inq", "skeptical"),
    204: ("complaint",   "negative"),
    205: ("complaint",   "negative"),
    206: ("complaint",   "negative"),
    207: ("complaint",   "negative"),
    208: ("contact_no",  "negative"),
    209: ("complaint",   "negative"),

    # === target=contact_fix/* ===
    210: ("contact_fix", "anxious"),
    211: ("contact_fix", "anxious"),
    212: ("complaint",   "negative"),
    213: ("contact_fix", "positive"),
    214: ("contact_fix", "anxious"),
    215: ("chitchat",    "neutral"),
    216: ("contact_fix", "skeptical"),
    217: ("contact_fix", "positive"),

    # === target=chitchat/* ===
    218: ("product_inq", "skeptical"),
    219: ("chitchat",    "neutral"),
    220: ("product_inq", "positive"),
    221: ("product_inq", "positive"),
    222: ("product_inq", "positive"),
    223: ("product_inq", "neutral"),
    224: ("complaint",   "skeptical"),
    225: ("price_inq",   "positive"),
    226: ("product_inq", "positive"),
    227: ("price_inq",   "positive"),
    228: ("complaint",   "skeptical"),
    229: ("complaint",   "skeptical"),
    230: ("price_inq",   "anxious"),
    231: ("chitchat",    "skeptical"),
    232: ("chitchat",    "positive"),
    233: ("chitchat",    "negative"),
    234: ("chitchat",    "negative"),
    235: ("chitchat",    "skeptical"),
    236: ("chitchat",    "skeptical"),
    237: ("chitchat",    "positive"),
}


def apply() -> None:
    base = pathlib.Path(_ROOT) / "tests"

    # ── Load needs_review ──
    review_path = base / "intent_needs_review.jsonl"
    with open(review_path, encoding="utf-8") as f:
        review_items = [json.loads(l) for l in f if l.strip()]

    # ── Apply annotations ──
    for idx, (exp_intent, exp_sent) in ANNOTATIONS.items():
        if idx < len(review_items):
            review_items[idx]["expected_intent"] = exp_intent
            review_items[idx]["expected_sentiment"] = exp_sent

    # ── Check coverage ──
    missing = [i for i in range(len(review_items)) if i not in ANNOTATIONS]
    if missing:
        print(f"⚠ Missing annotations for indices: {missing}")
        return

    # ── Write back needs_review ──
    with open(review_path, "w", encoding="utf-8") as f:
        for item in review_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"Updated: {review_path}")

    # ── Load & update annotated (full) ──
    annotated_path = base / "intent_annotated.jsonl"
    with open(annotated_path, encoding="utf-8") as f:
        annotated_items = [json.loads(l) for l in f if l.strip()]

    review_map = {r["query"]: r for r in review_items}

    for item in annotated_items:
        if item["query"] in review_map:
            r = review_map[item["query"]]
            item["expected_intent"] = r["expected_intent"]
            item["expected_sentiment"] = r["expected_sentiment"]
        else:
            # needs_review=False → target==predicted==expected
            if item["expected_intent"] is None:
                item["expected_intent"] = item["target_intent"]
            if item["expected_sentiment"] is None:
                item["expected_sentiment"] = item["target_sentiment"]

    with open(annotated_path, "w", encoding="utf-8") as f:
        for item in annotated_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"Updated: {annotated_path}")

    # ── Stats ──
    total = len(annotated_items)
    exp_eq_target = sum(1 for a in annotated_items
                        if a["expected_intent"] == a["target_intent"]
                        and a["expected_sentiment"] == a["target_sentiment"])
    exp_eq_pred = sum(1 for a in annotated_items
                      if a["expected_intent"] == a["predicted_intent"]
                      and a["expected_sentiment"] == a["predicted_sentiment"])
    target_int_wrong = sum(1 for a in annotated_items
                           if a["expected_intent"] != a["target_intent"])
    target_sent_wrong = sum(1 for a in annotated_items
                            if a["expected_sentiment"] != a["target_sentiment"])

    print(f"\n=== 标注统计 ===")
    print(f"总数: {total}")
    print(f"expected == target (target标签正确): {exp_eq_target}/{total} ({exp_eq_target/total:.1%})")
    print(f"expected == pred   (模型预测正确):  {exp_eq_pred}/{total} ({exp_eq_pred/total:.1%})")
    print(f"target_intent  标注修正: {target_int_wrong}/{total} ({target_int_wrong/total:.1%})")
    print(f"target_sentiment 标注修正: {target_sent_wrong}/{total} ({target_sent_wrong/total:.1%})")

    # ── Badcase: expected != predicted ──
    badcases = [a for a in annotated_items
                if a["expected_intent"] != a["predicted_intent"]
                or a["expected_sentiment"] != a["predicted_sentiment"]]
    print(f"\n=== Badcase (expected ≠ predicted) ===")
    print(f"总数: {len(badcases)}/{total} ({len(badcases)/total:.1%})")

    intent_bad = Counter()
    sent_bad = Counter()
    for b in badcases:
        if b["expected_intent"] != b["predicted_intent"]:
            intent_bad[(b["expected_intent"], b["predicted_intent"])] += 1
        if b["expected_sentiment"] != b["predicted_sentiment"]:
            sent_bad[(b["expected_sentiment"], b["predicted_sentiment"])] += 1

    print("\nTop intent 误判 (expected → predicted):")
    for (exp, pred), c in intent_bad.most_common(15):
        print(f"  {exp} → {pred}: {c}")

    print("\nTop sentiment 误判 (expected → predicted):")
    for (exp, pred), c in sent_bad.most_common(15):
        print(f"  {exp} → {pred}: {c}")

    # ── Per-intent accuracy ──
    print("\n=== 各意图模型准确率 ===")
    per_intent = defaultdict(lambda: {"total": 0, "correct": 0})
    for a in annotated_items:
        exp = a["expected_intent"]
        per_intent[exp]["total"] += 1
        if a["expected_intent"] == a["predicted_intent"]:
            per_intent[exp]["correct"] += 1

    for intent in sorted(per_intent):
        s = per_intent[intent]
        acc = s["correct"] / s["total"] if s["total"] else 0
        print(f"  {intent}: {s['correct']}/{s['total']} ({acc:.1%})")

    # ── Per-sentiment accuracy ──
    print("\n=== 各情绪模型准确率 ===")
    per_sent = defaultdict(lambda: {"total": 0, "correct": 0})
    for a in annotated_items:
        exp = a["expected_sentiment"]
        per_sent[exp]["total"] += 1
        if a["expected_sentiment"] == a["predicted_sentiment"]:
            per_sent[exp]["correct"] += 1

    for sent in sorted(per_sent):
        s = per_sent[sent]
        acc = s["correct"] / s["total"] if s["total"] else 0
        print(f"  {sent}: {s['correct']}/{s['total']} ({acc:.1%})")

    # ── Confusion matrix ──
    print("\n=== Intent 混淆矩阵 (expected → predicted) ===")
    matrix = defaultdict(lambda: defaultdict(int))
    for a in annotated_items:
        matrix[a["expected_intent"]][a["predicted_intent"]] += 1

    all_intents = sorted(set(list(matrix.keys()) + [k for v in matrix.values() for k in v]))
    header = "expected \\ pred  " + "  ".join(f"{i:>12}" for i in all_intents)
    print(header)
    print("-" * len(header))
    for ei in all_intents:
        row = f"{ei:>16}  "
        row += "  ".join(f"{matrix[ei][pi]:>12}" for pi in all_intents)
        print(row)

    print("\nDone!")


if __name__ == "__main__":
    apply()
