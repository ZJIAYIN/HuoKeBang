"""
批量生成意图识别原始 query 池（不带标注）。

原理：
  用户画像 × 目标意图情绪 → LLM 批量生成 → JSONL

使用方式：
  python tests/generate_intent_dataset.py

输出: tests/intent_raw.jsonl
  {"persona": "...", "query": "...", "target_intent": "price_inq", "target_sentiment": "skeptical", "split": "train"}
"""
import asyncio
import json
import os
import pathlib
import random
import sys
from typing import Any, Dict, List, Tuple

_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv()

from anthropic import AsyncAnthropic


# ═══════════════════════════════════════════════════════════════════════════════
# 画像池
# ═══════════════════════════════════════════════════════════════════════════════

PERSONAS: List[str] = [
    "首次咨询的小白用户，对产品完全不了解，需要从零科普，提问比较基础",
    "精明的比价客户，手上已经有好几家竞品报价，擅长讨价还价，每句话都在压价",
    "暴躁老哥，之前在其他平台被坑过，带着怨气来咨询，说话带刺但内心还是想解决问题",
    "犹豫纠结型客户，已经来回问了三轮了，每次都说'我再想想'，对价格和效果都有顾虑",
    "预算紧张的刚需用户，确实需要这个产品但手头紧，对价格极度敏感，反复确认优惠",
    "急性子客户，上来就要买/要结果，不想听长篇介绍，回复简短直接",
    "年纪较大的用户，不太熟悉线上操作，需要耐心引导，问题琐碎但态度和善",
    "被坑过的回头客，之前遇到过问题，现在来确认新政策，半信半疑",
    "被朋友推荐来的客户，有一定信任基础，但还需要自己确认一下细节",
    "情绪化客户，容易被激怒但也容易被安抚，情绪波动大",
    "同行业竞争对手来打听情报，问的问题很专业很刁钻，但没有真实购买意向",
    "大客户/企业采购负责人，关注合规、发票、合同条款，不在乎小优惠，要求正式",
    "冲动消费型，看到活动就想下单，但容易反悔，需要适度推动",
    "沉默型用户，回复极短（'嗯''哦''好'），需要客服主动引导才能说出需求",
    "学生用户，预算极其有限，对免费/优惠活动特别敏感",
]

# ═══════════════════════════════════════════════════════════════════════════════
# 对话阶段（已注释 — 当前仅用画像+意图情绪生成，暂不带历史上下文）
# ═══════════════════════════════════════════════════════════════════════════════

# STAGES: Dict[str, Dict[str, Any]] = {
#     "greeting": {
#         "desc": "刚进入对话，还没表明具体需求",
#         "history": [],
#     },
#     "product_inquiry": {
#         "desc": "正在了解产品功能，客服刚做了基础介绍",
#         "history": [
#             {"role": "user", "content": "你们主要做什么的"},
#             {"role": "assistant", "content": "我们主要提供智能客服解决方案，支持多渠道接入、自动问答和人工转接。您主要关注哪方面呢？"},
#         ],
#     },
#     "price_discussion": {
#         "desc": "客服已经报了价，用户正在考虑/比较",
#         "history": [
#             {"role": "user", "content": "价格是多少"},
#             {"role": "assistant", "content": "标准版是xxxx/年，包含所有核心功能。高级版xxxx/年，额外支持定制开发和优先技术支持。目前还有新用户9折优惠。"},
#         ],
#     },
#     "after_refusal": {
#         "desc": "之前客服引导留资被拒绝了，用户在继续咨询",
#         "history": [
#             {"role": "user", "content": "你们产品有什么功能"},
#             {"role": "assistant", "content": "我们支持A、B、C三大功能模块。方便留个联系方式吗？我让顾问给您发详细资料。"},
#             {"role": "user", "content": "暂时不方便，先看看"},
#             {"role": "assistant", "content": "没关系的，您有什么问题随时问我。"},
#         ],
#     },
#     "purchase_intent": {
#         "desc": "用户已经表现出明确购买意向，正在商量下单细节",
#         "history": [
#             {"role": "user", "content": "我要买标准版"},
#             {"role": "assistant", "content": "好的！标准版xxxx/年。请问是用个人名义还是公司名义下单？"},
#         ],
#     },
#     "complaint_context": {
#         "desc": "用户之前反馈过问题，现在来跟进",
#         "history": [
#             {"role": "user", "content": "我上次的问题还没解决"},
#             {"role": "assistant", "content": "非常抱歉给您带来不便，能告诉我具体是什么问题吗？我帮您跟进处理。"},
#         ],
#     },
# }


# ═══════════════════════════════════════════════════════════════════════════════
# 意图×情绪覆盖矩阵
# ═══════════════════════════════════════════════════════════════════════════════

TARGETS: List[Tuple[str, str, str]] = [
    # (intent, sentiment, 一句话说明)
    ("greeting",     "positive",  "友好打招呼"),
    ("greeting",     "neutral",   "简单问在不在"),
    ("product_inq",  "neutral",   "正常咨询产品"),
    ("product_inq",  "skeptical", "质疑产品效果"),
    ("product_inq",  "anxious",   "担心产品质量/售后"),
    ("product_inq",  "positive",  "对产品很感兴趣"),
    ("price_inq",    "neutral",   "正常询问价格"),
    ("price_inq",    "skeptical", "嫌贵/讨价还价"),
    ("price_inq",    "anxious",   "预算不够但有需求"),
    ("purchase",     "positive",  "主动要买"),
    ("purchase",     "neutral",   "询问下单流程"),
    ("purchase",     "skeptical", "临门一脚犹豫"),
    ("purchase",     "anxious",   "想买但怕被骗"),
    ("complaint",    "negative",  "强烈不满"),
    ("complaint",    "skeptical", "质疑但不完全是投诉"),
    ("complaint",    "anxious",   "焦虑型投诉"),
    ("complaint",    "neutral",   "冷静反馈问题"),
    ("contact_give", "neutral",   "直接给号码"),
    ("contact_give", "positive",  "配合留联系方式"),
    ("contact_no",   "neutral",   "平和拒绝"),
    ("contact_no",   "anxious",   "不放心所以拒绝"),
    ("contact_no",   "negative",  "强硬拒绝"),
    ("contact_fix",  "neutral",   "更正联系方式"),
    ("chitchat",     "positive",  "闲聊"),
    ("chitchat",     "neutral",   "问是不是机器人"),
]

QUERIES_PER_PERSONA = 1   # 每条画像生成 1 条（train）
PERSONAS_PER_COMBO = 15   # 所有画像

# # ── 旧参数（已注释）──────────────────────────────────────────────
# QUERIES_PER_BATCH = 1
# STAGES_PER_COMBO = 3


# ═══════════════════════════════════════════════════════════════════════════════

def _build_prompt(persona: str, intent_label: str,
                  sentiment_label: str, desc: str) -> str:
    return f"""你是客服对话数据生成专家，请为以下场景生成 {QUERIES_PER_PERSONA} 条用户消息。

用户画像: {persona}
意图: {intent_label}（{desc}）
情绪: {sentiment_label}

要求：
1. 每条消息要自然，像真实用户聊天（可以有口语化、错别字）
2. 消息表达方式可以有口语化、错别字
3. 必须符合用户画像设定
4. 意图和情绪是生成引导，消息要自然体现这些特征，不要太直白

返回 JSON 数组: ["消息"]"""

# # ── 旧 prompt（带历史上下文，已注释）─────────────────────────────
# def _build_prompt(persona: str, stage: Dict[str, Any],
#                   intent_label: str, sentiment_label: str,
#                   desc: str, count: int) -> str:
#     history_lines = []
#     if stage["history"]:
#         for h in stage["history"]:
#             history_lines.append(f"  {h['role']}: {h['content']}")
#     history_text = "\n".join(history_lines) if history_lines else "（无历史，这是对话的第一句话）"
#     return f"""你是客服对话数据生成专家，请为以下场景生成 {count} 条用户可能发送的消息。
# ...
# 返回 JSON 数组: ["消息1", "消息2", ...]"""


async def generate(client: AsyncAnthropic, model: str,
                   persona: str, intent_label: str,
                   sentiment_label: str, desc: str) -> List[str]:
    prompt = _build_prompt(persona, intent_label, sentiment_label, desc)
    try:
        resp = await client.messages.create(
            model=model, max_tokens=512, temperature=0.8,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        s, e = raw.find("["), raw.rfind("]") + 1
        if s == -1:
            return []
        return [q.strip() for q in json.loads(raw[s:e]) if isinstance(q, str) and q.strip()]
    except Exception as ex:
        print(f"  ⚠ 失败: {ex}")
        return []

# # ── 旧 generate（带 stage 参数，已注释）──────────────────────────
# async def generate(client: AsyncAnthropic, model: str,
#                    persona: str, stage: Dict[str, Any],
#                    intent_label: str, sentiment_label: str,
#                    desc: str, count: int) -> List[str]:


async def main():
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip() or None
    model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    if not api_key:
        print("❌ 请设置 ANTHROPIC_API_KEY")
        return

    client = AsyncAnthropic(api_key=api_key, **(dict(base_url=base_url) if base_url else {}))
    print(f"模型: {model}\n")

    out_path = pathlib.Path(_ROOT) / "tests" / "intent_raw.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("", encoding="utf-8")

    total = 0
    for intent_val, sent_val, desc in TARGETS:
        personas = random.sample(PERSONAS, min(PERSONAS_PER_COMBO, len(PERSONAS)))

        for persona in personas:
            label = f"[{intent_val}/{sent_val}]"
            print(f"{label} 画像={persona[:25]}...", end=" ", flush=True)

            queries = await generate(client, model, persona, intent_val, sent_val, desc)

            splits = ["train", "test"][:len(queries)]
            with open(out_path, "a", encoding="utf-8") as f:
                for q, split in zip(queries, splits):
                    f.write(json.dumps({
                        "persona":         persona,
                        "query":           q,
                        "target_intent":   intent_val,
                        "target_sentiment": sent_val,
                        "split":           split,
                    }, ensure_ascii=False) + "\n")

            total += len(queries)
            print(f"→ {len(queries)} 条")
            await asyncio.sleep(0.3)

    print(f"\n✅ 生成完成: {total} 条 → {out_path}")
    print(f"   25 combos × {PERSONAS_PER_COMBO} personas × {QUERIES_PER_PERSONA} = {25 * PERSONAS_PER_COMBO * QUERIES_PER_PERSONA} (理论)")
    print(f"下一步: python tests/annotate_intent.py")


if __name__ == "__main__":
    asyncio.run(main())
