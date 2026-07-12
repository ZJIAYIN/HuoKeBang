# 对话式试驾体验券系统 — 技术方案

## 1. 动机

### 1.1 当前问题

现有留资体系依赖 `LEAD_CAPTURE` skill，由 Planner 识别"用户愿意留电话"的意图后被动触发：

```
用户: "我想留个电话"
  → Planner 识别 LEAD_CAPTURE → "请问您的手机号是？"
  → 用户给号 → 记录
```

三个核心缺陷：

| 缺陷 | 表现 |
|------|------|
| **被动等待** | AI 不会主动创造留资动机，等用户自己提 |
| **生硬追问** | `CONTACT_NO` 冷却期外的每轮都可能追问"留个电话吗"，体验差 |
| **意图负担** | `CONTACT_NO` / `CONTACT_GIVE` / `CONTACT_FIX` 三个意图处理同一个事，Planner 误判率高 |

### 1.2 方案核心思想

**从"索取"变成"交换"**——AI 主动发放试驾体验券，用户因为想要券而主动填联系方式。

```
旧：  "方便留个电话吗？"                → 用户防御
新：  "恭喜获得一张到店试驾体验券 🎉    → 用户主动留资
        [确认领取]  [暂不需要]"
```

### 1.3 业务价值

| 维度 | 提升点 |
|------|--------|
| 留资率 | 券驱动留资，比追问电话的自然转化率高 |
| 用户体验 | 给用户"获得"的感觉，而不是"被索取" |
| 意图简化 | 去掉 CONTACT_NO / CONTACT_GIVE，减少 Planner 负担 |
| 技术展示 | 融合客服业务 + 电商高并发库存管理 |

---

## 2. 整体架构

```
┌────────────────────────────────────────────────────────────────────┐
│                      AgentEngine.run()                             │
│                                                                    │
│  Planner → Orchestrator ──→ CouponDecider ──→ Response Agent       │
│                            (规则引擎)          (LLM 生成)          │
│                                                                    │
└──────────────────────────────────┬─────────────────────────────────┘
                                   │
                          回复含 [体验券卡片]
                                   │
                             前端渲染
                          ┌───────┴──────┐
                          │              │
                    [确认领取]      [暂不需要]
                          │              │
                          ▼        卡片消失
                   POST /coupon/claim
                          │
                     ┌────┴────┐
                     │         │
                Redis Lua    RabbitMQ
                DECR 库存   延迟消息(1min)
                     │         │
                     ▼         ▼
              成功 ──→ 弹留资表单 ──→ 用户填 → 锁定券
                  │                  ├─ 不填 → RMQ 消费 → 释放 + 补偿
              失败 ──→ "名额已满"
```

---

## 3. 组件设计

### 3.1 CouponDecider（规则引擎）

**位置**：`agents/coupon_decider.py`（新增）

**输入**：对话状态（轮数、已出现意图、情绪、用户历史留资情况）

**输出**：`True / False`（本轮是否发券）

**规则**：

```
发券条件（全部满足才发）：
  ├── 对话轮数 ≥ 2（非第一次接触就发券）
  ├── 本轮情绪非负（emotion ∈ {positive, neutral}）
  ├── 本轮出现过 PRICE / PRODUCT（用户确实在看车）
  ├── 未处于冷却期（24h 内没发过券给这个用户）
  └── 用户未留过资（已有联系方式的不用发）

不发券条件（任一触发即不发）：
  ├── 用户情绪差（angry / frustrated / negative）
  ├── 用户本轮有 COMPLAINT
  ├── 用户已留资
  ├── 用户刚拒绝过（前端点了暂不需要）
  └── 全局库存 = 0
```

**设计原则**：纯规则，不调 LLM。确定性逻辑保障 0 歧义。

**接入点**：在 `Orchestrator.orchestrate()` 调用完毕后、`Response Agent` 生成前检查。

```python
# orchestrator.py 改动点
response_input = await self.orchestrator.orchestrate(...)

# 新增：CouponDecider 决策
coupon_decider = CouponDecider(redis_client)
should_issue = await coupon_decider.should_issue(
    user_id=user_id,
    conv_id=conv_id,
    emotion=planner_output.emotion,
    sub_tasks=planner_output.sub_tasks,
    rounds=round_count,
)

if should_issue:
    response_input.instructions.append(
        "你为用户准备了一张到店试驾体验券，"
        "在回复末尾用友好语气告知用户，"
        "引导用户点击确认领取。"
    )
```

### 3.2 库存管理（Redis + Lua）

**Key 设计**：

```
coupon:test_drive:stock      → 整数（全局库存，Atomic）
coupon:test_drive:pending    → Sorted Set（已领券但未留资的用户）
                                 score = 超时时间戳, member = user_id
coupon:test_drive:used       → Set（已成功留资的用户，幂等防重复）
coupon:cooldown:{user_id}    → "1"（24h TTL，发过券后进入冷却）
```

**Lua 脚本（扣库存 + 写入 pending，原子操作）**：

```lua
-- coupon_claim.lua
-- KEYS[1] = coupon:test_drive:stock
-- KEYS[2] = coupon:test_drive:pending
-- KEYS[3] = coupon:test_drive:used
-- ARGV[1] = user_id
-- ARGV[2] = expire_timestamp (now + 60s)

-- 幂等检查：已领过的直接返回成功
if redis.call('SISMEMBER', KEYS[3], ARGV[1]) == 1 then
    return {1, 0}  -- {成功, 剩余库存}
end

-- 原子扣减
local stock = redis.call('DECR', KEYS[1])
if stock < 0 then
    redis.call('INCR', KEYS[1])  -- 回滚
    return {0, 0}  -- {失败, 无余量}
end

-- 写入 pending 等待留资（超时时间戳）
redis.call('ZADD', KEYS[2], ARGV[2], ARGV[1])

return {1, stock}
```

### 3.3 RabbitMQ 延迟消息（超时检测）

**使用场景**：用户点击确认领取后，有 1 分钟时间填写联系方式。

- 如果 1 分钟内留资 → 锁定券，从 pending 移除
- 如果 1 分钟超时 → 释放库存 + 通知用户补偿

**消息结构**：

```json
{
  "type": "coupon_timeout",
  "user_id": "user_xxx",
  "conv_id": "conv_xxx",
  "coupon_type": "test_drive",
  "claimed_at": "2026-07-12 20:00:00"
}
```

**消费逻辑**：

```python
def handle_coupon_timeout(ch, method, properties, body):
    data = json.loads(body)
    user_id = data["user_id"]
    now = time.time()

    # 检查用户是否已完成留资
    if redis.sismember("coupon:test_drive:used", user_id):
        return  # 已留资，不释放

    # 检查是否仍在 pending 中且确实超时
    score = redis.zscore("coupon:test_drive:pending", user_id)
    if score and score <= now:
        # 释放库存
        redis.incr("coupon:test_drive:stock")
        redis.zrem("coupon:test_drive:pending", user_id)
        # 发送补偿通知（系统消息推送给用户）
        notify_compensation(user_id, conv_id)
```

### 3.4 前端券卡片

**位置**：`chat.js` 中的新组件（或内联模板）

**渲染条件**：AI 回复中检测到特殊标记（而非依赖意图识别）

**两种方式**：

| 方式 | 实现 | 优缺点 |
|------|------|--------|
| **标记位** | Response Agent 在回复末尾加 `[COUPON_CARD]`，前端解析渲染 | 不改 LLM 输出结构，纯前端逻辑 |
| **结构化 meta** | stream meta 中将 `coupon` 字段设为 `true`，前端按约定渲染 | 依赖 meta 传递 |

**推荐标记位方式**（改动最小）：

```html
<!-- 在 message-bubble 之后 -->
<div v-if="msg.role === 'ai' && msg.content.includes('[COUPON_CARD]')" class="coupon-card">
  <div class="coupon-card-body">
    <div class="coupon-icon">🎉</div>
    <div class="coupon-title">到店试驾体验券</div>
    <div class="coupon-desc">凭此券可到店体验指定车型，享受专属服务</div>
    <div class="coupon-actions">
      <button class="coupon-btn primary" @click="claimCoupon(i)">确认领取</button>
      <button class="coupon-btn secondary" @click="dismissCoupon(i)">暂不需要</button>
    </div>
  </div>
</div>
```

**确认后流程**：

```
点击确认 → POST /coupon/claim
            └─ 成功 → 隐藏卡片，弹留资表单
            └─ 失败 → toast "名额已满"
```

```html
<!-- 留资浮层 -->
<div v-if="showLeadForm" class="lead-form-overlay">
  <div class="lead-form-card">
    <h3>🎉 恭喜获得体验券</h3>
    <p>填写联系方式即可锁定名额</p>
    <input v-model="leadForm.name" placeholder="姓名" />
    <input v-model="leadForm.phone" placeholder="手机号" />
    <button @click="submitLeadForm">确认提交</button>
    <span class="countdown">剩余 {{ countdown }} 秒</span>
  </div>
</div>
```

**倒计时**：前端显示剩余时间，倒计时结束自动关闭浮层（但不影响后台超时检测）。

### 3.5 后端 API

#### POST /coupon/claim

```python
@router.post("/coupon/claim")
async def claim_coupon(user_id: str, conv_id: str):
    """
    用户点击确认领取体验券。
    1. Lua 原子扣库存
    2. 成功 → 入 RMQ 延迟消息
    3. 返回结果
    """
    script = """
        -- (见 3.2 节 Lua 脚本)
    """
    result = redis.eval(script, ...)
    if result[0] == 1:
        # 发送延迟消息
        rmq.send("coupon_timeout", {"user_id": user_id, ...}, delay=60_000)
        return {"status": "ok", "stock": result[1]}
    else:
        return {"status": "sold_out"}
```

#### POST /coupon/lead

```python
@router.post("/coupon/lead")
async def submit_lead(user_id: str, conv_id: str, phone: str, name: str = ""):
    """
    用户在领券后填写联系方式。
    1. 记录到 LeadStore (Redis)
    2. 标记 used 幂等集合
    3. 从 pending 中移除
    """
    redis.sadd("coupon:test_drive:used", user_id)
    redis.zrem("coupon:test_drive:pending", user_id)
    lead_store.save_lead(user_id, phone=phone, name=name)
    return {"status": "ok"}
```

---

## 4. 数据流完整时序

```
用户                         EchoMind                        Redis                RabbitMQ
 │                             │                              │                    │
 │  咨询 M8 价格                │                              │                    │
 │ ──────────────────────────> │                              │                    │
 │                             │  Planner: PRICE+PRODUCT      │                    │
 │                             │  Orchestrator → CouponDecider│                    │
 │                             │    → should_issue=True       │                    │
 │                             │  Response Agent 生成含券文案 │                    │
 │  "M8尊贵版26.98万...🎉"     │                              │                    │
 │  [确认领取] [暂不需要]      │                              │                    │
 │ <────────────────────────── │                              │                    │
 │                             │                              │                    │
 │  点击 [确认领取]             │                              │                    │
 │ ──────────────────────────> │  POST /coupon/claim          │                    │
 │                             │ ──────────────────────────>  │                    │
 │                             │  Lua DECR stock              │                    │
 │                             │  ZADD pending                │                    │
 │                             │ <──────────────────────────  │                    │
 │                             │                              │                    │
 │                             │  RMQ 发送延迟消息(60s)      │                    │
 │                             │ ──────────────────────────────────────────────> │
 │                             │                              │                    │
 │  "请填写联系方式"           │                              │                    │
 │ <────────────────────────── │                              │                    │
 │                             │                              │                    │
 │  用户填表提交               │                              │                    │
 │ ──────────────────────────> │  POST /coupon/lead           │                    │
 │                             │  → SADD used                 │                    │
 │                             │  → ZREM pending              │                    │
 │                             │  → 存 LeadStore              │                    │
 │  "锁定成功！"               │                              │                    │
 │ <────────────────────────── │                              │                    │
 │                             │                              │                    │
 │                             │        ...1min后...           │                    │
 │                             │                              │                    │
 │                             │                              │  RMQ 投递到消费端  │
 │                             │ <────────────────────────────────────────────── │
 │                             │  ZSCORE 发现已留资 → 忽略   │                    │
 │                             │                              │                    │
 ──── 如果超时未留资 ────       │                              │                    │
 │                             │  ZSCORE pending → 超时      │                    │
 │                             │  INCR stock（释放）          │                    │
 │                             │  ZREM pending                │                    │
 │  "名额已释放，下次再试"     │                              │                    │
 │ <────────────────────────── │                              │                    │
```

---

## 5. 现有代码改动清单

### 5.1 新增文件

| 文件 | 职责 |
|------|------|
| `agents/coupon_decider.py` | 规则引擎：判断本轮是否发券 |
| `agents/coupon_worker.py` | RMQ 消费端：超时释放 + 补偿通知 |
| `api/coupon.py`（或内联到 main.py）| `/coupon/claim` 和 `/coupon/lead` 接口 |
| `data/coupon_claim.lua` | Redis Lua 原子扣减脚本 |

### 5.2 修改文件

| 文件 | 改动 |
|------|------|
| `agents/orchestrator.py` | `orchestrate()` 后接入 CouponDecider，酌情注入 instruction |
| `api/main.py` | 注册 coupon 路由、RMQ 连接、后台 worker 启动 |
| `static/js/chat.js` | 券卡片渲染、确认/拒绝/留资表单、倒计时 |
| `static/js/app.js` | 新增 `api.claimCoupon` / `api.submitCouponLead` 方法 |
| `static/css/style.css` | 券卡片 + 留资浮层样式 |
| `docker-compose.yml` | 新增 RabbitMQ 服务 |

### 5.3 意图变更

| 变更 | 操作 |
|------|------|
| `CONTACT_NO` | **废弃**。用户拒绝旧方式改由前端按钮处理，不再需要 NLU 识别 |
| `CONTACT_GIVE` | **废弃**。留资通过券表单主动填写，不再需要 NLU 识别 |
| `CONTACT_FIX` | **保留**。修改联系方式仍需 NLU 理解 |
| `LEAD_CAPTURE` | **改造**。不再作为独立意图运行，语义转为"发券成功后的留资确认" |

Planner System Prompt 调整：

```
当前意图列表：GREETING / PRODUCT / PRICE / PURCHASE / COMPLAINT / CHITCHAT / CONTACT_FIX / LEAD_CAPTURE

LEAD_CAPTURE 仅在用户在券表单之外主动提供联系方式时触发。
CONTACT_NO 和 CONTACT_GIVE 已废弃，不再识别。
```

### 5.4 环境依赖新增

```
# requirements.txt 新增
rabbitmq-client==x.x  # 或 pika / aio-pika
```

---

## 6. 库存模式与配置

### 6.1 库存来源

体验券总数通过管理接口配置（不涉及资金，纯营销名额）：

```python
COUPON_CONFIG = {
    "test_drive": {
        "total": 50,            # 总名额
        "valid_days": 7,        # 到店有效期
        "timeout_seconds": 60,  # 留资超时时间
        "compensation": "下次咨询优先安排试驾",
    }
}
```

启动时初始化：

```python
redis.setnx("coupon:test_drive:stock", COUPON_CONFIG["test_drive"]["total"])
```

### 6.2 补偿策略

超时未留资的用户，下一次对话时系统消息通知补偿：

```
系统: "之前为您保留的体验券名额已释放，下次咨询时我们再为您优先安排。"
```

如果用户再一次进入发券条件，可以再次发放（24h 冷却期从最后一次发放算起）。

---

## 7. 边界情况

| 场景 | 处理 |
|------|------|
| 用户连续确认多次 | Lua 脚本幂等检查，已领券的返回成功不扣库存 |
| 用户确认后不填表也不关页面 | 倒计时到自动关闭，后台超时检测兜底 |
| 库存刚好只剩 1 个，多人同时确认 | Lua DECR 原子性保障，不会超卖 |
| 用户确认后去别处留资再回来填 | RMQ 消费时检查 used 集合，已留资不释放 |
| RMQ 宕机 | 超时检测降级为定时扫描 Sorted Set，精度降低但功能不丢 |
| 用户先拒绝（暂不需要）后又想要 | 拒绝标记 24h 冷却，冷却期后重新进入发券决策 |
| 多个车系多种券 | 未来通过 `coupon:{type}:stock` 扩展 |

---

## 8. 实施计划

| 阶段 | 内容 | 涉及文件 |
|------|------|---------|
| **P0** | CouponDecider 规则引擎 + Lua 脚本 + `/coupon/claim` | `coupon_decider.py`, `coupon_claim.lua`, `api/main.py` |
| **P1** | 前端券卡片 + 留资表单 | `chat.js`, `app.js`, `style.css` |
| **P2** | RabbitMQ 延迟消息 + 消费端 + 超时释放 | `coupon_worker.py`, `docker-compose.yml` |
| **P3** | 意图精简 + 废弃 CONTACT_NO/GIVE | `orchestrator.py`, planner prompt |
| **P4** | 端到端测试 + 库存初始化 + 管理接口 | 测试 + docs |

---

## 9. 与离线评测的衔接

新增 `COUPON_ISSUE` 意图 + 券相关测试用例：

```jsonl
{"query": "我想试驾M8", "sub_tasks": ["PRODUCT", "LEAD_CAPTURE"]}
{"query": "不需要，别给我发券", "sub_tasks": ["CONTACT_NO"]}  # 旧数据兼容
```

发券决策的正确性（该发时没发 / 不该发时发了）也纳入 badcase 回流范围。
