"""
RabbitMQ 连接工具 — 体验券超时检测的消息队列基础设施

职责：
  - 建立 RabbitMQ 连接
  - 声明 DLQ（Dead Letter Queue）拓扑：
    coupon.delay  → (TTL 到期) → coupon.dlx  → coupon.timeout → CouponWorker

队列拓扑：
  coupon.delay:    主队列，消息 TTL=60s，死信转发到 coupon.dlx
  coupon.dlx:      死信交换机（direct），绑定到 coupon.timeout
  coupon.timeout:  死信队列，CouponWorker 消费此队列，执行 Lua 释放
"""
import json
import logging
import os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# 队列/交换机名称
EXCHANGE_DLX    = "coupon.dlx"       # 死信交换机
QUEUE_DELAY     = "coupon.delay"     # 延迟队列（TTL 到期后转入 DLQ）
QUEUE_TIMEOUT   = "coupon.timeout"   # 死信队列（消费者监听）
ROUTING_TIMEOUT = "timeout"          # 死信路由键

# TTL 配置（毫秒）
QUEUE_TTL_MS = 60000    # coupon.delay 队列统一 x-message-ttl: 60s
MAX_RETRIES  = 3        # 最大重试次数


class RmqClient:
    """RabbitMQ 连接与拓扑管理。"""

    def __init__(self, url: Optional[str] = None):
        """初始化 RMQ 客户端。

        Args:
            url: AMQP URL，默认从环境变量 RABBITMQ_URL 读取
        """
        self._url = url or os.getenv(
            "RABBITMQ_URL",
            "amqp://echomind:echomind123@localhost:5672/",
        )
        self._connection = None
        self._channel = None

    # ── 连接管理 ──────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """建立异步连接 + 声明队列拓扑。

        Returns:
            是否连接成功
        """
        try:
            import aio_pika

            self._connection = await aio_pika.connect_robust(self._url)
            self._channel = await self._connection.channel()
            await self._declare_topology()
            logger.info("RabbitMQ 已连接，队列拓扑已声明")
            return True
        except ImportError:
            logger.error("aio_pika 未安装，请执行: pip install aio-pika")
            return False
        except Exception as ex:
            logger.warning(f"RabbitMQ 连接失败: {ex}")
            return False

    async def close(self) -> None:
        """关闭连接。"""
        if self._connection:
            await self._connection.close()
            logger.info("RabbitMQ 连接已关闭")

    @property
    def connected(self) -> bool:
        return self._connection is not None and not self._connection.is_closed

    # ── 拓扑声明 ──────────────────────────────────────────────────────────────

    async def _declare_topology(self) -> None:
        """声明 DLQ 队列拓扑。

        流程:
          1. 声明死信交换机 coupon.dlx（direct）
          2. 声明延迟队列 coupon.delay（绑定 x-dead-letter-exchange=dlx）
          3. 声明死信队列 coupon.timeout（绑定 dlx）
          4. 绑定路由
        """
        import aio_pika

        channel = self._channel

        # 1. 死信交换机
        dlx = await channel.declare_exchange(
            EXCHANGE_DLX,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )

        # 2. 延迟队列 — 消息在此停留 TTL 后由 RabbitMQ 自动死信转发
        delay_queue = await channel.declare_queue(
            QUEUE_DELAY,
            durable=True,
            arguments={
                "x-dead-letter-exchange":    EXCHANGE_DLX,
                "x-dead-letter-routing-key": ROUTING_TIMEOUT,
                "x-message-ttl":             QUEUE_TTL_MS,
            },
        )
        # 延迟队列不需要绑定到 DLX，消息通过 default_exchange 按队列名投递
        # 超时后由 RabbitMQ 自动死信转发到 coupon.dlx，路由键为 ROUTING_TIMEOUT

        # 3. 死信队列
        timeout_queue = await channel.declare_queue(
            QUEUE_TIMEOUT,
            durable=True,
        )
        await timeout_queue.bind(dlx, routing_key=ROUTING_TIMEOUT)

        logger.info(
            f"队列拓扑已声明: {QUEUE_DELAY} → DLX → {QUEUE_TIMEOUT}"
        )

    # ── 发消息 ────────────────────────────────────────────────────────────────

    async def publish_delay(
        self,
        body: Dict[str, Any],
        headers: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """发送体验券超时检测消息到延迟队列。

        TTL 由队列级 x-message-ttl 控制，不再需要逐消息设置。

        Args:
            body: 消息体（含 user_id, conv_id 等）
            headers: 消息头（用于传递重试次数等元数据）

        Returns:
            是否发送成功
        """
        if not self.connected:
            logger.warning("RabbitMQ 未连接，无法发送延迟消息")
            return False

        try:
            import aio_pika

            message = aio_pika.Message(
                body=json.dumps(body, ensure_ascii=False).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers=headers or {},
            )
            await self._channel.default_exchange.publish(
                message,
                routing_key=QUEUE_DELAY,
            )
            logger.debug(f"延迟消息已发送: user={body.get('user_id')}")
            return True
        except Exception as ex:
            logger.error(f"发送延迟消息失败: {ex}")
            return False

    # ── 消费 ──────────────────────────────────────────────────────────────────

    async def consume_timeout(self, callback: Callable) -> None:
        """消费 coupon.timeout 死信队列，失败时自动重试。

        消费失败后将消息重新投递到 coupon.delay，走 TTL→DLX→coupon.timeout
        链路实现延迟重试，超过 MAX_RETRIES 次后丢弃并记录告警。

        Args:
            callback: 异步回调函数，接收 Dict 消息体
        """
        if not self.connected:
            logger.error("RabbitMQ 未连接，无法启动消费者")
            return

        import aio_pika

        async def on_message(message: aio_pika.IncomingMessage) -> None:
            async with message.process(requeue=False):
                try:
                    body = json.loads(message.body.decode())
                    logger.debug(f"死信消息到达: {body}")
                    await callback(body)
                except Exception as ex:
                    retry_count = (message.headers or {}).get("x-retry-count", 0)
                    if retry_count < MAX_RETRIES:
                        logger.warning(
                            f"处理失败，第 {retry_count + 1}/{MAX_RETRIES} 次重试: {ex}"
                        )
                        await self.publish_delay(
                            body,
                            headers={"x-retry-count": retry_count + 1},
                        )
                    else:
                        logger.error(
                            f"已达最大重试次数 {MAX_RETRIES}，丢弃消息: {body}"
                        )

        queue = await self._channel.declare_queue(QUEUE_TIMEOUT, durable=True)
        await queue.consume(on_message)
        logger.info(f"已开始消费 {QUEUE_TIMEOUT}")
