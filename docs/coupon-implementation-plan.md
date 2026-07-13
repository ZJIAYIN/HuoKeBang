# 对话式体验券系统 — 执行计划

## 改动范围

```
后端： 4 新增 + 2 修改
前端： 2 修改
配置： 1 新增
```

---

## Phase 0 — 遗留代码清理 ✅

**已完成：** 7 文件修改，2 文件删除。留资入口已从 LLM 切换到前端表单。

---

## Phase 1 — Redis + Lua + CouponDecider（后端核心）

**目标：** 库存扣减原子化、CouponDecider 规则引擎跑通

| 任务 | 文件 | 操作 |
|------|------|------|
| 1.1 写 Lua 脚本 | `data/coupon_claim.lua` | **新增** — 原子扣库存 + 幂等检查 + 写 pending |
| 1.2 写 Lua 释放脚本 | `data/coupon_release.lua` | **新增** — 超时释放 + 已留资跳过 |
| 1.3 CouponDecider 规则引擎 | `agents/coupon_decider.py` | **新增** — 轮数>5 + PRICE/PRODUCT + 情绪好 |
| 1.4 CouponManager 封装 | `agents/coupon_manager.py` | **新增** — Lua 调用 + 库存查询 + 冷却管理 |
| 1.5 `POST /coupon/claim` | `api/main.py` | **修改** — 新增路由 |
| 1.6 `POST /coupon/lead` | `api/main.py` | **修改** — 新增路由 |
| 1.7 库存初始化 | `api/main.py` / lifespan | **修改** — 启动时 `SETNX` |

### 依赖关系

```
Lua 脚本  ──→  CouponManager
                         │
CouponDecider             │
       │                  │
       └─── 无依赖 ───────┤
                          │
                    ┌─────┴──────┐
               /coupon/claim   /coupon/lead
```

### 验证

```bash
redis-cli SET coupon:test_drive:stock 5
curl -X POST /coupon/claim -d '{"user_id":"u1","conv_id":"c1"}'
# → {"status":"ok","stock":4}
curl -X POST /coupon/claim -d '{"user_id":"u1","conv_id":"c1"}'
# → {"status":"ok","stock":4"}  （幂等，不扣）
# 重复到售罄
```

---

## Phase 2 — RabbitMQ DLQ+TTL 超时检测

**目标：** 60s TTL + 死信队列自动触发 + 消费端释放 + 补偿通知

| 任务 | 文件 | 操作 |
|------|------|------|
| 2.1 RabbitMQ 配置 | `docker-compose.yml` | **修改** — 添加 RMQ 服务 |
| 2.2 RMQ 连接工具 | `agents/rmq_client.py` | **新增** — 连接 + 声明 DLQ 队列拓扑 |
| 2.3 发消息到 delay 队列 | `agents/coupon_manager.py` | **修改** — claim 成功后发到 `coupon.delay` |
| 2.4 死信队列消费者 | `agents/coupon_worker.py` | **新增** — 监听 `coupon.timeout` + 调 Lua 释放 |
| 2.5 补偿通知 | `agents/coupon_worker.py` | **新增** — 系统消息推给用户 |

### 依赖关系

```
Phase1 CouponManager  ──→  发消息到 coupon.delay（TTL 到期 → coupon.timeout）
                                  │
                            CouponWorker  ←── docker-compose RMQ
                                  │
                            Lua release  ←── Phase1 脚本
```

### 验证

```bash
curl -X POST /coupon/claim -d '{"user_id":"u1","conv_id":"c1"}'
# 等 60s → 不提交表单 → stock 自动 +1
# 查看日志: "库存已释放: user=u1"
```

---

## Phase 3 — CouponDecider 接入 Orchestrator

**目标：** 对话满足条件时自动触发展券

| 任务 | 文件 | 操作 |
|------|------|------|
| 3.1 Orchestrator 集成 | `agents/orchestrator.py` | **修改** — orchestrate 后调用 CouponDecider |
| 3.2 CouponDecider 读取对话上下文 | `agents/coupon_decider.py` | **修改** — 从 planner_output 获取轮数/情绪/意图 |

### 架构

```
Orchestrator.orchestrate()
    ↓
[原有编排逻辑] → ResponseInput
    ↓
CouponDecider.should_issue(轮数, 情绪, sub_tasks, user_id)
    │
    ├─ True  → response_input.instructions.append("在末尾告知用户获得体验券...")
    └─ False → 不操作
    ↓
Response Agent 生成回复
```

### 验证

```
对话第 1 轮: "M8什么价格"
  → CouponDecider: 轮数≤5 → 不发

对话第 6 轮: "M8混动版油耗怎么样"（情绪 neutral）
  → CouponDecider: 轮数>5 ✅ PRICE+PRODUCT ✅ 情绪好 ✅
  → 回复末尾出现券卡片
```

---

## Phase 4 — 前端券卡片 + 留资表单

**目标：** 前端渲染券 UI、确认/拒绝流程、表单提交

| 任务 | 文件 | 操作 |
|------|------|------|
| 4.1 券卡片渲染 | `static/js/chat.js` | **修改** — 读取后端 `show_coupon` 字段 → 渲染卡片 |
| 4.2 确认按钮回调 | `static/js/chat.js` | **修改** — claimCoupon() → `POST /coupon/claim` |
| 4.3 拒绝按钮回调 | `static/js/chat.js` | **修改** — dismissCoupon() → 24h 冷却标记 |
| 4.4 留资浮层 | `static/js/chat.js` | **修改** — 确认后弹表单 + 倒计时 |
| 4.5 提交表单 | `static/js/chat.js` | **修改** — submitLeadForm() → `POST /coupon/lead` |
| 4.6 API 方法 | `static/js/app.js` | **修改** — 加 `claimCoupon` / `submitCouponLead` |
| 4.7 券卡片 CSS | `static/css/style.css` | **修改** — 卡片 + 表单 + 倒计时样式 |
| 4.8 表单图标 | `static/js/app.js` | **修改** — 加礼物/券图标 |

### 前端数据流

```
后端响应含 show_coupon=true 字段
    ↓
前端解析 → 渲染券卡片
    │
    ├─ [确认领取] → POST /coupon/claim
    │                   ↓ 成功
    │              弹留资浮层（60s 倒计时）
    │                   │
    │           ┌───────┴──────────┐
    │      用户填表提交        倒计时结束
    │           │                   │
    │     POST /coupon/lead     自动关闭浮层
    │     锁定成功消息          后台 RMQ 释放
    │
    └─ [暂不需要] → 隐藏卡片 → 24h 不发
```

### 验证

```javascript
// 模拟 AI 回复携带 show_coupon 字段
const doneEvent = {
  type: 'done',
  response: '恭喜获得试驾体验券 🎉',
  show_coupon: true,
  meta: { ... }
});
// → 应看到券卡片渲染
// → 点确认 → 弹表单
// → 填写 → 锁定
```

---

## Phase 5 — 端到端联调 + 文档

| 任务 | 操作 |
|------|------|
| 5.1 Docker Compose 加 RMQ | 确保 `docker-compose up` 正常 |
| 5.2 全链路测试 | 对话 6 轮 → 发券 → 确认 → 表单 → 超时释放 |
| 5.3 边界测试 | 库存=0 不发、幂等不扣、并发扣减 |
| 5.4 更新 README | 功能说明 + 架构图 |
| 5.5 更新 docs | 清理旧留资文档 |

---

## 阶段时间预估

| Phase | 内容 | 预估 |
|-------|------|------|
| **P1** | Lua + CouponDecider + API | **核心** |
| **P2** | RMQ DLQ+TTL + Worker | **核心** |
| **P3** | Orchestrator 集成 | 轻（1 文件改几行） |
| **P4** | 前端券卡片 + 表单 | **核心** |
| **P5** | 联调 + 文档 | 收尾 |

> **建议执行顺序：** P1 → P4 → P2 → P3 → P5
> 先跑通 P1 + P4 让前后端联调通（券能发、能领、能填表单），
> 再补 P2 超时释放（不影响主流程），
> P3 触发条件最后接（不影响手动测试）。
