"""
Prompt Injection 检测层。

在用户输入到达任何 LLM 之前，通过关键词匹配快速判断是否为注入攻击。

原理：
  1. 加载预定义的注入模式库（JSON 文件）
  2. 每个用户消息到来时，遍历模式库做子串匹配
  3. 匹配成功 → 判定为注入，直接拦截

与向量方案的对比：
  - 向量方案（ChromaDB + all-MiniLM-L6-v2）：对中文不生效，且依赖 ONNX 运行时
  - 关键词匹配：精确可靠，无模型依赖，对中英文均有效，性能更好
"""
import json
import logging
import pathlib
from typing import List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATTERNS_PATH = pathlib.Path(__file__).parent / "injection_patterns.json"


class InjectionDetector:
    """基于关键词匹配的 Prompt Injection 检测器。

    遍历注入模式库，检查用户输入是否包含任一模式子串。
    匹配规则为简单子串匹配（区分大小写不敏感）。
    """

    def __init__(
        self,
        threshold: float = 0.85,
        patterns_path: Optional[str] = None,
        chroma_path: str = "./data/chroma",
    ):
        """初始化 InjectionDetector。

        Args:
            threshold: 保留兼容，未使用（子串匹配为精确匹配）
            patterns_path: 注入模式库路径，默认使用自带的 injection_patterns.json
            chroma_path: 保留兼容，未使用
        """
        self.threshold = threshold
        self._patterns: List[str] = []

        path = patterns_path or str(_DEFAULT_PATTERNS_PATH)
        self._load_patterns(path)

    def _load_patterns(self, path: str) -> None:
        """从 JSON 文件加载注入模式。

        Args:
            path: 模式库 JSON 文件路径
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or not all(isinstance(s, str) for s in data):
                raise ValueError("模式库格式错误，应为字符串数组")
            self._patterns = data
            logger.info(f"注入检测: 已加载 {len(data)} 条模式 ({path})")
        except Exception as ex:
            logger.warning(f"注入检测: 加载模式库失败 ({path}): {ex}")

    def check(self, message: str) -> bool:
        """检测用户消息是否为注入攻击。

        遍历模式库做子串匹配，任一匹配即判定为注入。

        Args:
            message: 用户输入消息

        Returns:
            True = 检测到注入攻击，应予拦截
            False = 正常消息，继续处理
        """
        if not message or not message.strip():
            return False

        if not self._patterns:
            return False

        msg_lower = message.lower()

        for pattern in self._patterns:
            if pattern.lower() in msg_lower:
                logger.warning(
                    f"注入检测: 拦截! 匹配={pattern!r} "
                    f"消息={message[:80]!r}"
                )
                return True

        return False

    @property
    def is_ready(self) -> bool:
        """检测器是否就绪（模式库已加载）。"""
        return len(self._patterns) > 0

    @property
    def pattern_count(self) -> int:
        """注入模式数量。"""
        return len(self._patterns)
