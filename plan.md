# EchoMind 优化执行计划

> 整体目标：迁移至 Milvus 统一检索平台 + 多格式文档上传 + 文件 MD5 管理 + 文档切割优化 + 评测体系

---

## 总览

| 阶段 | 内容 | 内核改动 | 耗时 |
|------|------|---------|------|
| 一 | 依赖 & 配置 | requirements.txt, .env, docker-compose.yml | 30min |
| 二 | 知识库 Milvus 化 | mcp/knowledge_base.py（重写） | 2h |
| 三 | 记忆系统 Milvus 化 | memory/conversation_memory.py | 1.5h |
| 四 | API 层 | api/main.py（文件上传+删除入口） | 1h |
| 五 | Docker 构建 | Dockerfile（模型预下载调整） | 30min |
| 六 | 文档切割优化 | mcp/knowledge_base.py（父子分块） | 1h |
| 七 | 评测体系 | evaluation/evaluator.py（Recall@K） | 1h |
| 八 | 验证 | 启动服务、端到端测试 | 1h |

---

## 阶段一：依赖与配置

### 1.1 requirements.txt

**当前 → 目标**：

| 操作 | 包 | 原因 |
|------|-----|------|
| 🔴 移除 | `chromadb==0.5.23` | 不再使用 |
| 🟢 新增 | `pymilvus` | Milvus 客户端 |
| 🟢 新增 | `sentence-transformers` | BGE 嵌入模型 |
| 🟢 新增 | `jieba` | 中文分词（Milvus 分析器备用） |
| 🟢 新增 | `pdfplumber` | PDF 解析（含表格提取） |
| 🟢 新增 | `python-docx` | Word 文档解析 |
| 🟢 新增 | `redis` | 已有但确认保留（去重、工作记忆） |

> sentence-transformers 会拉 `torch` 依赖，构建镜像体积会增加 ~1.5GB。生产环境如果不开 GPU，可考虑用 `onnxruntime` + 转换后的 BGE 模型替代。

### 1.2 .env + .env.example

| 操作 | 变量 | 说明 |
|------|------|------|
| 🔴 移除 | `CHROMA_HOST` | 不再使用 |
| 🔴 移除 | `CHROMA_PORT` | 不再使用 |
| 🔴 移除 | `CHROMA_PERSIST_DIRECTORY` | 不再使用 |
| 🟢 新增 | `MILVUS_HOST=milvus` | Milvus gRPC 地址 |
| 🟢 新增 | `MILVUS_PORT=19530` | Milvus gRPC 端口 |
| 🔵 保留 | `REDIS_URL` | 不变（用于文件 MD5 去重 + 工作记忆） |

### 1.3 docker-compose.yml

**变更清单**：

```yaml
services:
  chromadb:                    # 🔴 移除
    image: chromadb/chroma:0.5.23

  etcd:                        # 🟢 新增 - Milvus 元数据存储
    image: quay.io/coreos/etcd:v3.5.17
    container_name: echomind-etcd
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
      - ETCD_SNAPSHOT_COUNT=50000
    volumes:
      - etcd-data:/etcd
    command: >
      etcd --advertise-client-urls http://0.0.0.0:2379
           --listen-client-urls http://0.0.0.0:2379
           --data-dir /etcd

  minio:                       # 🟢 新增 - 向量数据持久化
    image: minio/minio:latest
    container_name: echomind-minio
    ports:
      - "9000:9000"      # 对象存储 API
      - "9001:9001"      # Web 控制台
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes:
      - minio-data:/minio_data
    command: minio server /minio_data --console-address ":9001"

  milvus:                      # 🟢 新增 - 主服务
    image: milvusdb/milvus:latest
    container_name: echomind-milvus
    ports:
      - "19530:19530"    # gRPC
      - "9091:9091"      # HTTP
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
      # 或者使用本地存储（不依赖 MinIO）
      # LOCAL_STORAGE_ENABLED: true
    volumes:
      - milvus-data:/var/lib/milvus
    depends_on:
      - etcd
      - minio

  attu:                        # 🟢 新增 - Milvus Web UI
    image: zilliz/attu:latest
    container_name: echomind-attu
    ports:
      - "8085:3000"
    environment:
      MILVUS_URL: milvus:19530

  echomind:                    # 🔵 修改
    environment:
      CHROMA_HOST=chromadb     → 移除
      CHROMA_PERSIST_DIRECTORY → 移除
    #  新增:
      - MILVUS_HOST=milvus
      - MILVUS_PORT=19530
    depends_on:
      chromadb  → 移除
      milvus    → 新增

volumes:
  chromadb-data:  → 移除
  etcd-data:      → 新增
  minio-data:     → 新增
  milvus-data:    → 新增
```

**依赖关系**：`milvus` → `etcd` + `minio` → `attu`（可选） → `echomind` → `milvus`

---

## 阶段二：知识库 Milvus 化（核心）

### 文件：`mcp/knowledge_base.py`（重写）

#### 总体设计

```python
class KnowledgeBase:
    """
    基于 Milvus 的 RAG 知识库。
    
    能力：
      - Dense 向量（BGE-base-zh）语义检索
      - Sparse 向量（内置 BM25）关键词检索
      - Hybrid Search + RRF 混合排序
      - 按 file_md5 精准删除
      - 多格式文档导入（txt/md/json/pdf/docx）
    """
```

#### 2.1 初始化

```python
def __init__(
    self,
    milvus_host: str = "localhost",
    milvus_port: int = 19530,
    data_path: str = "./data/chroma",  # BGE 模型缓存用
):
    # 1. 连接 Milvus
    self._client = MilvusClient(f"http://{milvus_host}:{milvus_port}")
    
    # 2. 加载 BGE 嵌入模型
    self._embed_model = SentenceTransformer(
        "BAAI/bge-base-zh-v1.5",
        device="cpu",
        cache_folder=data_path,
    )
    
    # 3. 创建/获取 collection（含 schema 检查）
    self._collection = self._ensure_collection()
```

#### 2.2 Collection Schema

```python
def _ensure_collection(self):
    fields = [
        FieldSchema(name="chunk_id",       dtype=DataType.VARCHAR, max_length=64,  is_primary=True),
        FieldSchema(name="chunk_text",     dtype=DataType.VARCHAR, max_length=2000,
                    enable_analyzer=True,
                    analyzer_params={"type": "chinese"}),
        FieldSchema(name="title",          dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="file_md5",       dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="chunk_index",    dtype=DataType.INT64),
        FieldSchema(name="total_chunks",   dtype=DataType.INT64),
        FieldSchema(name="dense_vector",   dtype=DataType.FLOAT_VECTOR, dim=768),
        FieldSchema(name="sparse_vector",  dtype=DataType.SPARSE_FLOAT_VECTOR),
    ]
    schema = CollectionSchema(fields, "EchoMind 知识库")
    
    # BM25 Function（自动从 chunk_text 生成 sparse_vector）
    bm25 = Function(
        name="bm25",
        function_type=FunctionType.BM25,
        input_field_names=["chunk_text"],
        output_field_names=["sparse_vector"],
    )
    schema.add_function(bm25)
    
    collection = Collection(name="knowledge_base", schema=schema)
    
    # 创建索引
    collection.create_index("dense_vector",  {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 128}})
    collection.create_index("sparse_vector", {"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP"})
    collection.load()
    return collection
```

> ⚠️ **注意**：上述 `Function` API 基于 pymilvus 2.5+，实测时需确认版本兼容性。若不支持，则回退方案为：应用层用 jieba + `rank_bm25` 计算稀疏向量后传入 Milvus。

#### 2.3 文档导入

```python
def add_documents(self, documents: List[Dict[str, str]], file_md5: str = "") -> int:
    """
    批量导入文档。
    
    documents: [{"title": "...", "content": "..."}, ...]
    file_md5: 文件 MD5（上传时由 API 层计算传入）
    """
    ids, texts, titles, dense_vecs = [], [], [], []
    chunk_indices, total_chunks_list = [], []
    
    for doc in documents:
        chunks = self._chunk_text(doc["content"], chunk_size=500)
        for i, chunk in enumerate(chunks):
            chunk_id = hashlib.md5(f"{doc['title']}_{i}_{chunk[:50]}".encode()).hexdigest()
            ids.append(chunk_id)
            texts.append(chunk)
            titles.append(doc["title"])
            chunk_indices.append(i)
            total_chunks_list.append(len(chunks))
    
    # BGE 编码（批量）
    dense_vecs = self._embed_model.encode(texts).tolist()
    
    # 写入 Milvus（sparse_vector 由 BM25 Function 自动生成）
    self._collection.insert([
        ids, texts, titles,
        [file_md5] * len(ids),       # file_md5
        chunk_indices, total_chunks_list,
        dense_vecs,
        [{}] * len(ids),              # sparse_vector 占位（Function 自动填充）
    ])
    
    return len(ids)
```

#### 2.4 混合检索

```python
def search(self, query: str, top_k: int = 5) -> List[Dict]:
    # 1. BGE 编码 query
    query_dense = self._embed_model.encode([query]).tolist()
    
    # 2. Hybrid Search
    dense_req = AnnSearchRequest(query_dense, "dense_vector", {"metric_type": "IP", "params": {"nprobe": 10}}, limit=top_k * 2)
    sparse_req = AnnSearchRequest([query], "sparse_vector", {"metric_type": "IP"}, limit=top_k * 2)
    
    results = hybrid_search(
        self._collection,
        reqs=[dense_req, sparse_req],
        rerank=RRFRanker(),
        limit=top_k,
        output_fields=["chunk_text", "title", "chunk_index", "file_md5"],
    )
    
    # 3. 格式化返回
    items = []
    for hit in results[0]:
        items.append({
            "title":   hit["entity"]["title"],
            "content": hit["entity"]["chunk_text"],
            "score":   round(hit["score"], 4),
            "chunk":   hit["entity"]["chunk_index"],
        })
    return items
```

#### 2.5 按 MD5 删除

```python
def delete_by_file_md5(self, file_md5: str) -> int:
    """删除指定文件的所有 chunk。返回被删数量。"""
    expr = f'file_md5 == "{file_md5}"'
    result = self._collection.query(expr=expr, output_fields=["chunk_id"])
    ids = [r["chunk_id"] for r in result]
    if ids:
        self._collection.delete(expr=expr)
    return len(ids)
```

#### 2.6 文档切割

保留现有 `_chunk_text()` 方法（500 字、句号切分），待阶段六优化。

#### 2.7 默认文档

`_load_default_docs()` 适配新接口，导入方式不变。

---

## 阶段三：记忆系统 Milvus 化

### 文件：`memory/conversation_memory.py`

#### 3.1 初始化变更

| 当前（ChromaDB） | 目标（Milvus） |
|---|---|
| `chromadb.HttpClient(host, port)` | `MilvusClient(uri)` |
| `chroma.get_or_create_collection("episodic")` | 两个 Collection：`episodic`、`user_profile` |
| ChromaDB 内置 auto-embedding | 应用层嵌入（与知识库共用 BGE 模型） |

#### 3.2 MemoryManager 初始化

```python
class MemoryManager:
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        milvus_host: str = "localhost",
        milvus_port: int = 19530,
        api_key: str = "",
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
    ):
        # Redis 不变
        self._redis = redis.from_url(redis_url, decode_responses=True)
        
        # ChromaDB → Milvus
        self._milvus = MilvusClient(f"http://{milvus_host}:{milvus_port}")
        
        # 情景记忆 collection（episodic）
        self._episodic = self._ensure_episodic_collection()
        
        # 用户画像 collection（user_profile）
        self._profile = self._ensure_profile_collection()
        
        # 嵌入模型（轻量方案：用 BGE 或从知识库共享）
        self._embed_fn = self._get_embedding_function()
```

#### 3.3 Collection Schema

**`episodic`**：

```python
fields = [
    FieldSchema("id",        DataType.VARCHAR, max_length=64,  is_primary=True),
    FieldSchema("user_id",   DataType.VARCHAR, max_length=64),
    FieldSchema("conv_id",   DataType.VARCHAR, max_length=64),
    FieldSchema("summary",   DataType.VARCHAR, max_length=2000),
    FieldSchema("full_text", DataType.VARCHAR, max_length=2000),
    FieldSchema("ts",        DataType.VARCHAR, max_length=64),
    FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=768),
]
```

**`user_profile`**：

```python
fields = [
    FieldSchema("id",      DataType.VARCHAR, max_length=64,  is_primary=True),
    FieldSchema("user_id", DataType.VARCHAR, max_length=64),
    FieldSchema("conv_id", DataType.VARCHAR, max_length=64),
    FieldSchema("profile", DataType.VARCHAR, max_length=4000),
    FieldSchema("ts",      DataType.VARCHAR, max_length=64),
    FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=768),
]
```

#### 3.4 关键方法映射

| 当前方法 | ChromaDB 操作 | Milvus 操作 |
|----------|-------------|-------------|
| `_search_episodic` | `query(query_texts, where={"user_id"})` | 编码 query → `search(expr='user_id == ".."')` |
| `_store_episodic` | `add(ids, documents, metadatas)` | 编码文本 → `insert(...)` |
| `_get_profile` | `get(where={"user_id"})` | `query(expr='user_id == ".."')` |
| `update_profile` | `delete(ids) → add(ids, documents)` | `delete(expr=...) → insert(...)` |

#### 3.5 嵌入方案

记忆系统的嵌入与知识库略有不同——不需要 BM25，只需要语义向量。

方案：**与知识库共享 BGE 模型**。为了不互相阻塞，MemoryManager 持有一个独立的 `SentenceTransformer` 实例，或通过一个共享的嵌入服务调用。

```python
async def _embed(self, text: str) -> List[float]:
    """生成文本向量（同步调用 BGE，异步场景下用 run_in_executor 避免阻塞）"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: self._embed_model.encode([text])[0].tolist()
    )
```

---

## 阶段四：API 层

### 文件：`api/main.py`

#### 4.1 知识库初始化变更

```python
# 之前
kb = KnowledgeBase(
    chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
    chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
    chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
)

# 之后
kb = KnowledgeBase(
    milvus_host=os.getenv("MILVUS_HOST", "milvus"),
    milvus_port=int(os.getenv("MILVUS_PORT", "19530")),
    data_path="/app/data/models",  # BGE 模型缓存
)
```

#### 4.2 上传接口 — 支持 PDF/DOCX

**`POST /knowledge/upload` 改造**：

```python
@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(file: UploadFile = File(...)):
    # 1. 读取文件内容（二进制）
    content = await file.read()
    # 10MB 限制不变
    
    # 2. 计算 MD5
    file_md5 = hashlib.md5(content).hexdigest()
    
    # 3. 去重检查（Redis）
    if redis_client.sismember("kb:file_md5s", file_md5):
        return {"message": "文件已存在", "file_md5": file_md5, "dedup": True}
    
    # 4. 根据后缀解析
    docs = parse_file(content, filename)  # 见下方
    
    # 5. 导入知识库（传入 file_md5）
    count = kb.add_documents(docs, file_md5=file_md5)
    
    # 6. 记录 MD5
    redis_client.sadd("kb:file_md5s", file_md5)
    
    return {"file_md5": file_md5, "added_chunks": count, ...}
```

**文件解析函数**：

```python
def parse_file(content: bytes, filename: str) -> List[Dict]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    
    if ext in ("txt", "md"):
        text = content.decode("utf-8", errors="ignore")
        title = filename.rsplit(".", 1)[0]
        return [{"title": title, "content": text}]
    
    elif ext == "json":
        import json
        docs = json.loads(content.decode("utf-8"))
        return docs
    
    elif ext == "pdf":
        import pdfplumber
        import io
        text_parts = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                # 提取表格（转 Markdown）
                tables = page.extract_tables()
                for table in tables:
                    text_parts.append(table_to_markdown(table))
                # 提取文本
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        title = filename.rsplit(".", 1)[0]
        return [{"title": title, "content": "\n".join(text_parts)}]
    
    elif ext == "docx":
        from docx import Document
        import io
        doc = Document(io.BytesIO(content))
        text_parts = []
        for para in doc.paragraphs:
            text_parts.append(para.text)
        for table in doc.tables:
            text_parts.append(table_to_markdown(table))
        title = filename.rsplit(".", 1)[0]
        return [{"title": title, "content": "\n".join(text_parts)}]
    
    else:
        raise HTTPException(400, f"不支持的文件格式: .{ext}")
```

**表格转 Markdown 辅助函数**：

```python
def table_to_markdown(table) -> str:
    """将 pdfplumber/docx 表格转为 Markdown 表格字符串。"""
    rows = []
    for row in table.rows if hasattr(table, 'rows') else table:
        cells = [cell.strip() if cell else "" for cell in 
                 ([row[i].text for i in range(len(row))] if hasattr(row, 'text') else row)]
        rows.append("| " + " | ".join(cells) + " |")
    if rows:
        # 表头分隔线
        col_count = len(rows[0].split("|")) - 2
        rows.insert(1, "|" + "|".join([" --- "] * col_count) + "|")
    return "\n".join(rows)
```

#### 4.3 删除接口

```python
class DeleteKnowledgeInput(BaseModel):
    file_md5: str

@app.post("/knowledge/delete", tags=["知识库"])
async def delete_knowledge(body: DeleteKnowledgeInput):
    """
    根据文件 MD5 删除文档。
    
    上传时返回的 file_md5 传入此接口即可删除对应文档的所有片段。
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    
    deleted = kb.delete_by_file_md5(body.file_md5)
    if deleted == 0:
        raise HTTPException(404, f"未找到 file_md5 为 {body.file_md5} 的文档")
    
    # Redis 中移除 MD5 记录
    redis_client.srem("kb:file_md5s", body.file_md5)
    
    return {"message": f"成功删除 {deleted} 个文档片段", "deleted_chunks": deleted}
```

#### 4.4 Redis 客户端

API 层需要获取 Redis 连接（用于 MD5 去重）。两种做法：

**方案 A**：从 `_memory` 引用 Redis（复用）  
**方案 B**：在 lifespan 中单独初始化一个 Redis 连接  

推荐方案 A，避免重复连接：

```python
# 在 lifespan 中（KnowledgeBase 初始化后）：
_kb_redis = _memory._redis  # 复用 MemoryManager 的 Redis 连接
```

---

## 阶段五：Docker 构建

### 文件：`Dockerfile`

#### 5.1 移除 ChromaDB ONNX 预下载

```dockerfile
# 🔴 移除
RUN mkdir -p /root/.cache/chroma/onnx_models/all-MiniLM-L6-v2 && \
    curl -L ... all-MiniLM-L6-v2/onnx.tar.gz
```

#### 5.2 预下载 BGE 模型

```dockerfile
# 🟢 新增
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('BAAI/bge-base-zh-v1.5')"
```

> ⚠️ BGE-base-zh 约 1.1GB，下载较慢。可以接受的话也可去掉预下载，让服务启动时首次调用下载——但会导致初次 healthcheck 失败。建议构建时预下载。

#### 5.3 工作目录调整

```dockerfile
# 之前的 ChromaDB 缓存路径
COPY --from=dependencies /root/.cache/chroma ...  # 🔴 移除

# 新增 BGE 模型缓存
COPY --from=dependencies /root/.cache/huggingface /home/echomind/.cache/huggingface
```

---

## 阶段六：文档切割优化（父子分块 + auto-merging）

> 此阶段与 Milvus 迁移无直接依赖，可在阶段二完成后并行进行。

### 文件：`mcp/knowledge_base.py`

替换现有 `_chunk_text()` 为父子分块方案。

### 总流程

```
输入文档
  │
  ▼ Step 1
递归切分 → 按标点优先级拆成基础片段
  │
  ▼ Step 2
构建 Parent chunks（1500 字上限，顺序累积）
  │
  ▼ Step 3
在每个 Parent 内部构建 Child chunks（600 字上限）
  │
  ▼
输出 parent-child 映射关系
```

---

### 6.1 Step 1：递归切分（基础切分层）

对文本执行递归切分，标点优先级如下（从上到下逐级尝试）：

```
 ① 段落       \n\n                ← 最高优先级
 ② 句号/叹号  。！？               ← 完整句子结束
 ③ 分号       ；;                 ← 从句/列举项间自然停顿
 ④ 逗号       ，,                 ← 短语级停顿
 ⑤ 字数硬切                       ← 极限兜底
```

**规则**：
- 只有当片段长度超过当前目标阈值时才继续递归切分
- 若 ≤ 阈值则直接返回该片段
- 保持原文顺序，不允许重排

---

### 6.2 Step 2：构建 Parent chunks（1500 字）

**目标**：构建语义连续的上下文窗口，供 auto-merging 时提供完整上下文。

**规则**：
- 输入为 Step 1 递归切分后的基础片段列表
- 按原始顺序依次累积拼接
- 当前 parent + 下一个片段 ≤ 1500 字则继续加入
- 超过 1500 字则 flush 当前 parent，开启新 parent
- 只允许顺序填充，不允许最优拼接或重排

**输出**：
```python
{
  "parent_id": "md5_title_0",
  "parent_text": "...",   # ≤1500 字
  "index": 0,
}
```

---

### 6.3 Step 3：构建 Child chunks（600 字）

**目标**：构建检索粒度的子块，用于向量嵌入和 BM25 索引。

**规则**：
- 在每个 parent 内部独立执行递归切分（复用 Step 1 的切分逻辑）
- 目标阈值：**600 字**
- 优先按标点边界切分（\n\n → 。！？ → ；; → ，, → 字数硬切）
- 若 parent 本身 ≤ 600 字，则 child = parent（不再切分）
- **child 不能跨 parent**——每个 child 必须隶属于唯一 parent

**输出**：
```python
{
  "child_id": "md5_title_0_0",
  "parent_id": "md5_title_0",
  "child_text": "...",    # ≤600 字
  "index": 0,
}
```

---

### 6.4 强约束规则

1. parent chunk 必须保持原始文档顺序
2. child chunk 必须隶属于唯一 parent
3. child 不允许跨 parent 边界
4. 不允许改变 Step 1 递归切分的基础片段顺序
5. 所有切分必须优先保证标点语义边界，字数硬切是最后兜底
6. 所有长度约束必须严格满足上限

---

### 6.5 Auto-merging（检索阶段）

检索时只对 child chunks 做搜索，命中后按 parent_id 合并：

```
Hybrid Search 召回 Top-N（只召 child）
  │
  ▼
按 parent_id 分组
  │
  ├── 某 parent 只有 1 个 child 命中
  │     → 直接返回该 child_text
  │
  └── 某 parent 有 ≥ 2 个 child 命中
        → 在 Milvus 中查该 parent 的 parent_text
        → 用 parent_text 替换这些 child，合并为 1 条
```

### 6.6 存储结构

Milvus Collection 中增加字段以区分父子类型：

| 字段 | 类型 | 说明 |
|------|------|------|
| `chunk_id` | VARCHAR(64) PK | MD5(title + type + index) |
| `chunk_text` | VARCHAR(4096) | 父块或子块内容 |
| `type` | VARCHAR(16) | `"parent"` 或 `"child"` |
| `parent_id` | VARCHAR(64) | 子块指向父块，父块指向自身 |
| `title` | VARCHAR(256) | 文档标题 |
| `file_md5` | VARCHAR(64) | 文件 MD5 |
| `dense_vector` | FLOAT_VECTOR[768] | BGE 语义向量 |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | BM25 关键词向量 |

> 删除时仍按 `file_md5` 过滤，与 parent-child 结构无关。
> 子块参与混合检索，父块仅供 auto-merging 查询用。

### 6.7 与阶段二的集成

`add_documents()` 中原有的 `_chunk_text()` 替换为三步骤：

```python
# 替换前
chunks = self._chunk_text(text, chunk_size=500)

# 替换后
parent_child_pairs = self._chunk_parent_child(
    text,
    parent_max=1500,
    child_max=600,
)
# 返回 [{"parent": {...}, "children": [{...}, ...]}, ...]
```

---

## 阶段七：评测体系

> 与 Milvus 迁移无直接依赖，可独立进行。

### 文件：`evaluation/evaluator.py`

新增 `RetrievalEvaluator`，计算 Recall@K。

详细方案见 `优化.md` 第三节。

---

## 阶段八：验证

### 8.1 启动检查

```bash
docker compose up -d

# 确认所有服务健康
docker compose ps
# 应看到：redis, etcd, minio, milvus, attu, echomind, nginx, prometheus 全部 running

# 检查 Milvus Web UI
open http://localhost:8085
```

### 8.2 接口测试

```bash
# 上传 TXT
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@test.txt"

# 上传 PDF
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@test.pdf"

# 上传 DOCX
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@test.docx"

# 上传重复文件（应返回 dedup=True）
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@test.txt"

# 删除文档
curl -X POST http://localhost:8000/knowledge/delete \
  -H "Content-Type: application/json" \
  -d '{"file_md5": "xxx"}'

# 检索
curl -X POST "http://localhost:8000/search?query=退款&top_k=3"

# 对话
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "退款多久到账", "user_id": "test"}'
```

### 8.3 验收标准

| 检查项 | 预期 |
|--------|------|
| 知识库上传（txt/md/json/pdf/docx） | 返回 chunk 数正确，有 file_md5 |
| 文件去重 | 重复上传返回 `dedup: true`，chunk 不重复 |
| 文件删除 | 按 MD5 删除后检索不到该文档内容 |
| Dual-search | "退款"同时命中语义和关键词 |
| 记忆系统 | 多轮对话正常，压缩正常 |
| 记忆检索 | 跨会话检索正常 |
| Milvus Attu | 8085 端口可访问，能看到 collection 和数据 |

---

## 风险与注意事项

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| 1 | BGE 模型 1.1GB，Docker 构建慢 | 构建时间 +5min | 第一次构建后缓存 layer；开发环境可去掉预下载 |
| 2 | pymilvus `Function` API 版本差异 | 可能不支持 `BM25` | 准备回退方案：应用层 jieba + rank_bm25 计算稀疏向量 |
| 3 | 现有 ChromaDB 数据不迁移 | 已有知识库丢失 | 文档写清楚，需重新导入；`_load_default_docs()` 会重建默认文档 |
| 4 | 记忆数据不迁移 | 历史对话丢失 | 开发/测试环境影响不大；生产环境可写迁移脚本批量读取 ChromaDB → 写入 Milvus |
| 5 | sentence-transformers + torch 增加镜像体积 | 镜像 ~2GB → ~4GB | 可接受；后期可切 ONNX 推理优化 |
