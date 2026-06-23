"""
EchoMind 智能客服系统 — FastAPI 入口（新架构 v2）

在 lifespan 中初始化所有核心组件：AgentEngine、MemoryManager、KnowledgeBase 等。
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

# 将项目根目录加入 sys.path
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
_engine     = None  # AgentEngine（新架构）
_memory     = None
_kb         = None


API_KEY = "sk-92f09f3ada494ecd8390763ff293906b"
BASE_URL = "https://api.deepseek.com/anthropic"
MODEL = "deepseek-chat"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine, _memory, _kb

    print(BANNER, flush=True)

    from agents.orchestrator import AgentEngine
    from mcp.knowledge_base import KnowledgeBase
    from memory.conversation_memory import MemoryManager

    logger.info(f"模型: {MODEL}  base_url: {BASE_URL}")

    # ── 知识库 ──────────────────────────────────────────────────────────
    _kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        embedding_model=EMBEDDING_MODEL,
    )
    logger.info(f"知识库已加载: {_kb.doc_count} 个文档片段（嵌入模型: {EMBEDDING_MODEL}）")

    # ── 新架构引擎 ────────────────────────────────────────────────────
    _engine = AgentEngine(
        knowledge_base=_kb,
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
    )

    # ── 记忆管理器 ────────────────────────────────────────────────────
    _memory = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        api_key=API_KEY,
        base_url=BASE_URL,
        model=MODEL,
    )

    logger.info("EchoMind v2（新架构）已就绪")
    yield

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
    conv_id:         str
    response:        str
    primary_intent:  str
    sub_tasks:       List[str]
    emotion:         str
    skill_statuses:  List[Dict[str, Any]]
    knowledge_used:  bool
    latency_ms:      float


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    if _engine is None:
        raise HTTPException(503, "服务未就绪")
    return {
        "status": "ok",
        "architecture": "v2-plan-skill-orchestrator",
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    主对话接口（新架构 v2）。

    完整流程：
      记忆读取 → AgentEngine（Planner → Orchestrator → Response Agent）→ 记忆写入
    """
    if _engine is None or _memory is None:
        raise HTTPException(503, "服务未就绪")

    from memory.conversation_memory import MsgRole

    conv_id = req.conv_id or str(uuid.uuid4())

    # 1. 读取记忆上下文
    mem_ctx = await _memory.get_context(req.user_id, conv_id, query=req.message)

    # 2. 构建对话历史
    history = [
        {"role": m.role.value, "content": m.content}
        for m in mem_ctx.recent_messages[-5:]
    ] if mem_ctx.recent_messages else None

    # 3. AgentEngine 完整链路（理解 → 编排 → 生成）
    result = await _engine.run(
        message=req.message,
        conv_id=conv_id,
        user_id=req.user_id,
        memory_context=mem_ctx.to_prompt_text(),
        history=history,
    )

    # 4. 持久化意图识别结果（用于后续模型微调）
    _append_intent_log(
        message=req.message,
        primary_intent=result.primary_intent,
        sub_tasks=result.sub_tasks,
        emotion=result.emotion,
        user_id=req.user_id,
        history=history,
    )

    # 5. 写入记忆
    await _memory.add_message(req.user_id, conv_id, MsgRole.USER, req.message)
    await _memory.add_message(req.user_id, conv_id, MsgRole.ASSISTANT, result.response)

    # 6. 异步更新用户画像
    asyncio.create_task(_memory.update_profile(req.user_id, conv_id))

    return ChatResponse(
        conv_id=conv_id,
        response=result.response,
        primary_intent=result.primary_intent,
        sub_tasks=result.sub_tasks,
        emotion=result.emotion,
        skill_statuses=[
            {"name": s.name, "status": s.status, "reason": s.reason}
            for s in result.skill_statuses
        ],
        knowledge_used=result.need_rag,
        latency_ms=round(result.latency_ms, 1),
    )


def _append_intent_log(message: str, primary_intent: str,
                       sub_tasks: List[str], emotion: str,
                       user_id: str,
                       history: Optional[List[Dict[str, str]]] = None) -> None:
    """持久化 Planner 输出（JSONL），用于后续模型微调。"""
    log_path = pathlib.Path(_ROOT) / "data" / "intent_logs.jsonl"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "message":        message,
            "primary_intent": primary_intent,
            "sub_tasks":      sub_tasks,
            "emotion":        emotion,
            "user_id":        user_id,
            "history":        history or [],
            "timestamp":      _time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as ex:
        logger.warning(f"写入 intent 日志失败: {ex}")


# ── 其余路由 ──────────────────────────────────────────────────────────────────

@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标端点。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── 知识库路由 ───────────────────────────────────────────────────────────────

class DocInput(BaseModel):
    title:   str
    content: str


class BatchDocInput(BaseModel):
    documents: List[DocInput]


@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge(body: BatchDocInput):
    if _kb is None:
        raise HTTPException(503, "知识库未初始化")
    result = _kb.add_documents([{"title": d.title, "content": d.content} for d in body.documents])
    message = f"成功导入 {result['added_chunks']} 个文档片段"
    if result.get("skipped"):
        message += f"，跳过 {result['skipped']} 篇重复"
    return {
        "message": message,
        "added_chunks": result["added_chunks"],
        "skipped": result.get("skipped", 0),
        "doc_ids": result["doc_ids"],
        "total_chunks": _kb.doc_count,
    }


@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(file: UploadFile = File(...)):
    if _kb is None:
        raise HTTPException(503, "知识库未初始化")

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
        text = content.decode("utf-8", errors="ignore")
        docs = [{"title": title, "content": text}]

    result = _kb.add_documents(docs)
    message = f"文件 {filename} 导入成功，新增 {result['added_chunks']} 个片段"
    if result.get("skipped"):
        message += f"，跳过 {result['skipped']} 篇重复"
    return {
        "message": message,
        "added_chunks": result["added_chunks"],
        "skipped": result.get("skipped", 0),
        "doc_ids": result["doc_ids"],
        "total_chunks": _kb.doc_count,
        "source_docs": len(docs),
    }


def _parse_pdf(raw: bytes, title: str) -> List[Dict[str, str]]:
    import io
    import pdfplumber

    docs: List[Dict[str, str]] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            parts: List[str] = []
            text = page.extract_text()
            if text and text.strip():
                parts.append(text.strip())
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
        raise HTTPException(400, "PDF 文件中未提取到文本内容")
    return docs


def _parse_docx(raw: bytes, title: str) -> List[Dict[str, str]]:
    import io
    from docx import Document

    doc = Document(io.BytesIO(raw))
    parts: List[str] = []
    for child in doc.element.body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "p":
            from docx.text.paragraph import Paragraph
            para = Paragraph(child, doc)
            text = para.text.strip()
            if text:
                parts.append(text)
        elif tag == "tbl":
            from docx.table import Table
            table = Table(child, doc)
            rows: List[List[str]] = []
            for row in table.rows:
                rows.append([cell.text.strip() for cell in row.cells])
            md = _table_to_markdown(rows)
            if md:
                parts.append(md)
    if not parts:
        raise HTTPException(400, "Word 文档中未提取到文本内容")
    content = "\n\n".join(parts)
    return [{"title": title, "content": content}]


def _parse_json_upload(raw: bytes) -> List[Dict[str, str]]:
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
    if not rows:
        return ""
    clean: List[List[str]] = [
        [(cell or "").strip() for cell in row]
        for row in rows
    ]
    clean = [row for row in clean if any(cell for cell in row)]
    if not clean:
        return ""
    n_cols = max(len(row) for row in clean)
    for row in clean:
        while len(row) < n_cols:
            row.append("")
    col_widths = [0] * n_cols
    for row in clean:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))
    lines: List[str] = []
    for r_idx, row in enumerate(clean):
        line = "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"
        lines.append(line)
        if r_idx == 0:
            sep = "|" + "|".join("-" * (w + 2) for w in col_widths) + "|"
            lines.append(sep)
    return "\n".join(lines)


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats():
    if _kb is None:
        raise HTTPException(503, "知识库未初始化")
    return {"total_chunks": _kb.doc_count, "bm25_docs": _kb.bm25_doc_count}


@app.get("/knowledge/list", tags=["知识库"])
async def knowledge_list():
    if _kb is None:
        raise HTTPException(503, "知识库未初始化")
    return {"total": len(_kb.list_chunks()), "chunks": _kb.list_chunks()}


@app.delete("/knowledge/{doc_id}", tags=["知识库"])
async def knowledge_delete(doc_id: str):
    if _kb is None:
        raise HTTPException(503, "知识库未初始化")
    deleted = _kb.delete_by_doc_id(doc_id)
    if deleted == 0:
        raise HTTPException(404, f"文档 {doc_id} 不存在或已被删除")
    return {"message": f"文档 {doc_id} 已删除", "deleted_chunks": deleted, "total_chunks": _kb.doc_count}


@app.delete("/knowledge", tags=["知识库"])
async def knowledge_clear():
    if _kb is None:
        raise HTTPException(503, "知识库未初始化")
    deleted = _kb.clear()
    return {"message": "知识库已清空", "deleted_chunks": deleted, "total_chunks": _kb.doc_count}


@app.get("/memory/profiles", tags=["记忆"])
async def memory_profiles():
    if _memory is None:
        raise HTTPException(503, "记忆系统未初始化")
    profiles = _memory.list_profiles()
    return {"total": len(profiles), "profiles": profiles}


@app.get("/memory/episodic", tags=["记忆"])
async def memory_episodic():
    if _memory is None:
        raise HTTPException(503, "记忆系统未初始化")
    episodic = _memory.list_episodic()
    return {"total": len(episodic), "episodic": episodic}


# ── Multi-Intent 评估路由 ─────────────────────────────────────────────────────

class MultiEvalInput(BaseModel):
    cases: Optional[List[Dict[str, Any]]] = None  # 不传时默认加载 tests/test.jsonl


_DEFAULT_TEST_PATH = pathlib.Path(__file__).parent.parent / "tests" / "test.jsonl"


def _load_default_test_cases() -> List[Dict[str, Any]]:
    """加载默认的测试用例文件 tests/test.jsonl。"""
    path = _DEFAULT_TEST_PATH
    if not path.exists():
        raise HTTPException(500, f"默认测试文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@app.post("/eval/multi")
async def eval_multi(body: MultiEvalInput):
    """
    Multi-Intent 评估。

    评估维度：
      - Sub-task 多标签分类（Macro F1 + Exact Match Rate）
      - Slot 提取（Macro F1 + Exact Match Rate）

    用例格式：
      {"query": "M8多少钱", "sub_tasks": ["PRICE"], "slots": {"model": "M8"}}

    不传 cases 时默认加载 tests/test.jsonl。
    """
    if _engine is None:
        raise HTTPException(503, "服务未就绪")

    from evaluation.multi_intent_evaluator import MultiIntentEvaluator

    cases = body.cases if body.cases is not None else _load_default_test_cases()
    evaluator = MultiIntentEvaluator(_engine.planner)
    report, _ = await evaluator.eval(cases, detail=True)
    return evaluator.report_to_dict(report, include_details=True)


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    print(BANNER)
    print("EchoMind CLI (v2 架构) — 输入 quit 退出\n")

    from agents.orchestrator import AgentEngine
    from memory.conversation_memory import MemoryManager, MsgRole

    # CLI 模式下知识库可选
    global _kb
    if _kb is None:
        from mcp.knowledge_base import KnowledgeBase
        _kb = KnowledgeBase(
            chroma_host=os.getenv("CHROMA_HOST", "localhost"),
            chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
            chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),
        )

    engine = AgentEngine(
        knowledge_base=_kb,
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    )
    mem = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),
        api_key=API_KEY,
        base_url=BASE_URL,
        model=MODEL,
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

        result = await engine.run(
            message=msg,
            conv_id=conv_id,
            user_id=user_id,
            memory_context=ctx.to_prompt_text(),
            history=history,
        )

        await mem.add_message(user_id, conv_id, MsgRole.USER, msg)
        await mem.add_message(user_id, conv_id, MsgRole.ASSISTANT, result.response)

        status_str = ", ".join(
            f"{s.name}={s.status}" + (f"({s.reason[:20]})" if s.reason else "")
            for s in result.skill_statuses
        )
        print(f"\nEchoMind [{result.primary_intent}] {status_str}: {result.response}\n")


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
