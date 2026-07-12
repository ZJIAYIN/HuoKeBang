"""
RAG 知识库 —— 混合检索（向量 + BM25 关键词）+ RRF 融合。

功能：
  1. 文档导入：递归切割 + 贪心凑块（500 字）+ overlap 50
  2. 混合检索：向量语义检索 + BM25 关键词检索 → RRF 融合
  3. 与 MCP 工具框架集成：作为 knowledge_search 工具的真实 handler

混合检索链路：
  用户查询 → 向量检索(ChromaDB) + 关键词检索(BM25/jieba)
           → RRF 融合粗排
           → 上游 LLM 重排（由 MCPToolManager._rerank 完成）
"""
import hashlib
import logging
import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import chromadb
import jieba

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# BM25 关键词索引（纯 Python + jieba 分词）
# ═══════════════════════════════════════════════════════════════════════════════

class BM25Index:
    """
    纯 Python BM25 关键词检索索引。

    使用 jieba 做中文分词，无需外部检索服务。
    支持增量索引和全量重建。

    参数：
        k1: 词频饱和参数（默认 1.5）
        b:  文档长度归一化参数（默认 0.75）
        custom_words: 额外领域词典词条
    """

    # 客服领域默认词典 — 防止 jieba 误拆专有名词
    _DOMAIN_DICT: List[str] = [
        # 错误码 / 状态码
        "401错误", "500错误", "404错误", "403错误", "502错误", "503错误",
        # 时间/时效
        "工作日", "1-3个工作日", "3-5个工作日", "5-7个工作日", "24小时",
        "7天", "30天", "1年",
        # 业务术语
        "订单号", "运单号", "订单状态", "物流信息", "收货地址",
        "无理由退款", "原路返回", "两步验证", "二维码",
        "退款政策", "退款时效", "退款到账", "申请退款",
        "支付账户", "网上支付", "银行卡",
        "会员等级", "银卡会员", "金卡会员", "普通会员",
        "标准配送", "加急配送", "同城配送",
        # 常见词组
        "联系客服", "人工客服", "转人工", "在线客服",
        "商品质量", "累计消费", "异常登录",
        # 数字相关
        "95折", "9折",
    ]

    def __init__(self, k1: float = 1.5, b: float = 0.75,
                 custom_words: Optional[List[str]] = None):
        self.k1 = k1
        self.b = b
        self._docs: Dict[str, str] = {}
        self._tokens: Dict[str, List[str]] = {}
        self._df: Dict[str, int] = {}
        self._N = 0
        self._avgdl = 0.0

        # 注册领域词典
        for word in self._DOMAIN_DICT:
            jieba.add_word(word)
        if custom_words:
            for word in custom_words:
                jieba.add_word(word)

    # ── 分词 ──────────────────────────────────────────────────────────────────

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """
        中文用 jieba 分词，英文/数字保留。
        过滤单字和纯标点，保留有意义的词。
        """
        tokens: List[str] = []
        for word in jieba.cut(text):
            word = word.strip()
            if not word:
                continue
            # 过滤纯标点/空白
            if all(not c.isalnum() and not '一' <= c <= '鿿' for c in word):
                continue
            # 保留长度 >= 2 的中文词 或 任意英文/数字 token
            if len(word) >= 2 or word.isascii():
                tokens.append(word.lower())
        return tokens

    # ── 索引 ──────────────────────────────────────────────────────────────────

    def index(self, doc_id: str, text: str) -> None:
        """增量添加一篇文档到索引。"""
        doc_id = str(doc_id)
        text = self._safe_text(text)
        self._docs[doc_id] = text
        tokens = self.tokenize(text)
        self._tokens[doc_id] = tokens

        for token in set(tokens):
            self._df[token] = self._df.get(token, 0) + 1

        self._N = len(self._docs)
        self._avgdl = sum(len(t) for t in self._tokens.values()) / self._N if self._N else 0.0

    def rebuild(self, docs: Dict[str, str]) -> None:
        """全量重建索引。docs: {doc_id: text}"""
        self._docs.clear()
        self._tokens.clear()
        self._df.clear()
        for doc_id, text in docs.items():
            self._docs[str(doc_id)] = self._safe_text(text)
            tokens = self.tokenize(self._docs[str(doc_id)])
            self._tokens[str(doc_id)] = tokens
            for token in set(tokens):
                self._df[token] = self._df.get(token, 0) + 1
        self._N = len(self._docs)
        self._avgdl = sum(len(t) for t in self._tokens.values()) / self._N if self._N else 0.0

    def remove(self, doc_id: str) -> None:
        """从索引中移除一篇文档。"""
        doc_id = str(doc_id)
        if doc_id not in self._docs:
            return
        old_tokens = set(self._tokens.get(doc_id, []))
        for token in old_tokens:
            if token in self._df:
                self._df[token] -= 1
                if self._df[token] <= 0:
                    del self._df[token]
        self._docs.pop(doc_id, None)
        self._tokens.pop(doc_id, None)
        self._N = len(self._docs)
        self._avgdl = sum(len(t) for t in self._tokens.values()) / self._N if self._N else 0.0

    # ── 检索 ──────────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """
        BM25 关键词检索。

        返回: [(doc_id, bm25_score), ...]，按分数降序排列。
        空索引时返回空列表。
        """
        if self._N == 0 or not query:
            return []

        query_tokens = self.tokenize(query)
        if not query_tokens:
            return []

        scores: List[Tuple[str, float]] = []
        for doc_id, doc_tokens in self._tokens.items():
            score = self._bm25_score(query_tokens, doc_tokens)
            if score > 0:
                scores.append((doc_id, score))

        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]

    def _bm25_score(self, query_tokens: List[str], doc_tokens: List[str]) -> float:
        """计算单个文档的 BM25 分数。"""
        score = 0.0
        dl = max(len(doc_tokens), 1)
        tf = Counter(doc_tokens)

        for qt in query_tokens:
            if qt not in self._df:
                continue
            idf = math.log((self._N - self._df[qt] + 0.5) / (self._df[qt] + 0.5) + 1.0)
            tfd = tf.get(qt, 0)
            if tfd == 0:
                continue
            numerator = tfd * (self.k1 + 1)
            denominator = tfd + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
            score += idf * numerator / denominator

        return score

    @property
    def doc_count(self) -> int:
        return self._N

    @staticmethod
    def _safe_text(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 知识库（混合检索）
# ═══════════════════════════════════════════════════════════════════════════════

class KnowledgeBase:
    """
    基于 ChromaDB + BM25 的混合检索知识库。

    检索链路：
      向量检索(ChromaDB) + BM25 关键词检索 → RRF 融合 → Top-K

    ChromaDB 内置 Embedding 模型（all-MiniLM-L6-v2），
    调用 add() 时自动生成向量，query() 时自动做语义匹配。
    """

    COLLECTION_NAME = "knowledge_base"

    # 切割参数
    CHUNK_SIZE = 500    # 目标块大小（字符数）
    OVERLAP    = 50      # 相邻块之间的重叠字符数

    # RRF 参数
    RRF_K = 60

    # ── 递归切割的分隔符优先级（从粗到细）────────────────────────────────────
    _SEPARATORS = ["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
    ):
        """初始化知识库，使用 ChromaDB 内置嵌入模型。

        Args:
            chroma_host: ChromaDB 服务地址（HTTP 模式）
            chroma_port: ChromaDB 服务端口
            chroma_path: 本地持久化路径（HTTP 不可用时降级使用）
        """
        # 优先连接独立 ChromaDB 服务
        self._use_server = False
        try:
            self._client = chromadb.HttpClient(host=chroma_host, port=chroma_port)
            self._client.heartbeat()
            self._use_server = True
            logger.info(f"知识库 ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"知识库 ChromaDB 服务不可用，使用本地模式: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 使用 ChromaDB 内置嵌入模型
        try:
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={
                    "description": "EchoMind RAG 知识库（混合检索：向量 + BM25 → RRF）",
                },
            )
        except ValueError as _e:
            if "Embedding function conflict" in str(_e):
                logger.warning("嵌入函数冲突，删除旧集合重建")
                self._client.delete_collection(self.COLLECTION_NAME)
                self._collection = self._client.get_or_create_collection(
                    name=self.COLLECTION_NAME,
                    metadata={
                        "description": "EchoMind RAG 知识库（混合检索：向量 + BM25 → RRF）",
                    },
                )
            else:
                raise

        # BM25 关键词索引
        self._bm25 = BM25Index()

        # 如果知识库为空，导入默认文档
        if self._collection.count() == 0:
            self._load_default_docs()
        else:
            # ChromaDB 已有数据（服务重启），从 ChromaDB 重建 BM25 索引
            self._rebuild_bm25_from_chroma()
            logger.info(
                f"已从 ChromaDB 重建 BM25 索引: {self._bm25.doc_count} 篇"
            )

    def _rebuild_bm25_from_chroma(self) -> None:
        """从 ChromaDB 中读取所有文档，全量重建 BM25 索引。"""
        if self._collection.count() == 0:
            return
        result = self._collection.get()
        if result["documents"]:
            bm25_docs = {}
            for i in range(len(result["documents"])):
                doc_id = result["ids"][i] if result["ids"] and i < len(result["ids"]) else ""
                doc_text = result["documents"][i]
                if doc_id and doc_text:
                    bm25_docs[doc_id] = doc_text
            self._bm25.rebuild(bm25_docs)

    # ── 文档管理 ──────────────────────────────────────────────────────────────

    def add_documents(self, documents: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        批量导入文档到知识库（带内容去重）。

        documents 格式: [{"title": "...", "content": "..."}, ...]

        每篇文档以全文 MD5 作为 doc_id，重复上传自动跳过。
        doc_id 存入每个 chunk 的 metadata，支持按文档粒度删除。

        返回: {"added_chunks": N, "doc_ids": [...], "skipped": N}

        流程：
          1. 全文 MD5 计算 doc_id，检查是否已存在
          2. 新文档：递归切割 + 贪心凑块（~500 字）+ overlap 50
          3. 存入 ChromaDB（向量索引），同步加入 BM25 关键词索引
        """
        ids, docs, metas = [], [], []
        doc_ids: List[str] = []
        skipped = 0

        for doc in documents:
            title   = doc.get("title", "")
            content = doc.get("content", "")
            # "标题+内容" MD5 作为文档 ID — 标题不同就算不同文档，去重精确
            doc_id  = hashlib.md5(f"{title}|{content}".encode("utf-8")).hexdigest()
            doc_ids.append(doc_id)

            # 去重：检查 doc_id 是否已存在
            try:
                existing = self._collection.get(where={"doc_id": doc_id})
                if existing["ids"]:
                    skipped += 1
                    continue
            except Exception:
                pass  # 查重失败时继续导入，避免因查询异常阻塞写入

            chunks = self._chunk(content)

            for i, chunk_text in enumerate(chunks):
                chunk_id = hashlib.md5(
                    f"{doc_id}_c{i}_{chunk_text[:80]}".encode()
                ).hexdigest()
                ids.append(chunk_id)
                docs.append(chunk_text)
                metas.append({
                    "title":        title,
                    "doc_id":       doc_id,
                    "chunk_index":  i,
                    "total_chunks": len(chunks),
                })
                # BM25 增量索引
                self._bm25.index(chunk_id, chunk_text)

        if ids:
            if not (len(ids) == len(docs) == len(metas)):
                logger.error(
                    f"列表长度不一致！ids={len(ids)} docs={len(docs)} metas={len(metas)}"
                    f" 共 {len(documents)} 篇文档，已跳过 {skipped} 篇重复"
                )
                for d in documents:
                    logger.error(f"  doc title={d.get('title','')[:50]} content_len={len(d.get('content',''))}")
                raise RuntimeError(
                    f"add_documents 内部错误：ids/documents/metadatas 长度不一致 "
                    f"({len(ids)}/{len(docs)}/{len(metas)})，请检查日志"
                )
            self._collection.add(ids=ids, documents=docs, metadatas=metas)
            logger.info(
                f"知识库导入 {len(ids)} 个片段（{len(doc_ids)} 篇文档），"
                f"已跳过 {skipped} 篇重复，"
                f"BM25 索引 {self._bm25.doc_count} 篇"
            )

        if skipped and not ids:
            logger.info(f"全部 {skipped} 篇文档均为重复，已跳过")

        return {"added_chunks": len(ids), "doc_ids": doc_ids, "skipped": skipped}

    def delete_by_doc_id(self, doc_id: str) -> int:
        """
        按文档 ID 删除一篇文档的所有 chunk。
        同步清理 ChromaDB 和 BM25 索引。

        返回: 删除的 chunk 数量。
        """
        try:
            result = self._collection.get(where={"doc_id": doc_id})
        except Exception:
            # ChromaDB 部分版本/模式不支持 where 过滤 → 降级全量扫描
            result = self._collection.get()
            if result["ids"] and result["metadatas"]:
                drop_ids: List[str] = []
                for i in range(len(result["ids"])):
                    meta = result["metadatas"][i] if i < len(result["metadatas"]) else {}
                    if meta.get("doc_id") == doc_id:
                        drop_ids.append(result["ids"][i])
                if drop_ids:
                    self._collection.delete(ids=drop_ids)
                for cid in drop_ids:
                    self._bm25.remove(cid)
                logger.info(f"删除文档 {doc_id}: {len(drop_ids)} 个片段（降级扫描模式）")
                return len(drop_ids)
            return 0

        chunk_ids = result.get("ids", [])
        if not chunk_ids:
            return 0

        self._collection.delete(ids=chunk_ids)
        for cid in chunk_ids:
            self._bm25.remove(cid)

        logger.info(f"删除文档 {doc_id}: {len(chunk_ids)} 个片段")
        return len(chunk_ids)

    def clear(self) -> int:
        """
        清空知识库所有文档（只删内容，保留集合和嵌入函数）。
        同步清理 ChromaDB 和 BM25 索引。

        返回: 删除的 chunk 数量。
        """
        total = self._collection.count()
        if total == 0:
            return 0

        result = self._collection.get()
        if result["ids"]:
            self._collection.delete(ids=result["ids"])
            for cid in result["ids"]:
                self._bm25.remove(cid)

        logger.info(f"知识库已清空: 删除 {total} 个片段")
        return total

    def list_chunks(self) -> List[Dict[str, Any]]:
        """
        返回知识库中所有片段的完整列表。

        每条包含：id, doc_id, title, content（全文）, chunk_index, total_chunks。
        """
        if self._collection.count() == 0:
            return []

        result = self._collection.get()
        items: List[Dict[str, Any]] = []
        if result["documents"]:
            for i in range(len(result["documents"])):
                meta = result["metadatas"][i] if result["metadatas"] and i < len(result["metadatas"]) else {}
                items.append({
                    "id":           result["ids"][i] if result["ids"] and i < len(result["ids"]) else "",
                    "doc_id":       meta.get("doc_id", ""),
                    "title":        meta.get("title", ""),
                    "content":      result["documents"][i],
                    "chunk_index":  meta.get("chunk_index", 0),
                    "total_chunks": meta.get("total_chunks", 1),
                })
        return items

    def list_documents(self) -> List[Dict[str, Any]]:
        """
        按 doc_id 分组返回文档列表（含 chunk 预览）。

        每条包含：doc_id, title, chunk_count, content_preview。
        """
        chunks = self.list_chunks()
        groups: Dict[str, Dict[str, Any]] = {}
        for c in chunks:
            did = c.get("doc_id", "")
            if not did:
                continue
            if did not in groups:
                groups[did] = {
                    "doc_id":   did,
                    "title":    c.get("title", ""),
                    "chunks":   [],
                }
            groups[did]["chunks"].append({
                "id":           c["id"],
                "chunk_index":  c["chunk_index"],
                "total_chunks": c["total_chunks"],
                "content":      c["content"],
            })

        result = []
        for did, g in groups.items():
            g["chunk_count"] = len(g["chunks"])
            g["content_preview"] = (g["chunks"][0]["content"][:120] + "…") if g["chunks"] else ""
            result.append(g)
        return result

    # ── 检索 ──────────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        纯向量语义检索（保留向后兼容）。
        如需混合检索，请使用 search_hybrid()。
        """
        return self._vector_search(query, top_k)

    def search_hybrid(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        混合检索：向量 + BM25 关键词 → RRF 融合。

        相比纯向量检索：
          - 关键词匹配可以精确命中专有名词（如"退款"、"401 错误"）
          - BM25 对罕见术语的召回优于向量检索
          - RRF 融合综合语义相似度和关键词相关性，不依赖分数归一化
        """
        if self._collection.count() == 0:
            return []

        pool_size = max(top_k * 3, 10)  # RRF 候选池

        # 1. 向量检索
        vec_results = self._vector_search(query, pool_size)

        # 2. BM25 关键词检索
        kw_raw = self._bm25.search(query, pool_size)
        kw_results = self._bm25_lookup(kw_raw)

        # 3. RRF 融合
        fused = self._rrf_fusion(vec_results, kw_results, k=self.RRF_K)

        logger.debug(
            f"混合检索: query={query!r}, "
            f"向量={len(vec_results)}, BM25={len(kw_results)}, 融合={len(fused)}"
        )

        return fused[:top_k]

    # ── MCP 工具 handler ─────────────────────────────────────────────────────

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict[str, Any]]:
        """
        作为 MCP 工具的 handler 注册。
        使用混合检索（向量 + BM25 → RRF）。
        """
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        return self.search_hybrid(query, top_k=top_k)

    # ── 统计 ──────────────────────────────────────────────────────────────────

    @property
    def doc_count(self) -> int:
        return self._collection.count()

    @property
    def bm25_doc_count(self) -> int:
        return self._bm25.doc_count

    # ══════════════════════════════════════════════════════════════════════════
    # 切割策略
    # ══════════════════════════════════════════════════════════════════════════

    def _chunk(self, text: str) -> List[str]:
        """
        递归切割 + 贪心凑块 + overlap 50。

        Phase 0: 提取 [表格开始]...[表格结束] 区域为原子块，避免表格被拆散
        Phase 1: 递归拆到每个碎片 ≤ CHUNK_SIZE
        Phase 2: 贪心合并碎片，尽量接近 CHUNK_SIZE
        Phase 3: 相邻块之间重叠 OVERLAP 字符
        """
        if not text or not text.strip():
            return []

        # Phase 0: 提取表格预块，保证表格不被后续切分拆散
        mixed = self._split_preserve_tables(text)

        # Phase 1: 递归拆分（跳过表格预块）
        pieces = []
        for is_table, segment in mixed:
            if is_table:
                pieces.append(segment)
            else:
                pieces.extend(self._split_recursive(segment))

        # Phase 2: 贪心凑块
        merged = self._merge_greedy(pieces)

        # Phase 3: overlap
        return self._add_overlap(merged)

    @classmethod
    def _split_recursive(cls, text: str, level: int = 0) -> List[str]:
        """
        递归切分：逐级尝试更细的分隔符，直到每个碎片 ≤ CHUNK_SIZE。

        分隔符优先级: \\n\\n → \\n → 。 → ！ → ？ → . → ! → ? → 空格 → 字符硬切
        """
        if len(text) <= cls.CHUNK_SIZE:
            return [text] if text.strip() else []

        sep = cls._SEPARATORS[level] if level < len(cls._SEPARATORS) else ""

        if sep == "":
            # 兜底：字符硬切
            return [text[i:i + cls.CHUNK_SIZE] for i in range(0, len(text), cls.CHUNK_SIZE)]

        parts = text.split(sep)
        result: List[str] = []

        for part in parts:
            part = part.strip() if sep != " " else part
            if not part:
                continue
            if len(part) <= cls.CHUNK_SIZE:
                result.append(part)
            else:
                # 这片还太大，降级用下一级分隔符继续拆
                result.extend(cls._split_recursive(part, level + 1))

        return result

    def _merge_greedy(self, pieces: List[str]) -> List[str]:
        """
        贪心合并：遍历碎片，尽可能把相邻片凑到接近 CHUNK_SIZE。

        片与片之间用空格连接。
        """
        if not pieces:
            return []

        merged: List[str] = []
        bag: List[str] = []

        for piece in pieces:
            if not piece:
                continue

            # 模拟加上这片后的总长度（空格连接）
            if bag:
                candidate = sum(len(p) for p in bag) + len(bag) + len(piece)
            else:
                candidate = len(piece)

            if candidate <= self.CHUNK_SIZE:
                bag.append(piece)
            else:
                # 当前 bag 已经够大了，结块
                if bag:
                    merged.append(" ".join(bag))
                bag = [piece]

        # 尾巴
        if bag:
            merged.append(" ".join(bag))

        return merged

    def _add_overlap(self, chunks: List[str]) -> List[str]:
        """
        相邻块之间重叠 OVERLAP 字符。

        chunk[i] = chunk[i-1][-OVERLAP:] + " " + chunk[i]（i > 0）
        """
        if len(chunks) <= 1:
            return chunks

        result = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-self.OVERLAP:]
            result.append(prev_tail + " " + chunks[i])

        return result

    @classmethod
    def _split_preserve_tables(cls, text: str) -> List[Tuple[bool, str]]:
        """
        提取 [表格开始]...[表格结束] 包裹的区域作为原子块，
        保证表格不被递归切分器拆散。

        返回 [(is_table, segment), ...] 混合列表，
        is_table=True 的段是完整的表格内容，跳过递归拆分。
        """
        pattern = r'(\[表格开始\]\n.*?\n\[表格结束\])'
        parts = re.split(pattern, text, flags=re.DOTALL)
        result: List[Tuple[bool, str]] = []
        for part in parts:
            if not part:
                continue
            if part.startswith('[表格开始]') and part.endswith('[表格结束]'):
                inner = part[len('[表格开始]\n'):-len('\n[表格结束]')]
                result.append((True, inner))
            else:
                result.append((False, part))
        return result

    # ══════════════════════════════════════════════════════════════════════════
    # 检索内部方法
    # ══════════════════════════════════════════════════════════════════════════

    def _vector_search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """纯 ChromaDB 向量语义检索（使用 ChromaDB 内置嵌入模型）。"""
        if self._collection.count() == 0:
            return []

        n = min(top_k, self._collection.count())

        # 由 ChromaDB 内置模型完成嵌入 + 检索
        results = self._collection.query(
            query_texts=[query],
            n_results=n,
        )

        items: List[Dict[str, Any]] = []
        if results["documents"] and results["documents"][0]:
            result_ids   = results.get("ids", [[""] * n])[0]
            result_docs  = results["documents"][0]
            result_metas = results["metadatas"][0] if results["metadatas"] else [{}] * n
            result_dists = results["distances"][0] if results["distances"] else [1.0] * n

            for i in range(len(result_docs)):
                meta = result_metas[i] if i < len(result_metas) else {}
                items.append({
                    "id":           result_ids[i] if i < len(result_ids) else "",
                    "title":        meta.get("title", ""),
                    "content":      result_docs[i] if i < len(result_docs) else "",
                    "score":        round(1.0 - result_dists[i], 4) if i < len(result_dists) else 0.0,
                    "chunk_index":  meta.get("chunk_index", 0),
                })

        return items

    def _bm25_lookup(self, raw: List[Tuple[str, float]]) -> List[Dict[str, Any]]:
        """
        BM25 返回的是 (doc_id, score)，需要从 ChromaDB 补充元数据。
        如果 ChromaDB 查询失败（极端情况），用 BM25 自身的数据兜底。
        """
        results: List[Dict[str, Any]] = []
        for doc_id, bm25_score in raw:
            try:
                result = self._collection.get(ids=[doc_id])
                if result["documents"]:
                    meta = result["metadatas"][0] if result["metadatas"] else {}
                    results.append({
                        "id":          doc_id,
                        "title":       meta.get("title", ""),
                        "content":     result["documents"][0],
                        "score":       bm25_score,
                        "chunk_index": meta.get("chunk_index", 0),
                    })
                    continue
            except Exception:
                pass
            # 兜底
            results.append({
                "id":      doc_id,
                "title":   "",
                "content": self._bm25._docs.get(doc_id, ""),
                "score":   bm25_score,
            })
        return results

    # ══════════════════════════════════════════════════════════════════════════
    # RRF 融合
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _rrf_fusion(*rankings: List[Dict[str, Any]], k: int = 60) -> List[Dict[str, Any]]:
        """
        Reciprocal Rank Fusion（倒数排名融合）。

        公式: RRF_score(d) = Σ_{r in rankings} 1 / (k + rank_r(d))

        其中 k 是平滑常数（默认 60），rank_r(d) 是文档 d 在排名 r 中的位置（1-indexed）。

        不需要对向量分和 BM25 分做归一化——只依赖排名，天然适合异源检索结果合并。
        """
        scores: Dict[str, float] = {}
        docs: Dict[str, Dict[str, Any]] = {}

        for ranking in rankings:
            for rank, doc in enumerate(ranking, start=1):
                doc_id = doc.get("id", "")
                if not doc_id:
                    continue
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
                if doc_id not in docs:
                    docs[doc_id] = dict(doc)

        sorted_ids = sorted(scores, key=scores.get, reverse=True)  # type: ignore[arg-type]
        result: List[Dict[str, Any]] = []
        for doc_id in sorted_ids:
            doc_copy = dict(docs[doc_id])
            doc_copy["rrf_score"] = round(scores[doc_id], 4)
            result.append(doc_copy)

        return result

    # ══════════════════════════════════════════════════════════════════════════
    # 默认文档
    # ══════════════════════════════════════════════════════════════════════════

    def _load_default_docs(self) -> None:
        """导入默认知识库文档（客服场景常见问题）。"""
        default_docs = [
            {
                "title": "退款政策",
                "content": (
                    "退款政策说明。"
                    "用户在购买后 7 天内可以申请无理由退款。"
                    "退款申请提交后，系统会在 1-3 个工作日内审核。"
                    "审核通过后，款项将在 5-7 个工作日内退回原支付账户。"
                    "如果商品已发货，需要先完成退货流程才能退款。"
                    "退货运费由用户承担，除非是商品质量问题。"
                    "超过 7 天但未超过 30 天的订单，需要提供商品质量问题的证据才能退款。"
                ),
            },
            {
                "title": "订单查询",
                "content": (
                    "订单查询指南。"
                    "用户可以通过订单号查询订单状态。"
                    "订单状态包括：待支付、已支付、已发货、运输中、已签收、已完成。"
                    "如果订单显示已发货但超过 7 天未收到，可以联系客服申请查件。"
                    "物流信息通常在发货后 24 小时内更新。"
                    "如果订单显示异常，请提供订单号联系客服处理。"
                ),
            },
            {
                "title": "账户安全",
                "content": (
                    "账户安全说明。"
                    "建议用户定期修改密码，密码长度至少 8 位，包含字母和数字。"
                    "如果忘记密码，可以通过绑定的手机号或邮箱重置。"
                    "发现账户异常登录时，系统会自动锁定账户并发送通知。"
                    "用户可以在安全设置中开启两步验证，提高账户安全性。"
                    "不要将密码分享给他人，客服人员不会索要用户密码。"
                ),
            },
            {
                "title": "技术故障排查",
                "content": (
                    "常见技术问题排查。"
                    "应用崩溃：请尝试清除缓存后重启应用，如果问题持续请更新到最新版本。"
                    "登录失败 401 错误：表示认证失败，请检查用户名密码是否正确，或尝试重置密码。"
                    "页面加载慢：检查网络连接，尝试切换 WiFi 或移动数据。"
                    "支付失败：确认银行卡余额充足，检查是否开启了网上支付功能。"
                    "500 服务器错误：这是服务端问题，请稍后重试，如果持续出现请联系技术支持。"
                ),
            },
            {
                "title": "会员与积分",
                "content": (
                    "会员积分规则。"
                    "每消费 1 元累积 1 积分。"
                    "积分可以在下次购物时抵扣，100 积分 = 1 元。"
                    "会员等级分为：普通会员、银卡会员（累计消费 1000 元）、金卡会员（累计消费 5000 元）。"
                    "银卡会员享受 95 折优惠，金卡会员享受 9 折优惠。"
                    "积分有效期为 1 年，过期自动清零。"
                    "生日当月消费可获得双倍积分。"
                ),
            },
            {
                "title": "配送说明",
                "content": (
                    "配送服务说明。"
                    "标准配送：3-5 个工作日送达，免运费（订单满 99 元）。"
                    "加急配送：1-2 个工作日送达，运费 15 元。"
                    "同城配送：当日达或次日达，运费 10 元。"
                    "偏远地区可能需要额外 2-3 天。"
                    "配送时间为每天 9:00-18:00，节假日可能延迟。"
                    "如果需要修改收货地址，请在发货前联系客服。"
                ),
            },
        ]
        self.add_documents(default_docs)
        logger.info(
            f"已导入默认知识库: {len(default_docs)} 篇文档 → "
            f"{self.doc_count} 个片段（BM25: {self.bm25_doc_count} 篇）"
        )
