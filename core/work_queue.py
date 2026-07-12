"""
请求队列 + 工作协程。

用于削峰填谷，控制同时处理的 LLM 请求数量。

核心机制：
  - 进程内 asyncio.Queue，HTTP 连接不中断
  - 队列满了快速失败，不堆积请求
  - 流式和非流式共用同一组 worker

关键设计决策：
  - 为什么不用 MQ？主链路需要保持 HTTP 长连接（SSE 流式返回），
    MQ（RabbitMQ / Redis List）断了连接无法流式返回。
    所以主链路用进程内 Queue，MQ 只用于非主链路（画像更新、日志、埋点）。
"""
import asyncio
import logging
from typing import Any, AsyncGenerator, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# 流式结束标记（哨兵对象）
_END = object()


class QueueFullError(Exception):
    """队列已满，请求被拒绝。"""
    pass


class LLMWorkQueue:
    """请求队列 + 固定数量工作协程。

    支持两种模式：
      - submit():      非流式，返回完整结果
      - submit_stream(): 流式，返回 async generator（逐 token）

    用法：
        queue = LLMWorkQueue(engine=agent_engine, num_workers=4, max_size=50)

        # 非流式
        result = await queue.submit(message="你好")

        # 流式
        async for token in queue.submit_stream(message="你好"):
            yield token
    """

    def __init__(
        self,
        engine,
        num_workers: int = 4,
        max_size: int = 50,
    ):
        """初始化工作队列。

        Args:
            engine: AgentEngine 实例（需有 run() 和 run_stream() 方法）
            num_workers: 同时处理请求的工作协程数
            max_size: 队列最大长度，超限时快速失败
        """
        self._engine = engine
        self._max_size = max_size
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)

        self._workers = [
            asyncio.create_task(self._run_worker(i))
            for i in range(num_workers)
        ]
        logger.info(
            f"工作队列已启动: {num_workers} 个 worker, "
            f"队列上限 {max_size}"
        )

    @property
    def stats(self) -> Dict[str, Any]:
        """获取队列统计信息。"""
        return {
            "queue_size": self._queue.qsize(),
            "max_size": self._max_size,
            "num_workers": len(self._workers),
        }

    # ── 非流式 ─────────────────────────────────────────────────────────────

    async def submit(self, **kwargs) -> Any:
        """提交非流式请求，等待完整处理结果。

        HTTP 连接在 await 期间一直保持，不会断。

        Args:
            **kwargs: 传给 engine.run() 的参数

        Returns:
            OrchestratorResult

        Raises:
            QueueFullError: 队列满了，快速失败
        """
        if self._queue.full():
            raise QueueFullError("当前排队人数较多，请稍后再试")

        future = asyncio.get_event_loop().create_future()
        await self._queue.put(("chat", kwargs, future))
        return await future  # ← HTTP 连接在这里等，worker 处理完会 set_result

    # ── 流式 ───────────────────────────────────────────────────────────────

    async def submit_stream(self, **kwargs) -> AsyncGenerator[Any, None]:
        """提交流式请求，返回 async generator 逐 token 产出。

        HTTP 连接在迭代期间一直保持。

        Args:
            **kwargs: 传给 engine.run_stream() 的参数

        Yields:
            逐 token 的 event 字典

        Raises:
            QueueFullError: 队列满了，快速失败
        """
        if self._queue.full():
            raise QueueFullError("当前排队人数较多，请稍后再试")

        token_queue: asyncio.Queue = asyncio.Queue()
        await self._queue.put(("stream", kwargs, token_queue))

        while True:
            token = await token_queue.get()
            if token is _END:
                break
            yield token

    # ── 工作协程 ───────────────────────────────────────────────────────────

    async def _run_worker(self, idx: int) -> None:
        """工作协程主循环：从队列取任务，处理，回填结果。

        Args:
            idx: worker 编号（仅用于日志）
        """
        while True:
            mode, params, channel = await self._queue.get()
            try:
                if mode == "stream":
                    await self._handle_stream(params, channel)
                else:
                    await self._handle_chat(params, channel)
            except Exception as ex:
                logger.error(f"Worker {idx} 处理失败: {ex}", exc_info=True)
            finally:
                self._queue.task_done()

    async def _handle_chat(self, params: dict, future) -> None:
        """非流式处理。

        Args:
            params: 传给 engine.run() 的参数
            future: 用于回填结果的 Future
        """
        try:
            result = await self._engine.run(**params)
            future.set_result(result)
        except Exception as ex:
            future.set_exception(ex)

    async def _handle_stream(self, params: dict, token_queue: asyncio.Queue) -> None:
        """流式处理。

        Args:
            params: 传给 engine.run_stream() 的参数
            token_queue: 用于逐 token 回传的队列
        """
        try:
            async for event in self._engine.run_stream(**params):
                await token_queue.put(event)
        except Exception as ex:
            logger.error(f"流式处理失败: {ex}")
        finally:
            await token_queue.put(_END)
