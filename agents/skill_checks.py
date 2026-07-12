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
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


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


# ── 注册表 ──

_CHECKERS: Dict[str, Any] = {}


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
