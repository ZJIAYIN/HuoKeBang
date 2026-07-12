# EchoMind 限流体系设计方案

> 本文档记录 EchoMind 在应对高并发场景下的限流、降级与可靠性设计方案。
> 核心思路：**入口限流 защищаем систему → 缓存 снижаем затраты → 异步 не блокируем**

---

## 一、总体架构：四层递进

```
请求进来
   │
   ├─ ① 全局令牌桶（全局限流）
   │      控制系统总入口 QPS，防止突发流量打挂后端
   │      失败 → 429 "系统繁忙，请稍后再试"
   │
   ├─ ② 用户维度频控（单用户限流）
   │      同一用户 30s 内最多 N 条消息
   │      失败 → 429 "发送太频繁，请稍后再试"
   │
   ├─ ③ 同请求去重（重复拦截）
   │      同一用户发完全相同的内容且正在处理中，不重复调 LLM
   │      命中 → "上一条消息正在处理中..."
   │
   └─ ④ 请求队列 + 工作协程（削峰填谷）
          队列满了 → 快速失败 "当前排队人数较多"
          有空位 → 工作协程异步处理
          流式请求走旁路（不做严格排队）
```

---

## 二、各层详细设计

### 2.1 全局令牌桶（`core/rate_limiter.py`）

**职责：** 控制系统全局入口流量，防止瞬时流量打挂 LLM API 或数据库。

**实现方式：** 内存 TokenBucket，零外部依赖。

```python
class TokenBucket:
    """令牌桶全局限流器。

    内存实现，不依赖 Redis，毫秒级判断。
    支持突发（burst）和稳定速率（rate）两维控制。

    用法：
        bucket = TokenBucket(rate=20, capacity=10)
        if bucket.consume():
            # 放行
        else:
            # 429
    """

    def __init__(self, rate: float, capacity: int):
        """初始化令牌桶。

        Args:
            rate: 每秒补充的令牌数（稳定速率）
            capacity: 桶容量（最大突发量）
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)      # 初始满桶
        self._last = time.monotonic()

    def consume(self, tokens: int = 1) -> bool:
        """消耗 tokens 个令牌。

        Args:
            tokens: 本次消耗的令牌数，默认 1

        Returns:
            True=允许通过，False=限流
        """
        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last = now

        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False
```

**参数配置建议：**

| 环境 | rate | capacity | 含义 |
|------|------|----------|------|
| 开发 | 100 | 50 | 基本不限制 |
| 生产 | 20 | 10 | 稳定 20 QPS，突发 10 个 |
| 高压 | 10 | 5 | 保守模式 |

---

### 2.2 用户维度频控（`core/rate_limiter.py`）

**职责：** 防止单个用户刷爆 LLM 额度或资源。

**实现方式：** Redis INCR + TTL，跨会话共享。

```python
class UserRateLimiter:
    """单用户频控。

    基于 Redis 计数器，跨会话共享。
    同用户 30 秒内最多 5 条消息。

    用法：
        limiter = UserRateLimiter(redis_client, limit=5, window=30)
        if limiter.is_allowed(user_id):
            # 放行
        else:
            # 429 "发送太频繁"
    """

    def __init__(self, redis_client, limit: int = 5, window: int = 30):
        """初始化用户频控。

        Args:
            redis_client: Redis 连接（None 时放行所有）
            limit: 窗口内允许的最大请求数
            window: 时间窗口（秒）
        """
        self._redis = redis_client
        self._limit = limit
        self._window = window

    def is_allowed(self, user_id: str) -> bool:
        """检查用户是否被限流。

        Args:
            user_id: 用户 ID

        Returns:
            True=允许通过
        """
        if not self._redis:
            return True

        key = f"ratelimit:user:{user_id}"
        try:
            current = self._redis.incr(key)
            if current == 1:
                self._redis.expire(key, self._window)
            return current <= self._limit
        except Exception as ex:
            logger.warning(f"用户频控查询失败，放行: {ex}")
            return True   # Redis 不可用时降级放行
```

---

### 2.3 同请求去重（`core/rate_limiter.py`）

**职责：** 同一个用户发完全相同的内容且正在处理中，不重复打 LLM。

**场景：** 用户点击了两次发送按钮 / 网络重试导致重复请求。

```python
class RequestDedup:
    """请求去重器。

    基于 Redis SETNX，当相同 user_id + message_hash 正在处理时，
    后续请求直接拒绝。

    TTL 自动过期，不会死锁。
    """

    def __init__(self, redis_client, ttl: int = 15):
        """初始化请求去重。

        Args:
            redis_client: Redis 连接（None 时放行所有）
            ttl: 去重锁超时秒数，应大于单次 LLM 调用最大耗时
        """
        self._redis = redis_client
        self._ttl = ttl

    def try_acquire(self, user_id: str, msg_hash: str) -> bool:
        """尝试获取请求锁。

        Args:
            user_id: 用户 ID
            msg_hash: 消息的 MD5 哈希

        Returns:
            True=获得锁（继续处理），False=重复请求（拒绝）
        """
        if not self._redis:
            return True
        key = f"dedup:{user_id}:{msg_hash}"
        try:
            ok = self._redis.setnx(key, "1")
            if ok:
                self._redis.expire(key, self._ttl)
            return bool(ok)
        except Exception as ex:
            logger.warning(f"去重检查失败，放行: {ex}")
            return True

    def release(self, user_id: str, msg_hash: str) -> None:
        """释放请求锁（请求完成时调用）。

        Args:
            user_id: 用户 ID
            msg_hash: 消息的 MD5 哈希
        """
        if not self._redis:
            return
        try:
            self._redis.delete(f"dedup:{user_id}:{msg_hash}")
        except Exception as ex:
            logger.warning(f"去重锁释放失败: {ex}")
```

---

### 2.4 请求队列 + 工作协程（`core/work_queue.py`）

**职责：** 限定同时处理 LLM 请求的协程数量，队列满了快速失败。

---

#### 关键问题：队列和 HTTP 连接的关系

**请求放进队列时，HTTP 连接会不会断？**

不会。用户担心的"放了队列连接就断了"是针对**跨进程 MQ**（RabbitMQ / Kafka / Redis List）的。但 `asyncio.Queue` 是**进程内队列**，机制完全不一样：

```
用户 → FastAPI Handler
       │
       ├─ submit(request) → 把 (request, Future) 放入 asyncio.Queue
       │
       ├─ await future     ← HTTP 连接一直保持在这里等
       │                     │
       ▼                     │  Worker 从队列取出请求
                             │  Worker 处理（调 LLM、RAG 等）
                             │  Worker 把结果设置到 Future
                             │
       ◄─ future 拿到结果 ───┘
       │
       └─ return response    ← HTTP 连接正常返回，全程未断
```

**对于 SSE 流式场景呢？**

流式也能走队列，只是从"一次性 Future"变成"逐 token 通道"：

```
用户 → FastAPI SSE Handler
       │
       ├─ submit_stream(request) → 把 (request, token_queue) 放入 asyncio.Queue
       │
       ├─ while True:             ← HTTP 连接保持
       │     token = await token_queue.get()
       │     if token is END: break
       │     yield f"data: {token}\n\n"
       │
       ▼                          Worker 取出请求
                                  Worker 调 LLM 流式接口
                                  Worker 逐 token 放入 token_queue
                                  Worker 最后放入 END 标记
```

所以**同一套队列机制可以同时支持非流式和流式**——只是回写结果的通道不同：

| 模式 | 回写通道 | Worker 写入 | Handler 读取 |
|------|---------|------------|-------------|
| 非流式 `/chat` | `Future` | `future.set_result()` | `await future` |
| 流式 `/chat/stream` | `asyncio.Queue` | `await queue.put(token)` | `async for token in queue` |

---

#### 实现代码

```python
import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_END = object()  # 流式结束标记


class LLMWorkQueue:
    """请求队列 + 固定数量工作协程。

    用于削峰填谷，控制同时处理的 LLM 请求数量。

    支持两种模式：
      - submit():      非流式，返回完整结果（OrchestratorResult）
      - submit_stream(): 流式，返回 async generator（逐 token）

    关键设计：
      - 进程内队列，HTTP 连接不中断
      - 队列满了快速失败，不堆积
      - 流式和非流式共用同一组 worker
    """

    def __init__(self, engine, num_workers: int = 4, max_size: int = 50):
        """初始化工作队列。

        Args:
            engine: AgentEngine 实例
            num_workers: 同时处理请求的工作协程数
            max_size: 队列最大长度，超限直接拒绝
        """
        self._engine = engine
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._workers = [
            asyncio.create_task(self._worker(i))
            for i in range(num_workers)
        ]

    async def submit(self, message: str, user_id: str = "",
                     conv_id: str = "", **kwargs) -> Any:
        """提交非流式请求，等待完整结果。

        HTTP 连接在 await 期间一直保持，不会断。

        Args:
            message: 用户消息
            user_id: 用户 ID
            conv_id: 会话 ID

        Returns:
            OrchestratorResult

        Raises:
            asyncio.QueueFull: 队列满了，快速失败
        """
        future = asyncio.get_event_loop().create_future()
        await self._queue.put(("chat", dict(message=message, user_id=user_id,
                                            conv_id=conv_id, **kwargs), future))
        return await future  # ← HTTP 连接在这里等，不会断

    async def submit_stream(self, message: str, user_id: str = "",
                            conv_id: str = "", **kwargs):
        """提交流式请求，返回 async generator。

        每调用一次 send() 就往 SSE 写一个 token。
        HTTP 连接在迭代期间一直保持。

        Args:
            message: 用户消息
            user_id: 用户 ID
            conv_id: 会话 ID

        Yields:
            {"type": "token", "text": "..."}
            {"type": "done", "response": "...", ...}

        Raises:
            asyncio.QueueFull: 队列满了，快速失败
        """
        token_queue: asyncio.Queue = asyncio.Queue()
        await self._queue.put(("stream", dict(message=message, user_id=user_id,
                                              conv_id=conv_id, token_queue=token_queue, **kwargs), None))

        while True:
            token = await token_queue.get()
            if token is _END:
                break
            yield token

    async def _worker(self, idx: int):
        """工作协程：从队列取任务，处理，回填结果。"""
        while True:
            mode, params, future = await self._queue.get()
            try:
                if mode == "stream":
                    token_queue = params.pop("token_queue")
                    async for event in self._engine.run_stream(**params):
                        await token_queue.put(event)
                    await token_queue.put(_END)
                else:
                    result = await self._engine.run(**params)
                    future.set_result(result)
            except Exception as e:
                if future is not None:
                    future.set_exception(e)
                logger.error(f"Worker {idx} 处理失败: {e}")
            finally:
                self._queue.task_done()
```

---

#### 参数配置建议

| 环境 | num_workers | max_size | 含义 |
|------|-------------|----------|------|
| 开发 | 2 | 20 | 够用就行 |
| 生产 | 4~8 | 50~100 | 看 LLM API 并发上限 |
| 高压 | 8~16 | 30 | 更多 worker 但队列短点，快速失败 |

---

## 三、请求完整流程序列

```
请求进来
   │
   ├─ [1] TokenBucket.consume()
   │      ├─ True → 继续
   │      └─ False → return 429 "系统繁忙"
   │
   ├─ [2] UserRateLimiter.is_allowed(user_id)
   │      ├─ True → 继续
   │      └─ False → return 429 "发送太频繁"
   │
   ├─ [3] RequestDedup.try_acquire(user_id, msg_hash)
   │      ├─ True → 继续
   │      └─ False → return "上一条消息正在处理中..."
   │
   ├─ [4] LLMWorkQueue.submit(message)
   │      ├─ 队列未满 → 工作协程处理
   │      │       ├─ Planner 缓存命中？→ 直接返回 PlannerOutput
   │      │       ├─ Planner LLM 调用（有超时 + 重试 + Fallback）
   │      │       ├─ RAG 检索（MCPToolManager 有缓存 + 熔断 + 降级）
   │      │       ├─ ResponseAgent 生成（有超时 + 重试）
   │      │       └─ 异步：画像更新 / 日志写盘（不阻塞返回）
   │      │
   │      └─ 队列满了 → return "当前排队人数较多"
   │
   └─ [5] RequestDedup.release(user_id, msg_hash) — 不管成功失败都要释放
```

---

## 四、缓存体系（降低单次请求成本）

限流是"不让系统挂"，缓存是"让请求更快"。

| 缓存层级 | 存储 | TTL | 命中率 |
|---------|------|-----|--------|
| **Planner 缓存**（message → PlannerOutput） | 内存 dict | 60s | 高（短时重复问） |
| **RAG 缓存**（query → chunks） | MCPToolManager 内存 | 300s | 高（高频 FAQ） |
| **ResponseAgent 缓存**（意图+槽位 → 回复） | 内存 dict | 300s | 中（同一问题多次问） |

### 4.1 Planner 缓存

```python
async def plan(self, message, ...):
    cache_key = hashlib.md5(message.encode()).hexdigest()
    if cache_key in self._cache:
        logger.debug(f"Planner 缓存命中: {message[:40]}")
        return self._cache[cache_key]

    result = await self._llm_plan(message, ...)
    self._cache[cache_key] = result
    return result
```

### 4.2 RAG 缓存（已实现）

```python
# mcp/tool_manager.py 注册时已设 cache_ttl=300
# 相同参数 5 分钟内命中缓存，不重新检索
self._mcp.register(MCPToolDef(
    ...
    cache_ttl=300.0,
))
```

---

## 五、异步化非主链路

```
主链路（同步等待）→ LLM → 回复用户
                                │
                                └─ asyncio.create_task（不阻塞返回）
                                      ├─ 更新用户画像
                                      ├─ 写入对话日志
                                      └─ 更新 CRM 数据
```

当前项目中 `api/main.py:240` 已有此模式：

```python
# 不阻塞用户回复
asyncio.create_task(_memory.update_profile(req.user_id, conv_id))
```

可进一步扩展的异步任务：

| 任务 | 当前状态 | 改造方案 |
|------|---------|---------|
| 用户画像更新 | ✅ 已有 create_task | 增加错误重试 |
| 意图日志写盘 | ✅ 同步写 JSONL | 改为异步写入或 MQ |
| 对话摘要生成 | ❌ 未实现 | 后台 LLM 生成，下轮使用 |
| 埋点/指标推送 | ❌ 未实现 | 异步推 Prometheus |

---

## 六、面试回答话术

如果面试官问："你的 Agent 系统高并发下怎么保证可靠性？"

> 我会从两个维度考虑：**保护系统不被冲垮** 和 **降低单次请求成本**。
>
> 在保护系统方面，我做了四层递进的限流：
>
> 第一层是**全局令牌桶**，控制系统总入口 QPS，防止突发流量打挂 LLM API；
> 第二层是**用户维度的频控**，同一个用户 30 秒内最多 N 条消息，防止单用户刷爆；
> 第三层是**同请求去重**，同一个用户发完全相同的消息如果正在处理中就不重复调 LLM；
> 第四层是**请求队列 + 固定工作协程**，队列满了快速失败，不堆积请求。
>
> 在降低请求成本方面：
>
> 第一是**多级缓存**——Planner 对 message 做 hash 缓存、RAG 对 query 做 TTL 缓存、常见问题直接缓存最终回复；
> 第二是**Tool 共享**——多个 Skill 共用 RAG 结果，只检索一次；
> 第三是**异步化非主链路**——用户画像更新、日志写入、摘要生成放到后台，不阻塞用户回复。
>
> 此外，当系统负载过高时，我们还有**负载感知降级**——自动切到纯代码的 PlannerFallback + 跳过 RAG，保证系统还能正常响应，只是回复质量降级。

---

## 七、实施路线

### Phase 1 — 综合限流（本文核心）

- [ ] `core/rate_limiter.py`：TokenBucket + UserRateLimiter + RequestDedup
- [ ] `core/work_queue.py`：LLMWorkQueue（asyncio.Queue + 工作协程）
- [ ] `api/main.py`：四层串联到 `/chat` 和 `/chat/stream`

### Phase 2 — 缓存增强

- [ ] `core/intent_recognizer.py`：启用 Planner 的 `_cache`（5 行代码）
- [ ] `mcp/tool_manager.py`：验证 RAG 缓存是否生效
- [ ] 可选：ResponseAgent 缓存

### Phase 3 — 异步深化

- [ ] 意图日志改为异步写入
- [ ] 用户画像更新增加重试机制
- [ ] 探测 Prometheus 埋点接入

---

## 八、与现有降级体系的关系

```
限流层（本文）         降级层（upgrade.md）        基础设施层
┌──────────────┐      ┌──────────────────┐       ┌────────────┐
│ TokenBucket  │      │ PlannerFallback  │       │ Redis 降级 │
│ UserFreqCtrl │  →   │ RAG 链路降级     │  →    │ ChromaDB   │
│ RequestDedup │      │ ResponseAgent    │       │ 超时保护   │
│ WorkQueue    │      │ retry/fallback   │       │            │
└──────────────┘      └──────────────────┘       └────────────┘
     入口防护             能力降级                  基础设施
```

三者共同构成 EchoMind 的**完整可靠性体系**：

- **限流层**：在入口挡住不该进来的请求
- **降级层**：进来的请求即使部分组件不可用也能返回有意义的结果
- **基础设施层**：底层依赖（Redis/ChromaDB/LLM）不可用时系统不崩溃
