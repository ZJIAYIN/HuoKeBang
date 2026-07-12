# Skill Markdown 化技术方案

## 1. 背景与目标

### 现状痛点

- Skill 定义在 Python 类中，新增/修改需改代码、提交、部署
- 无版本管理，更新必须全量上线
- Skill 和指令耦合在代码中，运营人员无法直接修改话术

### 目标

1. **Skill 定义从 `.py` 迁移到 `.md`** — 元数据 + instruction 声明化
2. **动态加载 + 热重载** — 增删改 skill 无需重启进程
3. **版本 + 灰度发布** — 按用户比例逐步放量，风险可控

## 2. 整体架构

```
┌──────────────────────────────────────────────────┐
│                    agents/                        │
│                                                    │
│   skills/                                          │
│     weather.md         ← skill 定义（运营可改）      │
│     greeting.md                                    │
│     price.md                                       │
│     ...                                            │
│                                                    │
│   skill_loader.py      ← 新增：扫描 .md → 内存对象    │
│   skill_checks.py      ← 新增：校验逻辑统一收口       │
│   skill_registry.py    ← 改造：多版本 + 灰度路由      │
│   skill_watcher.py     ← 新增：热重载文件监听         │
│   orchestrator.py      ← 微调：resolve 传 user_id    │
└──────────────────────────────────────────────────┘
```

### 数据流

```
.md 文件
  │
  ▼
SkillLoader.load_all()    ← 扫描目录，解析 frontmatter
  │
  ▼
SkillRegistry.reload()    ← 写入内存，版本管理
  │
  ▼（运行时每次请求）
SkillRegistry.resolve(name, user_id)  ← 一致性哈希路由
  │
  ▼
skill_checks.check_skill(name, slots, emotion, required_slots)
  │
  ▼
Orchestrator 编排
```

## 3. SkillDescriptor 数据类

所有 .md 文件解析后统一表示为这个对象（内存中唯一形态）：

```python
@dataclass
class SkillDescriptor:
    name: str               # 技能标识，与 Planner sub_tasks 匹配
    version: str            # 语义版本号，如 "1.0.0"
    status: str             # "active" | "deprecated"
    rollout: int            # 灰度流量占比 0-100
    required_slots: List[str]
    optional_slots: List[str]
    required_tools: List[Tool]
    auto_evaluate: bool
    instruction: str        # .md 正文内容
    file_path: str          # 来源文件路径
    checksum: str           # 文件 MD5，用于变更检测
```

## 4. skill.md 格式定义

### 格式规范

- **YAML frontmatter**：`---` 包裹的元数据区块
- **正文**：frontmatter 之后的内容为 `instruction`
- **编码**：UTF-8

### 模板

```markdown
---
name: PRICE
version: "1.0.0"
status: active
rollout: 100
auto_evaluate: false
required_slots:
  - model
required_tools:
  - rag
optional_slots:
  - trim
---

根据【知识】中的价格信息回答用户的询价。
包括车型指导价、当前优惠幅度、落地价估算。
如果用户问的是具体配置，先确认配置名称再报价。
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✓ | 大写标识，与 Planner 的 sub_tasks 值一致 |
| `version` | ✓ | 语义化版本号，如 `"1.0.0"` |
| `status` | ✓ | `active` 或 `deprecated` |
| `rollout` | ✗ | 灰度比例 0~100，默认 100（全量）|
| `required_slots` | ✗ | 必需槽位列表，默认 `[]` |
| `optional_slots` | ✗ | 可选槽位列表，默认 `[]` |
| `required_tools` | ✗ | 所需工具列表，默认 `[]` |
| `auto_evaluate` | ✗ | 是否每轮自动评估，默认 `false` |
| 正文 | ✓ | instruction，传给 Response Agent 的提示词 |

## 5. 灰度发布机制

### 核心逻辑

同一 `name` 允许多个 `.md` 文件共存（不同版本），通过一致性哈希按 `user_id` 决定用户命中哪个版本。

```
用户请求
  │
  ▼
hash = md5(user_id) % 100
  │
  ▼
遍历该 name 的所有版本（按 version 降序）
  累加 rollout
  hash < 累加值？ → 命中该版本
  否则 → 继续遍历下一个
  │
  ▼
返回 SkillDescriptor
```

### 灰度推进示例

| 阶段 | 文件 | 动作 |
|---|---|---|
| 现状 | `weather.md` (v1.0, rollout=100) | 全量使用旧话术 |
| 灰度 5% | 新增 `weather_v2.md` (v2.0, rollout=5) | 5% 用户看到新话术 |
| 扩量 30% | 修改 `weather_v2.md` rollout=30 | 30% 用户看到新话术 |
| 全量 | 修改 `weather_v2.md` rollout=100, 删除 `weather.md` (v1.0) | 全量切换完成 |

### 一致性保证

- 同一 `user_id` 始终命中同一版本，体验一致
- 文件修改后 checksum 变化触发热重载，存量会话不受影响

## 6. 校验逻辑（skill_checks.py）

校验逻辑统一收口在一个文件，用 dict 映射，不搞动态 import。

```python
# skill_checks.py — 所有 skill 的校验函数在此

def _default_check(slots, emotion, required_slots):
    """默认规则：情绪无限制，槽位齐全即可执行"""
    missing = [s for s in required_slots if not slots.get(s)]
    if missing:
        return {"ok": False, "missing": missing}
    return {"ok": True}

def _check_lead_capture(slots, emotion, required_slots):
    """LeadCapture 特殊规则：负面情绪或已拒绝时跳过"""
    if emotion in ("angry", "negative", "frustrated"):
        return {"ok": False, "silent": True}
    if slots.get("lead_refused"):
        return {"ok": False, "silent": True}
    return _default_check(slots, emotion, required_slots)

# 特殊校验注册表——只有需要特殊逻辑的 skill 才加
_CHECKERS = {
    "LEAD_CAPTURE": _check_lead_capture,
}

def check_skill(name: str, slots: dict, emotion: str,
                required_slots: list) -> dict:
    """统一入口，Orchestrator 只调这个函数"""
    checker = _CHECKERS.get(name, _default_check)
    return checker(slots, emotion, required_slots)
```

设计原则：
- 80% 的 skill 用默认规则，无需写任何校验代码
- 特殊规则集中在同一文件，不散落在多个 validator 中
- 返回值统一为 `{"ok": bool, "missing": [...], "silent": bool, "reason": ""}`

## 7. 热重载机制

### 方案选型：轮询（轻量）

不引入 watchdog 依赖，用 `asyncio` 定时轮询文件 checksum。

```python
class SkillWatcher:
    """监听 skills/ 目录变化，触发热重载"""

    CHECK_INTERVAL = 10  # 秒

    def __init__(self, loader: SkillLoader, registry: SkillRegistry):
        self.loader = loader
        self.registry = registry
        self._checksums: dict[str, str] = {}

    async def start(self):
        """启动后台轮询协程"""
        while True:
            for fpath in self.loader.skills_dir.glob("*.md"):
                cs = md5(fpath.read_bytes())
                if self._checksums.get(fpath.name) != cs:
                    self._checksums[fpath.name] = cs
                    descriptor = self.loader.parse_file(fpath)
                    self.registry.reload(descriptor)
            await asyncio.sleep(self.CHECK_INTERVAL)
```

### 线程安全

- `SkillRegistry.reload()` 内部加 `asyncio.Lock`
- Orchestrator 在 `resolve()` 时通过同一把锁读取，不会读到半更新状态

## 8. 变更清单

### 新增文件

| 文件 | 估行 | 职责 |
|---|---|---|
| `agents/skill_loader.py` | ~120 | 目录扫描、frontmatter 解析、SkillDescriptor 构建 |
| `agents/skill_checks.py` | ~60 | 校验函数注册表 + 默认校验 |
| `agents/skill_watcher.py` | ~60 | 文件变更轮询 + 热重载触发 |

### 改造文件

| 文件 | 改动量 | 说明 |
|---|---|---|
| `agents/skill_registry.py` | 重写 ~100 行 | 存储 `List[SkillDescriptor]`，一致性哈希路由，reload 接口 |
| `agents/orchestrator.py` | 微调 ~20 行 | 传 user_id 调用 `resolve()`，换用 `skill_checks.check_skill()` |

### 迁移：现有 .py → .md

| 当前 .py 文件 | 对应 .md 文件 | 是否有特殊校验 |
|---|---|---|
| `greeting.py` | `greeting.md` | 否 |
| `weather.py` | `weather.md` | 否 |
| `product.py` | `product.md` | 否 |
| `price.py` | `price.md` | 否 |
| `finance.py` | `finance.md` | 否 |
| `complaint.py` | `complaint.md` | 否 |
| `purchase.py` | `purchase.md` | 否 |
| `contact_no.py` | `contact_no.md` | 否 |
| `lead_capture.py` | `lead_capture.md` | **是** — 需在 `skill_checks.py` 注册 |

### 可删除文件

迁移完成后，以下文件可以移除：

- `agents/skills/base.py`（Tool 枚举迁到 `tool_layer.py` 或独立文件）
- `agents/skills/__init__.py`（不再有 Python 类需要导出）
- `agents/skills/*.py`（所有 skill Python 文件）

## 9. 兼容考虑

### 过渡期

`SkillRegistry` 同时支持两种来源：
- `.md` 文件 → `SkillDescriptor`
- `.py` 类 → `BaseSkill` 子类（保持现有接口）

通过 `resolve()` 方法统一返回 `SkillDescriptor`，从 `.py` 类读取时自动构造：

```python
def _from_class(cls: Type[BaseSkill]) -> SkillDescriptor:
    return SkillDescriptor(
        name=cls.name,
        version="legacy",
        status="active",
        rollout=100,
        required_slots=list(cls.required_slots),
        ...
        instruction=cls.instruction,
    )
```

这样可以在线逐个迁移，不用一次全改完。

### 启动顺序

1. `SkillLoader` 扫描 .md 目录
2. 有 .md 文件 → 使用 .md 定义
3. 部分 skill 没有 .md 文件 → 回退到 .py 类
4. `.md` 和 `.py` 同名时 → `.md` 优先

## 10. 回滚方案

- **话术回滚**：git revert .md 文件，热重载自动生效
- **版本回滚**：保留旧版本 .md 文件，调低新版本的 `rollout` 即可
- **全量回滚**：删除新版 .md 文件，旧版自动恢复 100% 流量
- **极端回滚**：切换 feature flag 强制使用 `.py` 类按旧模式运行
