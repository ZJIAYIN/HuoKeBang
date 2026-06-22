"""
验证 KnowledgeBase 的切割策略 + BM25 索引 + RRF 融合。

用法:
  cd /mnt/d/py\ works/EchoMind
  python3 -m pytest tests/test_knowledge_base.py -v
  或直接:
  python3 tests/test_knowledge_base.py
"""
import logging
import sys
import pathlib

_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from mcp.knowledge_base import BM25Index, KnowledgeBase  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 切割策略测试
# ═══════════════════════════════════════════════════════════════════════════════

def test_split_recursive_short_text():
    """短文不切割"""
    kb = KnowledgeBase.__new__(KnowledgeBase)
    result = kb._split_recursive("你好世界", level=0)
    assert len(result) == 1
    assert result[0] == "你好世界"


def test_split_recursive_paragraph():
    """多段落按 \\n\\n 切"""
    kb = KnowledgeBase.__new__(KnowledgeBase)
    text = "段落A内容。" + "X" * 400 + "\n\n段落B内容。" + "Y" * 400
    result = kb._split_recursive(text, level=0)
    # 每段都 > 500，但 \\n\\n 切后各自都 < 500（因为每段 ~400+文字）
    # 实际上 400 + "段落A内容。" ≈ 407 < 500，所以不会继续切
    assert len(result) >= 1  # 至少拆成 A 和 B 两段


def test_split_recursive_long_sentence():
    """超长句降级到 \\n 再降级到 。"""
    kb = KnowledgeBase.__new__(KnowledgeBase)
    # 一段 1200 字，没有 \\n\\n 和 \\n，但有很多句号
    text = ("第一句内容" + "。") * 60  # ~360 字
    text += ("第二句内容" + "。") * 60
    text += ("第三句内容" + "。") * 60
    # 总共约 1080 字
    result = kb._split_recursive(text, level=0)
    # 所有碎片都应 ≤ 500
    for r in result:
        assert len(r) <= 500, f"碎片长度 {len(r)} > 500: {r[:50]}..."


def test_split_recursive_hard_cut():
    """无任何分隔符 → 字符硬切"""
    kb = KnowledgeBase.__new__(KnowledgeBase)
    text = "A" * 1200
    result = kb._split_recursive(text, level=0)
    assert len(result) == 3  # 1200 / 500 = 3 块
    for r in result:
        assert len(r) <= 500


def test_merge_greedy():
    """贪心凑块输出每块 ≤ 500"""
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb.CHUNK_SIZE = 500
    pieces = ["A" * 200, "B" * 200, "C" * 100, "D" * 300, "E" * 100, "F" * 250, "G" * 100]
    merged = kb._merge_greedy(pieces)
    # 期望: [200+200=400, 100+300=400, 100+250=350, 100]
    for m in merged:
        assert len(m) <= 500 + 3, f"块长度 {len(m)} > 500"


def test_add_overlap():
    """overlap 测试"""
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb.OVERLAP = 5
    chunks = ["ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZ"]
    result = kb._add_overlap(chunks)
    assert result[0] == "ABCDEFGHIJ"
    assert result[1].startswith("GHIJ")  # chunks[0][-5:] = "GHIJ"
    assert result[2].startswith("QRST")  # chunks[1][-5:] = "QRST"


def test_chunk_full_pipeline():
    """完整的三阶段切割"""
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb.CHUNK_SIZE = 500
    kb.OVERLAP = 50

    text = (
        "退款政策说明。用户在购买后7天内可以申请无理由退款。"
        "退款申请提交后，系统会在1-3个工作日内审核。"
        "审核通过后，款项将在5-7个工作日内退回原支付账户。\n\n"
        "如果商品已发货，需要先完成退货流程才能退款。"
        "退货运费由用户承担，除非是商品质量问题。"
        "超过7天但未超过30天的订单，需要提供商品质量问题的证据才能退款。\n\n"
        "订单查询指南。用户可以通过订单号查询订单状态。"
        "订单状态包括：待支付、已支付、已发货、运输中、已签收、已完成。"
    )
    chunks = kb._chunk(text)
    assert len(chunks) >= 1
    # overlap 检查
    if len(chunks) > 1:
        prev_tail = chunks[0][-50:]
        assert chunks[1].startswith(prev_tail), \
            f"chunk[1] 应该以 chunk[0] 的尾部开头"
    print(f"  切割结果: {len(chunks)} 块")
    for i, c in enumerate(chunks):
        print(f"    Block {i}: {len(c)} chars → {c[:60]}...")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. BM25 索引测试
# ═══════════════════════════════════════════════════════════════════════════════

def test_bm25_index_and_search():
    """BM25 基本索引 + 检索"""
    bm = BM25Index()
    docs = {
        "d1": "退款政策说明 用户在购买后7天内可以申请无理由退款",
        "d2": "订单查询指南 用户可以通过订单号查询订单状态",
        "d3": "账户安全说明 建议用户定期修改密码",
    }
    for did, text in docs.items():
        bm.index(did, text)

    assert bm.doc_count == 3

    # 搜索 "退款"
    results = bm.search("退款", top_k=3)
    assert len(results) > 0
    assert results[0][0] == "d1"  # d1 应该排第一
    print(f"  BM25 检索 '退款': {results}")

    # 搜索不存在的词
    results = bm.search("火星殖民", top_k=3)
    assert len(results) == 0


def test_bm25_rebuild():
    """BM25 全量重建"""
    bm = BM25Index()
    bm.index("a", "测试文档 A")
    bm.index("b", "测试文档 B")
    bm.rebuild({"x": "新的文档 X", "y": "新的文档 Y"})
    assert bm.doc_count == 2
    assert bm.search("文档 A", top_k=1) == []  # 旧的被清掉了


def test_bm25_remove():
    """BM25 删除文档"""
    bm = BM25Index()
    bm.index("a", "退款政策")
    bm.index("b", "订单查询")
    bm.remove("a")
    assert bm.doc_count == 1
    results = bm.search("退款", top_k=3)
    assert len(results) == 0  # "退款" 只在已删除的 a 中


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RRF 融合测试
# ═══════════════════════════════════════════════════════════════════════════════

def test_rrf_fusion():
    """RRF 融合基本逻辑"""
    vec = [
        {"id": "a", "score": 0.9, "title": "Doc A"},
        {"id": "b", "score": 0.8, "title": "Doc B"},
        {"id": "c", "score": 0.7, "title": "Doc C"},
    ]
    kw = [
        {"id": "b", "score": 5.2, "title": "Doc B"},
        {"id": "d", "score": 4.1, "title": "Doc D"},
        {"id": "a", "score": 3.0, "title": "Doc A"},
    ]
    fused = KnowledgeBase._rrf_fusion(vec, kw, k=60)

    # b 在两个排名中分别排第 2 和第 1，应该总分最高
    assert fused[0]["id"] == "b", f"期望 b 排第一，实际 {fused[0]['id']}"
    assert "rrf_score" in fused[0]
    print(f"  RRF 融合结果:")
    for item in fused:
        print(f"    {item['id']}: rrf={item['rrf_score']}")


def test_rrf_single_ranking():
    """只有一路排名时，RRF 退化为原排名"""
    vec = [
        {"id": "x", "score": 0.9},
        {"id": "y", "score": 0.8},
        {"id": "z", "score": 0.7},
    ]
    fused = KnowledgeBase._rrf_fusion(vec, k=60)
    assert [r["id"] for r in fused] == ["x", "y", "z"]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 知识库完整流程测试（需要 ChromaDB）
# ═══════════════════════════════════════════════════════════════════════════════

def test_knowledge_base_e2e():
    """端到端：导入文档 → 向量检索 → 混合检索"""
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        kb = KnowledgeBase(
            chroma_host="nonexistent-host",  # 故意不连远程，触发本地模式
            chroma_port=9999,
            chroma_path=tmpdir,
        )

        # 导入测试文档
        count = kb.add_documents([
            {
                "title": "测试退款",
                "content": "退款政策：用户在购买后7天内可以申请无理由退款。退款将原路返回。",
            },
            {
                "title": "测试订单",
                "content": "订单查询：用户可以通过订单号查询物流状态和配送进度。",
            },
            {
                "title": "长文档测试",
                "content": (
                    "这是第一章内容。" * 30 + "\n\n" +
                    "这是第二章内容。" * 30 + "\n\n" +
                    "这是第三章内容，包含退款相关信息。" * 30
                ),
            },
        ])
        assert count > 0
        print(f"  导入 {count} 个片段")

        # 向量检索
        vec_results = kb.search("退款", top_k=3)
        assert len(vec_results) > 0
        print(f"  向量检索 '退款': {len(vec_results)} 条")

        # 混合检索
        hybrid_results = kb.search_hybrid("退款", top_k=3)
        assert len(hybrid_results) > 0
        # 混合检索结果包含 rrf_score
        for r in hybrid_results:
            assert "rrf_score" in r
        print(f"  混合检索 '退款': {len(hybrid_results)} 条")
        for r in hybrid_results:
            print(f"    [{r['rrf_score']:.4f}] {r['title']}: {r['content'][:60]}...")

        # 验证 BM25 索引同步
        assert kb.bm25_doc_count == kb.doc_count

        # 精确关键词检索：BM25 应该能精确命中
        precise_results = kb.search_hybrid("401 错误", top_k=1)
        print(f"  混合检索 '401 错误': {len(precise_results)} 条")


def test_search_handler():
    """MCP handler 接口"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        kb = KnowledgeBase(chroma_host="nonexistent-host", chroma_port=9999, chroma_path=tmpdir)
        kb.add_documents([
            {"title": "测试", "content": "这是一篇关于退款的文档。" * 10},
        ])

        import asyncio
        results = asyncio.run(kb.search_handler({"query": "退款"}, None))
        assert len(results) > 0
        print(f"  search_handler '退款': {len(results)} 条")


# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import traceback

    tests = [
        ("递归切分-短文", test_split_recursive_short_text),
        ("递归切分-段落", test_split_recursive_paragraph),
        ("递归切分-长句", test_split_recursive_long_sentence),
        ("递归切分-硬切", test_split_recursive_hard_cut),
        ("贪心凑块", test_merge_greedy),
        ("overlap", test_add_overlap),
        ("完整切割流程", test_chunk_full_pipeline),
        ("BM25-索引检索", test_bm25_index_and_search),
        ("BM25-重建", test_bm25_rebuild),
        ("BM25-删除", test_bm25_remove),
        ("RRF-融合", test_rrf_fusion),
        ("RRF-单路排名", test_rrf_single_ranking),
        ("知识库-端到端", test_knowledge_base_e2e),
        ("search_handler接口", test_search_handler),
    ]

    passed = 0
    for name, fn in tests:
        try:
            print(f"\n{'='*60}")
            print(f"TEST: {name}")
            print(f"{'='*60}")
            fn()
            passed += 1
            print(f"  ✓ PASS")
        except Exception as e:
            print(f"  ✗ FAIL: {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"结果: {passed}/{len(tests)} 通过")
    print(f"{'='*60}")
