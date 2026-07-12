"""
skill_checks — 所有 Skill 的校验逻辑统一收口。

设计原则：
  1. 80% 的 skill 用默认校验（槽位齐全即可执行）
  2. 特殊逻辑集中写在这个文件里，不散落在多个 validator 中
  3. Orchestrator 只调 check_skill() 一个入口

返回值格式（统一）：
  {"ok": True}                                      → 可以执行
  {"ok": False, "missing": ["phone"]}               → 缺槽位，LLM 在末尾追问
  {"ok": False, "silent": True}                     → 安静跳过，不进 pending
  {"ok": False, "reason": "情绪不适合"}              → pending 展示原因
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 拒绝留资冷却时间窗口（默认 24 小时内不再追问）
LEAD_COOLDOWN_HOURS = 24

# ── 校验函数 ──


def _default_check(
    slots: Dict[str, Any],
    emotion: str,  # noqa: ARG001 — 默认规则不关心情绪
    required_slots: List[str],
) -> Dict[str, Any]:
    """默认规则：情绪无限制，槽位齐全即可执行。"""
    missing = [s for s in required_slots if s not in slots or slots[s] is None]
    if missing:
        return {"ok": False, "missing": missing}
    return {"ok": True}


def _check_lead_capture(
    slots: Dict[str, Any],
    emotion: str,
    required_slots: List[str],
) -> Dict[str, Any]:
    """LeadCapture 特殊规则。

    执行条件：情绪 OK + 不在冷却期 + phone 已提供。

    四种结果：
      - 情绪差          → ok=False reason="情绪不适合"
      - 拒绝冷却期内    → ok=False silent=True
      - phone 缺失      → ok=False missing=["phone"] → LLM 追问
      - phone 存在      → ok=True → 确认联系方式
    """
    # 1. 情绪检查
    blocked = {"angry", "very_negative", "negative", "skeptical", "frustrated"}
    if emotion.lower() in blocked:
        return {"ok": False, "reason": f"情绪 '{emotion}' 不适合执行此任务"}

    # 2. 冷却期检查
    if _is_lead_refused(slots):
        return {"ok": False, "silent": True}

    # 3. 槽位检查
    missing = [s for s in required_slots if s not in slots or slots[s] is None]
    if missing:
        return {"ok": False, "missing": missing}

    return {"ok": True}


# ── 冷却期检查（LeadCapture 专用） ──


def _is_lead_refused(slots: Dict[str, Any]) -> bool:
    """检查用户在冷却期内是否拒绝过留资。

    支持两种格式：
      - lead_refused = True（当前会话拒绝过，Planner 输出）
      - lead_refused_at = ISO 时间戳（精确时间窗口，由代码设置）
    """
    # 简单布尔值
    if slots.get("lead_refused") is True:
        return True

    # 时间戳格式 → 检查是否在冷却窗口内
    raw = slots.get("lead_refused_at")
    if raw:
        try:
            if isinstance(raw, str):
                refused_at = datetime.fromisoformat(raw)
            elif isinstance(raw, (int, float)):
                refused_at = datetime.fromtimestamp(raw)
            else:
                refused_at = raw
            elapsed = datetime.now() - refused_at
            if elapsed < timedelta(hours=LEAD_COOLDOWN_HOURS):
                return True
            # 超过冷却期 → 允许再次询问
            return False
        except (ValueError, TypeError):
            return True  # 格式异常时保守处理

    return False


# ── 注册表 ──

# 只有需要特殊校验的 skill 才加到这里
_CHECKERS: Dict[str, Any] = {
    "LEAD_CAPTURE": _check_lead_capture,
}


# ── 统一入口 ──


def check_skill(
    name: str,
    slots: Dict[str, Any],
    emotion: str,
    required_slots: List[str],
) -> Dict[str, Any]:
    """统一校验入口。Orchestrator 只调这个函数。

    返回值见模块文档。
    """
    checker = _CHECKERS.get(name, _default_check)
    try:
        return checker(slots, emotion, required_slots)
    except Exception as exc:
        logger.error(f"Skill 校验异常 [{name}]: {exc}")
        return {"ok": False, "reason": "校验异常", "silent": True}
