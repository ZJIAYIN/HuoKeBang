# 体验券系统可靠性升级方案（终版·确认）

---

## 整体流程

```
┌──────────────────────────────────────────────────────────────┐
│  POST /coupon/claim                                          │
│                                                              │
│  ① Lua 脚本（Redis）                                         │
│     ├── 一人一单：claimed:<user_id> 存在性检查                 │
│     ├── 不超卖：GET stock → 如果 <=0 返回 sold_out            │
│     ├── DECR stock（预扣）                                    │
│     └── SET claimed:<user_id>=conv_id EX 3600               │
│                                                              │
│  ② MySQL 兜底判重（同一事务）                                  │
│     ├── SELECT 1 FROM coupon_order                           │
│     │   WHERE user_id=? AND status IN ('claimed','lead_submitted') │
│     │   → 查到则回滚 Redis（Lua 脚本补库存 + DEL claimed）      │
│     │   → 返回前端 duplicate                                  │
│     │                                                         │
│     ├── 未查到 → INSERT coupon_order (status='claimed')       │
│     ├── INSERT coupon_outbox (status='init')                  │
│     └── COMMIT                                                │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  OutboxScanner（后台线程，每 2s 扫一次）                       │
│                                                              │
│  SELECT * FROM coupon_outbox WHERE status='init' LIMIT 50    │
│  → 发 RMQ 消息到 coupon.delay 队列（TTL=60s）                 │
│  → 成功 → UPDATE status='sent'                               │
│  → 失败 → 重试，3 次上限 → status='failed' 报警               │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  60s TTL → DLQ coupon.timeout → CouponWorker 消费            │
│                                                              │
│  ① 查 MySQL coupon_order / coupon_lead                       │
│     ├── 该 user_id 已有 lead 记录 → 跳过（已留资，券锁定）     │
│     └── 无 lead 记录 → 执行释放：                              │
│           ├── Lua：DEL claimed:<user_id> + INCR stock         │
│           └── UPDATE coupon_order SET status='released'      │
│                                                              │
│  ② 消费异常 → nack + requeue（有限重试，超限死信报警）          │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  POST /coupon/lead（留资）                                    │
│                                                              │
│  INSERT INTO coupon_lead (...)                                │
│  UPDATE coupon_order SET status='lead_submitted' WHERE id=?  │
│  → 后续 CouponWorker 查到 lead 记录，跳过释放                  │
└──────────────────────────────────────────────────────────────┘
```

---

## 核心设计要点

### Lua 脚本职责（只做 Redis 层快速校验）

```lua
-- coupon_claim.lua（改造现有脚本）
-- KEYS[1] = coupon:test_drive:stock
-- KEYS[2] = coupon:test_drive:claimed:<user_id>
-- ARGV[1] = user_id
-- ARGV[2] = conv_id

-- 一人一单检查
local claimed = redis.call("EXISTS", KEYS[2])
if claimed == 1 then
    return cjson.encode({status="duplicate"})
end

-- 不超卖检查
local stock = tonumber(redis.call("GET", KEYS[1]) or 0)
if stock <= 0 then
    return cjson.encode({status="sold_out", stock=0})
end

-- DECR 预扣
local remaining = redis.call("DECR", KEYS[1])
if remaining < 0 then
    redis.call("INCR", KEYS[1])  -- 回滚
    return cjson.encode({status="sold_out", stock=0})
end

-- 写占位标记（幂等 + 一人一单，TTL 按业务需要）
redis.call("SET", KEYS[2], ARGV[2], "EX", ARGV[3])
return cjson.encode({status="ok", stock=tonumber(remaining)})
```

### MySQL 是唯一判重依据

Lua 通过后，MySQL 事务内再做一次查询——**以 MySQL 为准**。

```python
# API handler 伪代码
async def coupon_claim(user_id, conv_id):
    # ① Lua 预扣（Redis 快速校验 + 预占位）
    lua_result = await claim_lua(user_id, conv_id)
    if lua_result["status"] != "ok":
        return lua_result

    # ② MySQL 事务兜底判重
    try:
        async with db.transaction():
            exists = await db.fetch_one(
                "SELECT 1 FROM coupon_order "
                "WHERE user_id=? AND status IN ('claimed','lead_submitted')",
                user_id,
            )
            if exists:
                # MySQL 查到已有订单 → 回滚 Redis
                await rollback_lua(user_id)
                return {"status": "duplicate", "message": "已经领取过"}

            # 插入订单 + outbox 消息（同事务）
            order_id = await db.execute(
                "INSERT INTO coupon_order (user_id, conv_id, status) "
                "VALUES (?, ?, 'claimed')", user_id, conv_id,
            )
            await db.execute(
                "INSERT INTO coupon_outbox (order_id, event_type, payload) "
                "VALUES (?, 'claim_delay', ?)",
                order_id, json.dumps({"user_id": user_id, "order_id": order_id}),
            )

        return {"status": "ok", "order_id": order_id}

    except Exception as e:
        # DB 异常 → 回滚 Redis
        await rollback_lua(user_id)
        logger.error(f"DB 写入失败，已回滚 Redis: {e}")
        return {"status": "error", "message": "系统繁忙"}
```

### 回滚 Lua 脚本

```lua
-- coupon_claim_rollback.lua
-- MySQL 查到重复 / DB 异常时，撤销 Redis 预扣
-- KEYS[1] = coupon:test_drive:stock
-- KEYS[2] = coupon:test_drive:claimed:<user_id>

redis.call("DEL", KEYS[2])
local stock = redis.call("INCR", KEYS[1])
return cjson.encode({status="rolled_back", stock=tonumber(stock)})
```

### 释放 Lua 脚本

```lua
-- coupon_release.lua（改造现有脚本）
-- CouponWorker 消费 DLQ 时调用
-- KEYS[1] = coupon:test_drive:stock
-- KEYS[2] = coupon:test_drive:claimed:<user_id>

redis.call("DEL", KEYS[2])
local stock = redis.call("INCR", KEYS[1])
return cjson.encode({status="released", stock=tonumber(stock)})
```

---

## MySQL 表结构

```sql
-- 券订单表
CREATE TABLE coupon_order (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    user_id         VARCHAR(64)     NOT NULL,
    conv_id         VARCHAR(64)     NOT NULL,
    status          VARCHAR(20)     NOT NULL DEFAULT 'claimed'
                    COMMENT 'claimed|lead_submitted|released',
    stock_snapshot  INT             NOT NULL DEFAULT 0,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    INDEX idx_user_id (user_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 留资表
CREATE TABLE coupon_lead (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    order_id        BIGINT          NOT NULL,
    user_id         VARCHAR(64)     NOT NULL,
    name            VARCHAR(50)     NOT NULL,
    phone           VARCHAR(20)     NOT NULL,
    conv_id         VARCHAR(64)     NOT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_user_id (user_id),
    INDEX idx_order_id (order_id),
    CONSTRAINT fk_lead_order FOREIGN KEY (order_id) REFERENCES coupon_order(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 本地消息表
CREATE TABLE coupon_outbox (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    order_id        BIGINT          NOT NULL,
    event_type      VARCHAR(32)     NOT NULL COMMENT 'claim_delay',
    payload         JSON            NOT NULL,
    status          VARCHAR(10)     NOT NULL DEFAULT 'init' COMMENT 'init|sent|failed',
    retry_count     TINYINT         NOT NULL DEFAULT 0,
    max_retries     TINYINT         NOT NULL DEFAULT 3,
    error_msg       VARCHAR(512)    DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    INDEX idx_status_created (status, created_at),
    CONSTRAINT fk_outbox_order FOREIGN KEY (order_id) REFERENCES coupon_order(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

---

## 涉及文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `sql/init_coupon.sql` | 3 张表 DDL |
| 修改 | `data/coupon_claim.lua` | 调整：一人一单 + 不超卖，claimed TTL 按业务定 |
| 新增 | `data/coupon_claim_rollback.lua` | 回滚 |
| 修改 | `data/coupon_release.lua` | 简化：DEL claimed + INCR stock |
| 新增 | `agents/coupon_db.py` | 异步 MySQL 连接池 + CRUD |
| 新增 | `agents/outbox_scanner.py` | 扫 outbox 表发 RMQ |
| 新增 | `agents/coupon_worker.py` | 消费 DLQ，查 lead 决定是否释放 |
| 修改 | `agents/coupon_manager.py` | 集成 DB，重写 claim/release/rollback |
| 修改 | `api/main.py` | lifespan 启动 OutboxScanner + CouponWorker |
| 修改 | `docker-compose.yml` | 加 MySQL 8 服务 |
| 修改 | `.env` / `.env.example` | 加 MYSQL_URL |
| 修改 | `requirements.txt` | 加 aiomysql |
| 修改 | `README.md` | 更新架构说明 |
