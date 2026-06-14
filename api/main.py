"""
EchoMind 智能客服系统 — FastAPI 入口

启动时打印小熊饼干图案。
所有核心组件在 lifespan 中初始化，通过环境变量配置。
"""
import asyncio
import json
import logging
import os
import pathlib
import sys
import time as _time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

# 将项目根目录加入 sys.path，确保无论从哪里执行都能找到 agents/core/memory 等模块
# 这一行必须在所有项目内部 import 之前执行
_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BANNER = r"""
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
   ╔══════════════════════╗
   ║   EchoMind  v2.0     ║
   ║   智能客服 AI 系统    ║
   ╚══════════════════════╝
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_orchestrator = None
_memory       = None
_tool_manager = None
_monitor      = None
_evaluator    = None


def _anthropic_cfg() -> Dict[str, Any]:
    key = "sk-92f09f3ada494ecd8390763ff293906b"
    if not key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY")
    cfg: Dict[str, Any] = {
        "api_key":  key,
        "model":    os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        cfg["base_url"] = base_url
    return cfg


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator, _memory, _tool_manager, _monitor, _evaluator

    print(BANNER, flush=True)

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from core.intent_recognizer import IntentRecognizer
    from evaluation.evaluator import EndToEndEvaluator
    from mcp.knowledge_base import KnowledgeBase
    from mcp.tool_manager import MCPToolManager, Tool
    from memory.conversation_memory import MemoryManager
    from monitor.performance_monitor import PerformanceMonitor

    cfg = _anthropic_cfg()
    logger.info(f"模型: {cfg['model']}  base_url: {cfg.get('base_url', '(官方)')}")

    # 意图识别器（Orchestrator 内部也会创建，这里单独暴露给 Evaluator）
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # Agent 编排器（含留资能力）
    _orchestrator = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
    )

    # 记忆管理器（Redis 工作记忆 + ChromaDB 情景记忆/用户画像）
    _memory = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # MCP 工具管理器 + RAG 知识库（基于 ChromaDB 的真实检索）
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )
    kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
    )
    logger.info(f"知识库已加载: {kb.doc_count} 个文档片段")

    def knowledge_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        query = params.get("query", "")
        return [{
            "title": "知识库降级结果",
            "content": f"知识库暂时不可用，未能完成对“{query}”的语义检索。请稍后重试，或转人工客服确认。",
            "score": 0.0,
            "fallback": True,
            "error": error,
        }]

    _tool_manager.register(Tool(
        name="knowledge_search",
        description="搜索知识库（基于 ChromaDB 向量检索）",
        handler=kb.search_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        },
        cache_ttl=300.0,
        supports_rerank=True,
        fallback=knowledge_fallback,
    ))

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None
    _monitor = PerformanceMonitor(
        orchestrator=_orchestrator,
        tool_manager=_tool_manager,
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,
        prometheus_port=prom_port,
    )
    await _monitor.start()

    # 评测器
    _evaluator = EndToEndEvaluator(
        orchestrator=_orchestrator,
        recognizer=recognizer,
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        baseline_path=os.getenv("EVAL_BASELINE_PATH", "/app/data/eval/baseline.json"),
    )

    logger.info("EchoMind 已就绪")
    yield

    await _monitor.stop()
    logger.info("EchoMind 已关闭")


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="EchoMind 智能客服",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:     str
    user_id:     str = "anonymous"
    conv_id:     Optional[str] = None


class ChatResponse(BaseModel):
    conv_id:     str
    response:    str
    intent:      str
    sentiment:   str = "neutral"
    agent_type:  str
    escalated:   bool
    ask_contact: bool = False
    latency_ms:  float
    knowledge_used: bool = False


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return {"status": "ok", "agents": _orchestrator.get_stats()}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    主对话接口。完整流程：
      记忆读取 → 意图识别 → (业务类意图 → RAG) → Agent 路由 → 执行 → 记忆写入
    """
    if _orchestrator is None or _memory is None:
        raise HTTPException(503, "服务未就绪")

    from agents.agent_orchestrator import Request as OrcReq
    from core.intent_recognizer import IntentCategory
    from memory.conversation_memory import MsgRole

    conv_id = req.conv_id or str(uuid.uuid4())

    # 1. 读取记忆上下文
    mem_ctx = await _memory.get_context(req.user_id, conv_id, query=req.message)

    # 2. 构建对话历史（供意图识别 + Agent 使用）
    history = [
        {"role": m.role.value, "content": m.content}
        for m in mem_ctx.recent_messages[-5:]
    ] if mem_ctx.recent_messages else None

    # 3. 意图识别（先于 RAG，用真实 intent 替代硬编码门控）
    intent_result = await _orchestrator.recognize_intent(req.message, history)

    # 持久化意图识别结果，用于后续模型训练
    _append_intent_log(
        message=req.message,
        intent=intent_result.intent.value,
        sentiment=intent_result.sentiment.value,
        confidence=intent_result.confidence,
        user_id=req.user_id,
    )

    # 4. 只有明确不需要知识库的意图才跳过（GREETING/CHITCHAT/联系方式类）
    _SKIP_RAG = {IntentCategory.GREETING, IntentCategory.CHITCHAT,
                 IntentCategory.CONTACT_GIVE, IntentCategory.CONTACT_FIX}
    knowledge_text, knowledge_used = "", False
    if intent_result.intent not in _SKIP_RAG:
        knowledge_text, knowledge_used = await _build_knowledge_context(req.message)

    # 5. 构建上下文：记忆 + 知识库
    context_parts = [mem_ctx.to_prompt_text()]
    if knowledge_text:
        context_parts.append(knowledge_text)
    full_context = "\n\n".join(part for part in context_parts if part)

    # 6. 构建编排请求（预填意图，orchestrator 会跳过二次识别）
    orch_req = OrcReq(
        message=req.message,
        user_id=req.user_id,
        conv_id=conv_id,
        context=full_context,
        history=history,
        intent=intent_result.intent,
        sentiment=intent_result.sentiment,
        urgency=intent_result.urgency,
    )

    # 7. 执行
    result = await _orchestrator.run(orch_req)

    # 8. 如果用户给出了联系方式，自动存储线索
    if result.intent and result.intent.value == "contact_give":
        import re as _re
        phones = _re.findall(r"1[3-9]\d{9}", req.message)
        wechat_match = _re.search(r"微信[号:\s]*([a-zA-Z]\w+)", req.message)
        phone = phones[0] if phones else ""
        wechat = wechat_match.group(1) if wechat_match else ""
        if phone or wechat:
            _orchestrator.store_lead(req.user_id, phone=phone, wechat=wechat)

    # 9. 写入记忆
    await _memory.add_message(req.user_id, conv_id, MsgRole.USER, req.message)
    await _memory.add_message(req.user_id, conv_id, MsgRole.ASSISTANT, result.response)

    # 10. 异步更新用户画像（含 sentiment 历史）
    asyncio.create_task(_memory.update_profile(req.user_id, conv_id))

    return ChatResponse(
        conv_id=conv_id,
        response=result.response,
        intent=result.intent.value if result.intent else "chitchat",
        sentiment=result.sentiment.value if result.sentiment else "neutral",
        agent_type=result.agent_type.value,
        escalated=result.escalated,
        ask_contact=result.ask_contact,
        latency_ms=round(result.latency_ms, 1),
        knowledge_used=knowledge_used,
    )


async def _build_knowledge_context(message: str, top_k: int = 3) -> tuple[str, bool]:
    """
    为 /chat 主链路构建 RAG 知识上下文。

    调用方（/chat）已根据意图做了门控，本函数不再重复判断。
    复用 MCPToolManager 的查询改写、并行召回、重排、fallback 能力。
    """
    if _tool_manager is None:
        return "", False
    try:
        result = await _tool_manager.search_with_rewrite("knowledge_search", message, top_k=top_k)
        if not result.success or not isinstance(result.data, list) or not result.data:
            return "", False

        parts = ["[知识库检索结果]"]
        used = False
        for i, item in enumerate(result.data[:top_k], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "未命名文档"))
            content = str(item.get("content", "")).strip()
            score = item.get("score", "")
            if not content:
                continue
            used = True
            parts.append(f"{i}. 标题: {title}\n   相关度: {score}\n   内容: {content[:600]}")

        if not used:
            return "", False
        parts.append("请优先依据以上知识库内容回答；如果知识库内容不足，再结合通用客服能力说明。")
        return "\n".join(parts), True
    except Exception as ex:
        logger.warning(f"构建知识库上下文失败: {ex}")
        return "", False


def _append_intent_log(message: str, intent: str, sentiment: str,
                       confidence: float, user_id: str) -> None:
    """持久化 intent 识别结果（JSONL），用于后续模型训练。"""
    log_path = pathlib.Path(_ROOT) / "data" / "intent_logs.jsonl"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "message":    message,
            "intent":     intent,
            "sentiment":  sentiment,
            "confidence": round(confidence, 4),
            "user_id":    user_id,
            "timestamp":  _time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as ex:
        logger.warning(f"写入 intent 日志失败: {ex}")


@app.get("/monitor")
async def monitor_summary():
    """实时监控摘要：Agent 成功率、工具统计、告警、优化建议。"""
    if _monitor is None:
        raise HTTPException(503, "服务未就绪")
    return _monitor.summary()


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标入口。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search")
async def search(query: str, top_k: int = 5):
    """
    演示检索优化链路：查询改写 → 并行召回 → 重排 → Top-K。
    展示 MCP 工具调用的核心亮点。
    """
    if _tool_manager is None:
        raise HTTPException(503, "服务未就绪")
    result = await _tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
    return {"query": query, "results": result.data, "reranked": result.reranked}


class DocInput(BaseModel):
    """单篇文档输入。"""
    title:   str
    content: str


class BatchDocInput(BaseModel):
    """批量文档导入请求体。"""
    documents: List[DocInput]


class EvalIntentInput(BaseModel):
    """意图识别评测用例。"""
    message: str
    expected_intent: str
    context: Optional[Dict[str, Any]] = None


class EvalDialogInput(BaseModel):
    """对话质量评测用例。question 单轮，turns 多轮。"""
    question: Optional[str] = None
    turns: Optional[List[str]] = None
    user_id: Optional[str] = None
    conv_id: Optional[str] = None


class EvalRunInput(BaseModel):
    """评测请求。为空时使用内置默认用例。"""
    intent_cases: Optional[List[EvalIntentInput]] = None
    dialog_cases: Optional[List[EvalDialogInput]] = None


@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge(body: BatchDocInput):
    """
    批量导入文档到知识库。

    文档会自动切片（每片 500 字）并存入 ChromaDB / BM25 索引。
    返回每篇文档的 doc_id，可用于后续按文档删除。

    示例请求体：
    ```json
    {
      "documents": [
        {"title": "退款政策", "content": "用户在购买后 7 天内可以申请无理由退款..."},
        {"title": "配送说明", "content": "标准配送 3-5 个工作日..."}
      ]
    }
    ```
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    result = kb.add_documents([{"title": d.title, "content": d.content} for d in body.documents])
    return {
        "message": f"成功导入 {result['added_chunks']} 个文档片段",
        "added_chunks": result["added_chunks"],
        "doc_ids": result["doc_ids"],
        "total_chunks": kb.doc_count,
    }


@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(file: UploadFile = File(...)):
    """
    上传文件导入知识库。

    支持格式：
    - `.pdf`：自动提取文本（pdfplumber）
    - `.docx`：自动提取文本（python-docx）
    - `.txt` / `.md`：整个文件作为一篇文档，文件名作为标题
    - `.json`：JSON 数组格式 `[{"title": "...", "content": "..."}, ...]`

    文件大小限制：10MB
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件大小超过 10MB 限制")

    filename = file.filename or "unknown"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    title = filename.rsplit(".", 1)[0] if "." in filename else filename

    if ext == "pdf":
        docs = _parse_pdf(content, title)
    elif ext == "docx":
        docs = _parse_docx(content, title)
    elif ext == "json":
        docs = _parse_json_upload(content)
    else:
        # txt / md：整个文件作为一篇文档
        text = content.decode("utf-8", errors="ignore")
        docs = [{"title": title, "content": text}]

    result = kb.add_documents(docs)
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": result["added_chunks"],
        "doc_ids": result["doc_ids"],
        "total_chunks": kb.doc_count,
        "source_docs": len(docs),
    }


def _parse_pdf(raw: bytes, title: str) -> List[Dict[str, str]]:
    """用 pdfplumber 提取 PDF 文本 + 表格（表格转 markdown）。"""
    import io

    import pdfplumber

    docs: List[Dict[str, str]] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            parts: List[str] = []

            # 1. 文本
            text = page.extract_text()
            if text and text.strip():
                parts.append(text.strip())

            # 2. 表格 → markdown
            tables = page.extract_tables()
            if tables:
                for t in tables:
                    md = _table_to_markdown(t)
                    if md:
                        parts.append(md)

            if parts:
                doc_title = f"{title}_p{i}" if len(pdf.pages) > 1 else title
                docs.append({"title": doc_title, "content": "\n\n".join(parts)})

    if not docs:
        raise HTTPException(400, f"PDF 文件中未提取到文本内容")

    logger.info(f"PDF 解析完成: {title} → {len(docs)} 页")
    return docs


def _parse_docx(raw: bytes, title: str) -> List[Dict[str, str]]:
    """
    用 python-docx 提取 Word 文档文本 + 表格（表格转 markdown）。

    遍历 document body 保持段落和表格的原始顺序。
    """
    import io

    from docx import Document

    doc = Document(io.BytesIO(raw))
    parts: List[str] = []

    for child in doc.element.body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

        if tag == "p":
            # 段落
            from docx.text.paragraph import Paragraph
            para = Paragraph(child, doc)
            text = para.text.strip()
            if text:
                parts.append(text)

        elif tag == "tbl":
            # 表格 → markdown
            from docx.table import Table
            table = Table(child, doc)
            rows: List[List[str]] = []
            for row in table.rows:
                rows.append([cell.text.strip() for cell in row.cells])
            md = _table_to_markdown(rows)
            if md:
                parts.append(md)

    if not parts:
        raise HTTPException(400, f"Word 文档中未提取到文本内容")

    content = "\n\n".join(parts)
    logger.info(f"DOCX 解析完成: {title} → {len(parts)} 个内容块")
    return [{"title": title, "content": content}]


def _parse_json_upload(raw: bytes) -> List[Dict[str, str]]:
    """解析 JSON 格式的知识库导入文件。"""
    import json as _json

    text = raw.decode("utf-8", errors="ignore")
    try:
        docs = _json.loads(text)
        if not isinstance(docs, list):
            raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")
        return docs
    except _json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 解析失败: {e}")


def _table_to_markdown(rows: List[List[Optional[str]]]) -> str:
    """将二维表格转为 markdown table 字符串。"""
    if not rows:
        return ""

    # 清洗：None → ""，strip
    clean: List[List[str]] = [
        [(cell or "").strip() for cell in row]
        for row in rows
    ]

    # 过滤全空行
    clean = [row for row in clean if any(cell for cell in row)]
    if not clean:
        return ""

    n_cols = max(len(row) for row in clean)
    # 补齐列数不一致的行
    for row in clean:
        while len(row) < n_cols:
            row.append("")

    # 计算列宽
    col_widths = [0] * n_cols
    for row in clean:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    lines: List[str] = []
    for r_idx, row in enumerate(clean):
        line = "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"
        lines.append(line)
        if r_idx == 0:
            # 表头分隔线
            sep = "|" + "|".join("-" * (w + 2) for w in col_widths) + "|"
            lines.append(sep)

    return "\n".join(lines)


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats():
    """查看知识库统计信息（文档片段总数 + 父块数 + BM25 索引数）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    return {
        "total_chunks": kb.doc_count,
        "bm25_docs": kb.bm25_doc_count,
    }


@app.get("/knowledge/list", tags=["知识库"])
async def knowledge_list():
    """返回知识库中所有片段的完整列表（含全文）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    chunks = kb.list_chunks()
    return {
        "total": len(chunks),
        "chunks": chunks,
    }


@app.delete("/knowledge/{doc_id}", tags=["知识库"])
async def knowledge_delete(doc_id: str):
    """按文档 ID 删除一篇文档的所有 chunk（同步清理 ChromaDB + BM25）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    deleted = kb.delete_by_doc_id(doc_id)
    if deleted == 0:
        raise HTTPException(404, f"文档 {doc_id} 不存在或已被删除")
    return {
        "message": f"文档 {doc_id} 已删除",
        "deleted_chunks": deleted,
        "total_chunks": kb.doc_count,
    }


@app.delete("/knowledge", tags=["知识库"])
async def knowledge_clear():
    """清空知识库所有文档（同步清理 ChromaDB + BM25 索引）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    deleted = kb.clear()
    return {
        "message": "知识库已清空",
        "deleted_chunks": deleted,
        "total_chunks": kb.doc_count,
    }


@app.get("/memory/profiles", tags=["记忆"])
async def memory_profiles():
    """返回所有用户画像的完整列表。"""
    if _memory is None:
        raise HTTPException(503, "记忆系统未初始化")
    profiles = _memory.list_profiles()
    return {"total": len(profiles), "profiles": profiles}


@app.get("/memory/episodic", tags=["记忆"])
async def memory_episodic():
    """返回所有情景记忆的完整列表。"""
    if _memory is None:
        raise HTTPException(503, "记忆系统未初始化")
    episodic = _memory.list_episodic()
    return {"total": len(episodic), "episodic": episodic}


@app.post("/eval/run")
async def run_eval(body: Optional[EvalRunInput] = None):
    """运行内置评测用例，返回评测报告。"""
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    from evaluation.evaluator import DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES, IntentTestCase

    if body and body.intent_cases is not None:
        intent_cases = [
            IntentTestCase(
                message=c.message,
                expected_intent=c.expected_intent,
                context=c.context,
            )
            for c in body.intent_cases
        ]
    else:
        intent_cases = DEFAULT_INTENT_CASES

    if body and body.dialog_cases is not None:
        dialog_cases = [
            c.model_dump(exclude_none=True)
            for c in body.dialog_cases
        ]
    else:
        dialog_cases = DEFAULT_DIALOG_CASES

    report = await _evaluator.run(
        intent_cases=intent_cases,
        dialog_cases=dialog_cases,
    )
    return {
        "pass_rate":       report.pass_rate,
        "total":           report.total,
        "passed":          report.passed,
        "avg_scores":      report.avg_scores,
        "regressions":     report.regressions,
        "recommendations": report.recommendations,
        "results": [
            {
                "test_id": r.test_id,
                "passed": r.passed,
                "scores": r.scores,
                "detail": r.detail,
                "metadata": r.metadata,
            }
            for r in report.results
        ],
    }


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    print(BANNER)
    print("EchoMind CLI — 输入 quit 退出\n")

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from memory.conversation_memory import MemoryManager, MsgRole

    cfg = _anthropic_cfg()
    orch = AgentOrchestrator(api_key=cfg["api_key"], base_url=cfg.get("base_url"), model=cfg["model"])
    mem  = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    user_id, conv_id = "cli_user", str(uuid.uuid4())

    while True:
        try:
            msg = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见 ʕ•ᴥ•ʔ")
            break
        if not msg or msg.lower() in ("quit", "exit", "退出"):
            print("再见 ʕ•ᴥ•ʔ")
            break

        ctx = await mem.get_context(user_id, conv_id, query=msg)
        history = [
            {"role": m.role.value, "content": m.content}
            for m in ctx.recent_messages[-5:]
        ] if ctx.recent_messages else None
        req = Request(message=msg, user_id=user_id, conv_id=conv_id, context=ctx.to_prompt_text(), history=history)
        result = await orch.run(req)

        await mem.add_message(user_id, conv_id, MsgRole.USER, msg)
        await mem.add_message(user_id, conv_id, MsgRole.ASSISTANT, result.response)

        print(f"\nEchoMind [{result.agent_type.value}]: {result.response}\n")


if __name__ == "__main__":
    if "--cli" in sys.argv:
        asyncio.run(_cli())
    else:
        uvicorn.run(
            "api.main:app",
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            reload=os.getenv("APP_ENV") == "development",
        )
