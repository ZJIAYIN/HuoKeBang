"""
EchoMind 智能客服系统 — FastAPI 入口（新架构 v2）

在 lifespan 中初始化所有核心组件：AgentEngine、MemoryManager、KnowledgeBase 等。
"""
import asyncio
import hashlib
import json
import logging
import os
import pathlib
import sys
import time as _time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import redis as _redis_module

# from core.rate_limiter import RequestDedup  # TODO: 去重逻辑待重新设计后启用

# 将项目根目录加入 sys.path
_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# 抑制第三方库调试日志（心跳包等）
for _noisy in ("aiormq", "pamqp", "aio_pika"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

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
_detector   = None  # Prompt Injection 检测器
_work_queue = None  # 请求队列 + 工作协程
_rate_limiter = None  # 限流组件（包含令牌桶 + 用户频控 + 去重）

# 上传去重（Redis Set + MD5）
_redis_dedup  = None
_UPLOAD_DEDUP_KEY = "echomind:upload_doc_ids"

# 体验券系统
_coupon_manager = None
_coupon_decider = None

# MySQL 持久化 + Outbox 扫描
_coupon_db = None
_outbox_scanner = None

# RabbitMQ + 后台 Worker
_rmq_client = None
_coupon_worker = None


API_KEY = "sk-92f09f3ada494ecd8390763ff293906b"
BASE_URL = "https://api.deepseek.com/anthropic"
MODEL = "deepseek-chat"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine, _memory, _kb, _detector, _redis_dedup, _rate_limiter, _work_queue
    global _coupon_manager, _coupon_decider, _coupon_db, _outbox_scanner
    global _rmq_client, _coupon_worker

    print(BANNER, flush=True)

    from agents.orchestrator import AgentEngine
    from mcp.knowledge_base import KnowledgeBase

    logger.info(f"模型: {MODEL}  base_url: {BASE_URL}")

    # ── 上传去重 Redis Set ─────────────────────────────────────────────
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    try:
        _redis_dedup = _redis_module.from_url(redis_url, decode_responses=True)
        logger.info("上传去重 Redis 已连接")
    except Exception:
        logger.warning("Redis 不可用，上传去重降级为 ChromaDB 查询")

    # ── 知识库 ──────────────────────────────────────────────────────────
    _kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
    )
    logger.info(f"知识库已加载: {_kb.doc_count} 个文档片段")

    # ── 记忆管理器（提前初始化，后续 coupon 系统依赖） ──────────────
    from memory.conversation_memory import MemoryManager
    _memory = MemoryManager(
        redis_url=redis_url,
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        api_key=API_KEY,
        base_url=BASE_URL,
        model=MODEL,
    )

    # ── MySQL 连接池（体验券持久化） ──────────────────────────────
    from agents.coupon_db import CouponDB
    _coupon_db = CouponDB()
    db_ok = await _coupon_db.connect()
    if not db_ok:
        logger.warning("MySQL 不可用，体验券持久化降级为纯 Redis 模式")

    # ── 体验券系统（CouponManager + CouponDecider） ──────────────────
    from agents.coupon_manager import CouponManager
    from agents.coupon_decider import CouponDecider

    _coupon_redis = _redis_dedup  # 复用 Redis 连接
    if _coupon_redis is not None:
        _coupon_manager = CouponManager(
            redis_client=_coupon_redis,
            coupon_db=_coupon_db if db_ok else None,
        )
        _coupon_manager.load_scripts()
        await _coupon_manager.init_stock()
        _coupon_decider = CouponDecider()
        logger.info("体验券系统已初始化（CouponManager + CouponDecider）")
    else:
        logger.warning("Redis 不可用，体验券系统已禁用")
        _coupon_decider = CouponDecider()
        _coupon_decider.disable()

    # ── 新架构引擎（传入 coupon_decider） ────────────────────────────
    _engine = AgentEngine(
        knowledge_base=_kb,
        redis_url=redis_url,
        coupon_decider=_coupon_decider,
    )

    # ── RabbitMQ + CouponWorker + OutboxScanner ──────────────────
    from agents.rmq_client import RmqClient
    from agents.coupon_worker import CouponWorker
    from agents.outbox_scanner import OutboxScanner

    _rmq_client = RmqClient()
    rmq_ok = await _rmq_client.connect()
    if rmq_ok and _coupon_manager is not None:
        # CouponWorker — 消费 DLQ 超时消息
        _coupon_worker = CouponWorker(
            coupon_manager=_coupon_manager,
            coupon_db=_coupon_db if db_ok else None,
            memory_manager=_memory,
        )
        _coupon_worker.start(_rmq_client)
        logger.info("CouponWorker 已启动（体验券超时检测）")

        # OutboxScanner — 扫描 coupon_outbox 发 RMQ
        if db_ok:
            _outbox_scanner = OutboxScanner(
                coupon_db=_coupon_db,
                rmq_client=_rmq_client,
            )
            _outbox_scanner.start()
            logger.info("OutboxScanner 已启动（体验券消息表扫描）")
        else:
            logger.warning("MySQL 不可用，OutboxScanner 已跳过")
    else:
        logger.warning(
            f"RabbitMQ 不可用（conn={rmq_ok}），体验券超时释放功能降级"
        )

    # ── 注入检测器（基于向量相似度的 Prompt Injection 检测） ──────────────
    from security.injection_detector import InjectionDetector
    _detector = InjectionDetector(threshold=0.85)

    # ── 限流组件（令牌桶 + 用户频控）───────────────────────────────────────
    from core.rate_limiter import TokenBucket, UserRateLimiter
    _redis_for_ratelimit = _redis_dedup  # 复用上传去重的 Redis 连接
    _rate_limiter = dict(
        token_bucket=TokenBucket(rate=20, capacity=10),
        user_limiter=UserRateLimiter(_redis_for_ratelimit, limit=5, window=30),
        # TODO: 去重逻辑待重新设计后启用
        # dedup=RequestDedup(_redis_for_ratelimit, ttl=15),
    )

    # ── 请求队列 + 工作协程 ──────────────────────────────────────────────
    from core.work_queue import LLMWorkQueue
    _work_queue = LLMWorkQueue(engine=_engine, num_workers=4, max_size=50)

    logger.info("EchoMind v2（新架构）已就绪")
    yield

    # ── 关闭后台组件 ──────────────────────────────────────────────
    if _outbox_scanner is not None:
        await _outbox_scanner.stop()
    if _coupon_worker is not None:
        await _coupon_worker.stop()
    if _rmq_client is not None:
        await _rmq_client.close()
    if _coupon_db is not None:
        await _coupon_db.close()
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


# ── 静态文件（前端 SPA）────────────────────────────────────────────────────────
_STATIC_DIR = pathlib.Path(__file__).parent.parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(str(_STATIC_DIR / "index.html"))


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:     str = Field(..., max_length=4000, description="用户消息，最长 4000 字符")
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
    show_coupon:     bool = False


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
      限流检查 → 注入检测 → 记忆读取 → AgentEngine（Planner → Orchestrator → Response Agent）→ 记忆写入

    限流保护：
      1. 全局令牌桶 —— 控制总入口 QPS
      2. 用户频控 —— 同一用户 30s 内最多 5 条
      3. 工作队列 —— 超过并发上限时返回 429
    """
    if _engine is None or _memory is None:
        raise HTTPException(503, "服务未就绪")

    rl = _rate_limiter

    # ── 0a. 全局令牌桶 ────────────────────────────────────
    if rl and not rl["token_bucket"].consume():
        return ChatResponse(
            conv_id=req.conv_id or str(uuid.uuid4()),
            response="系统繁忙，请稍后再试。",
            primary_intent="chitchat",
            sub_tasks=["GREETING"],
            emotion="neutral",
            skill_statuses=[],
            knowledge_used=False,
            latency_ms=0.0,
        )

    # ── 0b. 用户频控 ──────────────────────────────────────
    if rl and not rl["user_limiter"].is_allowed(req.user_id):
        return ChatResponse(
            conv_id=req.conv_id or str(uuid.uuid4()),
            response="消息发送太频繁，请稍后再试。",
            primary_intent="chitchat",
            sub_tasks=["GREETING"],
            emotion="neutral",
            skill_statuses=[],
            knowledge_used=False,
            latency_ms=0.0,
        )

    # ── 0d. 注入检测 ──────────────────────────────────
    if _detector and _detector.check(req.message):
        return ChatResponse(
            conv_id=req.conv_id or str(uuid.uuid4()),
            response="抱歉，您的输入包含异常内容，请重新描述您的问题。",
            primary_intent="chitchat",
            sub_tasks=["GREETING"],
            emotion="neutral",
            skill_statuses=[],
            knowledge_used=False,
            latency_ms=0.0,
        )

    from memory.conversation_memory import MsgRole

    conv_id = req.conv_id or str(uuid.uuid4())

    # 1. 读取记忆上下文
    mem_ctx = await _memory.get_context(req.user_id, conv_id, query=req.message)

    # 2. 构建对话历史
    history = [
        {"role": m.role.value, "content": m.content}
        for m in mem_ctx.recent_messages[-5:]
    ] if mem_ctx.recent_messages else None

    # 3. 通过工作队列提交（控制并发）
    if _work_queue:
        result = await _work_queue.submit(
            message=req.message.strip(),
            conv_id=conv_id,
            user_id=req.user_id,
            memory_context=mem_ctx.to_prompt_text(),
            history=history,
        )
    else:
        # 工作队列不可用时直接调用（极端降级）
        result = await _engine.run(
            message=req.message.strip(),
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
        show_coupon=result.show_coupon,
    )


def _stream_rejection(req: ChatRequest, message: str) -> StreamingResponse:
    """构造流式拒绝响应（SSE 格式）。

    用于限流、注入检测等前置拦截场景，保持与正常流式一致的接口格式。

    Args:
        req: 原始请求
        message: 拒绝原因文本

    Returns:
        SSE StreamingResponse
    """
    async def reject_gen():
        rejection = message
        yield json.dumps({
            "type": "meta",
            "primary_intent": "chitchat",
            "sub_tasks": ["GREETING"],
            "emotion": "neutral",
            "skill_statuses": [],
            "need_rag": False,
            "conv_id": req.conv_id or str(uuid.uuid4()),
        }) + "\n"
        yield json.dumps({
            "type": "done",
            "response": rejection,
            "primary_intent": "chitchat",
            "sub_tasks": ["GREETING"],
            "emotion": "neutral",
            "skill_statuses": [],
            "knowledge_used": False,
            "latency_ms": 0,
            "conv_id": req.conv_id or str(uuid.uuid4()),
        }) + "\n"
    return StreamingResponse(reject_gen(), media_type="text/event-stream")


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """
    流式对话接口（SSE）。

    流格式：
      data: {"type":"meta","primary_intent":"...","emotion":"...",...}
      data: {"type":"token","text":"逐 token 内容"}
      data: {"type":"done","response":"完整回复",...}
    """
    if _engine is None or _memory is None:
        raise HTTPException(503, "服务未就绪")

    rl = _rate_limiter

    # ── 0a. 全局令牌桶 ────────────────────────────────────
    if rl and not rl["token_bucket"].consume():
        rejection = "系统繁忙，请稍后再试。"
        return _stream_rejection(req, rejection)

    # ── 0b. 用户频控 ──────────────────────────────────────
    if rl and not rl["user_limiter"].is_allowed(req.user_id):
        rejection = "消息发送太频繁，请稍后再试。"
        return _stream_rejection(req, rejection)

    # ── 0d. 注入检测 ──────────────────────────────────────
    if _detector and _detector.check(req.message):
        return _stream_rejection(req, "抱歉，您的输入包含异常内容，请重新描述您的问题。")

    from memory.conversation_memory import MsgRole

    conv_id = req.conv_id or str(uuid.uuid4())
    user_id = req.user_id or "anonymous"

    # 1. 读取记忆上下文
    mem_ctx = await _memory.get_context(user_id, conv_id, query=req.message)

    # 2. 构建对话历史
    history = [
        {"role": m.role.value, "content": m.content}
        for m in mem_ctx.recent_messages[-5:]
    ] if mem_ctx.recent_messages else None

    async def event_generator():
        """SSE 事件流。"""
        full_response = ""
        kwargs = dict(
            message=req.message.strip(),
            conv_id=conv_id,
            user_id=user_id,
            memory_context=mem_ctx.to_prompt_text(),
            history=history,
        )

        try:
            if _work_queue:
                stream = _work_queue.submit_stream(**kwargs)
            else:
                stream = _engine.run_stream(**kwargs)

            async for event in stream:
                if event["type"] == "meta":
                    _append_intent_log(
                        message=req.message,
                        primary_intent=event.get("primary_intent", ""),
                        sub_tasks=event.get("sub_tasks", []),
                        emotion=event.get("emotion", ""),
                        user_id=user_id,
                        history=history,
                    )
                    yield f"data: {json.dumps(event)}\n\n"

                elif event["type"] == "token":
                    full_response += event["text"]
                    yield f"data: {json.dumps(event)}\n\n"

                elif event["type"] == "done":
                    await _memory.add_message(user_id, conv_id, MsgRole.USER, req.message)
                    await _memory.add_message(user_id, conv_id, MsgRole.ASSISTANT, full_response)
                    asyncio.create_task(_memory.update_profile(user_id, conv_id))
                    yield f"data: {json.dumps(event)}\n\n"

        except Exception as ex:
            logger.error(f"流式处理异常: {ex}")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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


# ── 体验券路由 ──────────────────────────────────────────────────────────────────

class CouponClaimInput(BaseModel):
    user_id: str = Field(..., min_length=1, description="用户 ID")
    conv_id: str = Field(..., min_length=1, description="会话 ID")


class CouponLeadInput(BaseModel):
    user_id: str = Field(..., min_length=1, description="用户 ID")
    name:    str = Field(..., min_length=1, max_length=50, description="姓名")
    phone:   str = Field(..., min_length=7, max_length=20, description="手机号")
    conv_id: str = Field(..., min_length=1, description="会话 ID")


@app.post("/coupon/claim", tags=["体验券"])
async def coupon_claim(body: CouponClaimInput):
    """
    用户确认领取体验券。

    原子操作：检查库存 → 扣减 → 写 pending。
    支持幂等：同一用户重复调用不重复扣减。
    """
    if _coupon_manager is None:
        raise HTTPException(503, "体验券系统未初始化")

    # # 检查冷却
    # if _coupon_manager.check_cooldown(body.user_id):
    #     return {"status": "cooldown", "message": "24h 冷却中", "stock": _coupon_manager.get_stock()}

    result = await _coupon_manager.claim(body.user_id, body.conv_id)

    if result.get("status") == "error":
        raise HTTPException(500, result.get("message", "领取失败"))

    return result


@app.post("/coupon/lead", tags=["体验券"])
async def coupon_lead(body: CouponLeadInput):
    """
    用户提交留资表单。

    锁定体验券（释放时不再归还库存），记录留资信息。
    同时写入 MySQL（持久化）和 Redis（缓存）。
    """
    if _coupon_manager is None:
        raise HTTPException(503, "体验券系统未初始化")

    # 检查是否有 claimed 记录
    if not _coupon_manager.check_claimed(body.user_id):
        raise HTTPException(400, "无待处理的体验券，请先领取")

    user_id = body.user_id
    name = body.name
    phone = body.phone
    conv_id = body.conv_id

    # ── 1. 写入 MySQL（持久化） ──────────────────────────
    order_id = 0
    if _coupon_db and _coupon_db.connected:
        try:
            # 查找用户的订单
            order = await _coupon_db.find_order_by_user_id(user_id)
            if order:
                order_id = order["id"]
                # 插入 lead 记录
                await _coupon_db.insert_lead(
                    order_id=order_id,
                    user_id=user_id,
                    name=name,
                    phone=phone,
                    conv_id=conv_id,
                )
                # 更新订单状态
                await _coupon_db.update_order_status(order_id, "lead_submitted")
                logger.info(
                    f"MySQL 留资已保存: order_id={order_id} user={user_id}"
                )
        except Exception as ex:
            logger.error(f"MySQL 留资写入失败: {ex}")
            # 不阻断流程，Redis 缓存仍可工作

    # ── 2. 写入 Redis（缓存） ────────────────────────────
    lead_data = body.model_dump_json()
    ok = _coupon_manager.set_lead_submitted(user_id, lead_data)
    if not ok:
        raise HTTPException(500, "留资保存失败")

    logger.info(
        f"留资提交成功: user={user_id} name={name} "
        f"phone={phone[-4:]} order_id={order_id}"
    )

    return {
        "status": "ok",
        "message": "试驾体验券已锁定，感谢您的参与！",
        "stock": _coupon_manager.get_stock(),
    }


@app.get("/coupon/stats", tags=["体验券"])
async def coupon_stats():
    """体验券系统统计（库存/领取/留资数）。"""
    if _coupon_manager is None:
        raise HTTPException(503, "体验券系统未初始化")
    return _coupon_manager.stats()


@app.get("/coupon/check", tags=["体验券"])
async def coupon_check(user_id: str = "", conv_id: str = ""):
    """
    检查用户是否可以领取体验券（供前端预检）。

    返回冷却状态、pending 状态、库存余量。
    """
    if _coupon_manager is None:
        raise HTTPException(503, "体验券系统未初始化")

    return {
        "claimed": _coupon_manager.check_claimed(user_id) if user_id else False,
        "pending": _coupon_manager.check_pending(user_id) if user_id else False,
        "cooldown": _coupon_manager.check_cooldown(user_id) if user_id else False,
        "lead_submitted": _coupon_manager.check_lead_submitted(user_id) if user_id else False,
        "stock": _coupon_manager.get_stock(),
    }


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

    # ── Redis 快速去重：基于原始文件内容，跳过解析 ───────────────────────
    raw_doc_id = hashlib.md5(f"{filename}|{len(content)}|{content[:500]}".encode()).hexdigest()
    if _redis_dedup and _redis_dedup.sismember(_UPLOAD_DEDUP_KEY, raw_doc_id):
        return {
            "message": f"文件 {filename} 已存在，跳过导入",
            "added_chunks": 0, "skipped": 1, "doc_ids": [], "total_chunks": _kb.doc_count, "source_docs": 0, "dedup": "redis",
        }

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
    added = result.get("added_chunks", 0)
    skipped = result.get("skipped", 0)

    # 导入成功 → 记录到 Redis Set
    if added > 0 and _redis_dedup:
        _redis_dedup.sadd(_UPLOAD_DEDUP_KEY, raw_doc_id)

    message = f"文件 {filename}"
    if added > 0:
        message += f" 导入成功，新增 {added} 个片段"
    if skipped > 0:
        message += f"（跳过 {skipped} 篇重复）"
    if added == 0 and skipped == 0:
        message += " 无可导入内容"

    return {
        "message": message,
        "added_chunks": added,
        "skipped": skipped,
        "doc_ids": result.get("doc_ids", []),
        "total_chunks": _kb.doc_count,
        "source_docs": len(docs),
        "dedup": False,
    }


def _parse_pdf(raw: bytes, title: str) -> List[Dict[str, str]]:
    """
    解析 PDF，所有页合并为一篇文档（单个 doc_id）。

    每页的文本和表格按页面顺序拼接，保持可读性。
    _chunk() 会自动在 500 字符处切割，所以合并不影响后续处理。
    """
    import io
    import pdfplumber

    all_parts: List[str] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            page_parts: List[str] = []
            text = page.extract_text()
            if text and text.strip():
                page_parts.append(text.strip())
            tables = page.extract_tables()
            if tables:
                for t in tables:
                    md = _table_to_markdown(t)
                    if md:
                        page_parts.append(f"[表格开始]\n{md}\n[表格结束]")
            if page_parts:
                page_header = f"--- 第 {i} 页 ---"
                all_parts.append(page_header + "\n" + "\n\n".join(page_parts))

    if not all_parts:
        raise HTTPException(400, "PDF 文件中未提取到文本内容")

    # 整份 PDF 就是一篇文档 → 一个 doc_id
    return [{"title": title, "content": "\n\n".join(all_parts)}]


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
                parts.append(f"[表格开始]\n{md}\n[表格结束]")
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


@app.get("/knowledge/documents", tags=["知识库"])
async def knowledge_documents():
    """按文档（doc_id）分组返回，每条含所有 chunk 预览。"""
    if _kb is None:
        raise HTTPException(503, "知识库未初始化")
    docs = _kb.list_documents()
    return {"total": len(docs), "documents": docs}


@app.delete("/knowledge/{doc_id}", tags=["知识库"])
async def knowledge_delete(doc_id: str):
    if _kb is None:
        raise HTTPException(503, "知识库未初始化")
    deleted = _kb.delete_by_doc_id(doc_id)
    if deleted == 0:
        raise HTTPException(404, f"文档 {doc_id} 不存在或已被删除")
    # 清除去重缓存（允许重新上传同一文件）
    if _redis_dedup:
        _redis_dedup.delete(_UPLOAD_DEDUP_KEY)
    return {"message": f"文档 {doc_id} 已删除", "deleted_chunks": deleted, "total_chunks": _kb.doc_count}


@app.delete("/knowledge", tags=["知识库"])
async def knowledge_clear():
    if _kb is None:
        raise HTTPException(503, "知识库未初始化")
    deleted = _kb.clear()
    if _redis_dedup:
        _redis_dedup.delete(_UPLOAD_DEDUP_KEY)
    return {"message": "知识库已清空", "deleted_chunks": deleted, "total_chunks": _kb.doc_count}


@app.delete("/memory/profile", tags=["记忆"])
async def memory_clear_profile(user_id: Optional[str] = None):
    if _memory is None:
        raise HTTPException(503, "记忆系统未初始化")
    deleted = _memory.clear_profile(user_id)
    tag = f"user={user_id}" if user_id else "全部"
    return {"message": f"用户画像已清除 ({tag}) {deleted} 条", "deleted": deleted}


@app.delete("/memory/episodic", tags=["记忆"])
async def memory_clear_episodic(user_id: Optional[str] = None):
    if _memory is None:
        raise HTTPException(503, "记忆系统未初始化")
    deleted = _memory.clear_episodic(user_id)
    tag = f"user={user_id}" if user_id else "全部"
    return {"message": f"情景记忆已清除 ({tag}) {deleted} 条", "deleted": deleted}


@app.delete("/memory/working", tags=["记忆"])
async def memory_clear_working(user_id: str, conv_id: str):
    if _memory is None:
        raise HTTPException(503, "记忆系统未初始化")
    ok = _memory.clear_working_memory(user_id, conv_id)
    return {"message": "工作记忆已清除" if ok else "清除失败", "success": ok}


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
    Multi-Intent 评估（简化版）。

    评估维度：
      - 每类意图独立 Precision / Recall / F1 → Macro F1
      - 多意图集合 Exact Match Rate（子任务集合完全一致的比例）

    注意：
      多意图本质上就是多个单意图的组合，槽位评估意义不大，已移除。
      测试用例中的 "slots" 字段会被忽略。

    用例格式（旧版 slots 字段可选，会被忽略）：
      {"query": "M8多少钱", "sub_tasks": ["PRICE"]}
      {"query": "你好，M9价格多少", "sub_tasks": ["GREETING", "PRICE"]}

    不传 cases 时默认加载 tests/test.jsonl。
    """
    if _engine is None:
        raise HTTPException(503, "服务未就绪")

    from evaluation.multi_intent_evaluator import MultiIntentEvaluator

    cases = body.cases if body.cases is not None else _load_default_test_cases()
    evaluator = MultiIntentEvaluator(_engine.planner)
    report, _ = await evaluator.eval(cases, detail=True)
    return evaluator.report_to_dict(report, include_details=True)


# ── Bad Case 反馈 ──────────────────────────────────────────────────────────────

class BadCaseReport(BaseModel):
    """用户反馈的 bad case。"""
    query: str
    response: str = ""
    predicted_sub_tasks: List[str] = []
    conv_id: str = ""
    user_id: str = ""
    note: str = ""  # 用户可选的补充说明


_BADCASE_PATH = pathlib.Path(__file__).parent.parent / "data" / "badcase.jsonl"


@app.post("/feedback/badcase")
async def report_badcase(body: BadCaseReport):
    """
    用户端 bad case 上报。

    前端用户点"👎"时自动带上 query + 识别结果，追加到 data/badcase.jsonl。
    定期人工审核后 merge 进 tests/test.jsonl，持续补齐离线评测盲区。
    """
    import json, time
    record = body.model_dump()
    record["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    record["_source"] = "web_feedback"

    _BADCASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_BADCASE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info(f"badcase recorded: query={body.query[:40]!r}")
    return {"status": "ok"}


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
