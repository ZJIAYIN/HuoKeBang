"""
Prompt Injection 检测层。

在用户输入到达任何 LLM 之前，通过 ChromaDB 向量相似度
快速判断是否为注入攻击。

原理：
  1. 加载预定义的注入模式库（JSON 文件）
  2. 存入 ChromaDB collection，由 ChromaDB 内置模型自动嵌入
  3. 每个用户消息到来时，query ChromaDB 算相似度
  4. 最高分超过阈值 → 判定为注入，直接拦截

ChromaDB 不可用时降级放行所有消息。
"""
import json
import logging
import pathlib
from typing import List, Optional

import chromadb

logger = logging.getLogger(__name__)

# 默认模式库路径（与本文件同目录）
_DEFAULT_PATTERNS_PATH = pathlib.Path(__file__).parent / "injection_patterns.json"
_COLLECTION_NAME = "injection_patterns"


class InjectionDetector:
    """基于 ChromaDB 向量相似度的 Prompt Injection 检测器。

    阈值建议：
      - 0.85：严格的检测，几乎无误报，适合拦截明确的注入
      - 0.80：更敏感，拦截变体更强，但可能误伤正常输入
    默认 0.85，可根据实际效果调整。
    """

    def __init__(
        self,
        threshold: float = 0.85,
        patterns_path: Optional[str] = None,
        chroma_path: str = "./data/chroma",
    ):
        """初始化 InjectionDetector。

        Args:
            threshold: 余弦相似度阈值，超过此值判定为注入
            patterns_path: 注入模式库路径，默认使用自带的 injection_patterns.json
            chroma_path: ChromaDB 持久化路径
        """
        self.threshold = threshold
        self._collection = None

        # 初始化 ChromaDB（本地嵌入式模式）
        try:
            client = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._collection = client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"description": "Prompt Injection 检测模式库"},
            )
            logger.info(f"注入检测 ChromaDB 已初始化: {chroma_path}")
        except Exception as ex:
            logger.warning(f"注入检测 ChromaDB 初始化失败，降级放行: {ex}")

        # 加载模式库
        path = patterns_path or str(_DEFAULT_PATTERNS_PATH)
        patterns = self._load_patterns(path)

        # 模式库非空且 collection 中无数据时写入
        if patterns and self._collection and self._collection.count() == 0:
            self._index_patterns(patterns)

    def _load_patterns(self, path: str) -> List[str]:
        """从 JSON 文件加载注入模式。

        Args:
            path: 模式库 JSON 文件路径

        Returns:
            模式字符串列表
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or not all(isinstance(s, str) for s in data):
                raise ValueError("模式库格式错误，应为字符串数组")
            logger.info(f"注入检测: 已加载 {len(data)} 条模式 ({path})")
            return data
        except Exception as ex:
            logger.warning(f"注入检测: 加载模式库失败 ({path}): {ex}")
            return []

    def _index_patterns(self, patterns: List[str]) -> None:
        """将注入模式写入 ChromaDB。

        Args:
            patterns: 模式字符串列表
        """
        if not self._collection:
            return
        ids = [str(i) for i in range(len(patterns))]
        try:
            self._collection.add(
                ids=ids,
                documents=patterns,
                metadatas=[{"index": i} for i in range(len(patterns))],
            )
            logger.info(f"注入检测: {len(patterns)} 条模式已写入 ChromaDB")
        except Exception as ex:
            logger.warning(f"注入检测: 模式写入失败: {ex}")

    def check(self, message: str) -> bool:
        """检测用户消息是否为注入攻击。

        通过 ChromaDB 查询最相似的注入模式，
        最高相似度超过阈值则判定为注入。

        Args:
            message: 用户输入消息

        Returns:
            True = 检测到注入攻击，应予拦截
            False = 正常消息，继续处理
        """
        if not message or not message.strip():
            return False

        # ChromaDB 不可用或模式库为空 → 放行
        if not self._collection or self._collection.count() == 0:
            return False

        try:
            results = self._collection.query(
                query_texts=[message],
                n_results=1,
            )

            if results["distances"] and results["distances"][0]:
                # ChromaDB 返回的是 L2 距离，转相似度
                max_sim = 1.0 - float(results["distances"][0][0])
                if max_sim >= self.threshold:
                    matched = results["documents"][0][0][:60] if results["documents"] else ""
                    logger.warning(
                        f"注入检测: 拦截! 相似度={max_sim:.4f} "
                        f"匹配={matched!r} "
                        f"消息={message[:80]!r}"
                    )
                    return True

            return False

        except Exception as ex:
            logger.warning(f"注入检测: 查询失败（降级放行）: {ex}")
            return False

    @property
    def is_ready(self) -> bool:
        """检测器是否就绪（ChromaDB collection 已初始化）。"""
        return self._collection is not None

    @property
    def pattern_count(self) -> int:
        """注入模式数量。"""
        if self._collection:
            return self._collection.count()
        return 0
