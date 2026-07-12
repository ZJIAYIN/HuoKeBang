"""
SkillLoader — 扫描 skills/ 目录下的 .md 文件，解析为 SkillDescriptor。

职责：
  1. 扫描目录获取所有 .md 文件
  2. 解析 YAML frontmatter
  3. 提取 instruction 正文
  4. 构建 SkillDescriptor 对象

用法：
    loader = SkillLoader()
    all_skills = loader.load_all()  # {"WEATHER": [Descriptor, ...]}
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Tool(str, Enum):
    """Orchestrator 统一调度的工具类型。"""
    RAG = "rag"
    CRM = "crm"
    CALCULATOR = "calc"
    WEATHER = "weather"


@dataclass
class SkillDescriptor:
    """由 .md 文件解析而来的内存 Skill 表示。

    只携带数据，不包含校验逻辑（校验逻辑统一在 skill_checks.py 中）。
    """

    name: str                         # 技能标识，与 Planner sub_tasks 匹配
    version: str                      # 语义版本号
    instruction: str                  # 正文（给 Response Agent 的提示词）
    status: str = "active"            # active | deprecated
    rollout: int = 100                # 灰度流量占比 0-100
    required_slots: List[str] = field(default_factory=list)
    optional_slots: List[str] = field(default_factory=list)
    required_tools: List[Tool] = field(default_factory=list)
    auto_evaluate: bool = False
    file_path: str = ""               # 来源文件路径
    checksum: str = ""                # 文件 MD5，用于变更检测


class SkillLoader:
    """扫描并解析 skills/ 目录的 .md 文件。"""

    # 默认 skills 目录（与 loader.py 同级的 skills/ 文件夹）
    SKILLS_DIR = Path(__file__).parent / "skills"

    # frontmatter 正则：以 --- 开头的 YAML 区块
    _FM_PATTERN = re.compile(r"^---\s*\n(.*?\n)---\s*\n(.*)", re.DOTALL)

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or self.SKILLS_DIR
        if not self.skills_dir.exists():
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Skill 目录不存在，已创建: {self.skills_dir}")

    # ── 公开方法 ──

    def load_all(self) -> Dict[str, List[SkillDescriptor]]:
        """扫描所有 .md 文件，返回 {name: [version1, version2, ...]}。

        每个 name 下的版本按 version 降序排列（高版本在前）。
        """
        result: Dict[str, List[SkillDescriptor]] = {}

        for fpath in sorted(self.skills_dir.glob("*.md")):
            try:
                sd = self.parse_file(fpath)
                result.setdefault(sd.name, []).append(sd)
                logger.debug(f"Loaded skill: {sd.name} v{sd.version} ← {fpath.name}")
            except Exception as exc:
                logger.error(f"解析 Skill 文件失败 [{fpath.name}]: {exc}")

        # 每个 name 内按版本降序
        for name in result:
            result[name].sort(key=lambda x: x.version, reverse=True)

        return result

    def parse_file(self, fpath: Path) -> SkillDescriptor:
        """解析单个 .md 文件 → SkillDescriptor。"""
        raw = fpath.read_text(encoding="utf-8")
        checksum = hashlib.md5(raw.encode()).hexdigest()
        frontmatter, body = self._split_frontmatter(raw)
        meta = self._parse_yaml(frontmatter)
        return self._build_descriptor(meta, body, fpath, checksum)

    # ── 内部方法 ──

    @staticmethod
    def _split_frontmatter(raw: str) -> tuple[str, str]:
        """分离 frontmatter（--- 包裹的 YAML）和正文。

        返回 (frontmatter_text, body_text)。
        - 没有 frontmatter：整个文件视为 instruction
        """
        m = SkillLoader._FM_PATTERN.match(raw)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return "", raw.strip()

    @staticmethod
    def _parse_yaml(text: str) -> Dict[str, Any]:
        """解析 YAML 文本，空文本返回空 dict。"""
        if not text.strip():
            return {}
        try:
            import yaml
            data = yaml.safe_load(text) or {}
            if not isinstance(data, dict):
                raise ValueError("YAML 结构不是 dict")
            return data
        except ImportError as exc:
            raise RuntimeError("需要 PyYAML 库：pip install pyyaml") from exc
        except Exception as exc:
            raise ValueError(f"YAML 解析失败: {exc}") from exc

    def _build_descriptor(
        self,
        meta: Dict[str, Any],
        body: str,
        fpath: Path,
        checksum: str,
    ) -> SkillDescriptor:
        """从 YAML meta + body 构建 SkillDescriptor。"""
        name = (meta.get("name") or "").strip()
        if not name:
            raise ValueError(f"缺少 name 字段: {fpath.name}")

        version = str(meta.get("version", "1.0.0"))

        # 工具列表：字符串 → Tool 枚举
        tools_raw: list = meta.get("required_tools") or []
        tools = []
        for t in tools_raw:
            try:
                tools.append(Tool(t))
            except ValueError:
                logger.warning(f"[{fpath.name}] 未知工具 '{t}'，已跳过")

        return SkillDescriptor(
            name=name,
            version=version,
            instruction=body,
            status=meta.get("status", "active"),
            rollout=int(meta.get("rollout", 100)),
            required_slots=list(meta.get("required_slots") or []),
            optional_slots=list(meta.get("optional_slots") or []),
            required_tools=tools,
            auto_evaluate=bool(meta.get("auto_evaluate", False)),
            file_path=str(fpath),
            checksum=checksum,
        )
