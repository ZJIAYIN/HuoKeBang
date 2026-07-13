-- ═══════════════════════════════════════════════════════════════
-- EchoMind 体验券系统 — MySQL 表结构
-- ═══════════════════════════════════════════════════════════════

-- 券库存表（Redis 库存的 MySQL 权威数据源）
-- 使用乐观锁版本号防止并发扣减
CREATE TABLE IF NOT EXISTS coupon_stock (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    coupon_type     VARCHAR(32)     NOT NULL DEFAULT 'test_drive' COMMENT '券类型',
    total           INT             NOT NULL DEFAULT 0 COMMENT '总库存',
    remaining       INT             NOT NULL DEFAULT 0 COMMENT '当前剩余库存',
    version         INT             NOT NULL DEFAULT 0 COMMENT '乐观锁版本号',
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    UNIQUE INDEX idx_coupon_type (coupon_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 券订单表（每行记录 = 一次领取）
CREATE TABLE IF NOT EXISTS coupon_order (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    user_id         VARCHAR(64)     NOT NULL,
    conv_id         VARCHAR(64)     NOT NULL,
    status          VARCHAR(20)     NOT NULL DEFAULT 'claimed'
                    COMMENT 'claimed|lead_submitted|released',
    stock_snapshot  INT             NOT NULL DEFAULT 0 COMMENT '领取时的库存快照',
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    INDEX idx_user_id (user_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 留资表（消费者从此表扫描判断是否已留资）
CREATE TABLE IF NOT EXISTS coupon_lead (
    id              BIGINT          PRIMARY KEY AUTO_INCREMENT,
    order_id        BIGINT          NOT NULL,
    user_id         VARCHAR(64)     NOT NULL,
    name            VARCHAR(50)     NOT NULL,
    phone           VARCHAR(20)     NOT NULL,
    conv_id         VARCHAR(64)     NOT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_user_id (user_id),
    INDEX idx_order_id (order_id),
    INDEX idx_created_at (created_at),
    CONSTRAINT fk_lead_order FOREIGN KEY (order_id) REFERENCES coupon_order(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 本地消息表（Outbox 模式，保证 RMQ 消息可靠投递）
CREATE TABLE IF NOT EXISTS coupon_outbox (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
