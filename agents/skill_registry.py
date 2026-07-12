"""
Skill 注册与查找（SkillRegistry）

职责：
  - 从 SkillLoader 加载 .md 定义
  - 管理多版本（灰度发布）
  - 一致性哈希路由（resolve）
  - 热重载接口（reload）

用法：
    # 初始化
    SkillRegistry.init_from_loader(loader)

    # 按 user_id 灰度路由
    desc = SkillRegistry.resolve("WEATHER", user_id="u123")
    if desc:
        result = check_skill(desc.name, slots, emotion, desc.required_slots)

    # 热重载（SkillWatcher 调用）
    SkillRegistry.reload(new_descriptor)
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional

from agents.skill_loader import SkillDescriptor, SkillLoader

logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    Skill 注册表。

    存储结构：
      _skills: {name: [descriptor_v2, descriptor_v1, ...]}
    """

    _skills: Dict[str, List[SkillDescriptor]] = {}
    _lock: Any = None  # asyncio.Lock 实例，外部注入

    # ── 初始化 ──

    @classmethod
    def init_from_loader(cls, loader: SkillLoader) -> None:
        """从 SkillLoader 加载所有 .md 文件，替换当前注册表。"""
        loaded = loader.load_all()
        if not loaded:
            logger.info("SkillLoader 未找到 .md 文件")
            return
        cls._skills = loaded
        count = sum(len(v) for v in loaded.values())
        logger.info(f"Skill 已从 .md 加载: {count} 个定义（{len(loaded)} 个技能）")

    # ── 热重载 ──

    @classmethod
    def reload(cls, descriptor: SkillDescriptor) -> None:
        """热重载单个 Skill 定义（SkillWatcher 调用）。

        替换或追加到 _skills[name]。
        """
        versions = cls._skills.setdefault(descriptor.name, [])
        # 替换同版本号的旧记录
        for i, v in enumerate(versions):
            if v.version == descriptor.version:
                versions[i] = descriptor
                versions.sort(key=lambda x: x.version, reverse=True)
                logger.info(
                    f"🔄 Skill 热重载: {descriptor.name} v{descriptor.version}"
                )
                return
        # 追加新版本
        versions.append(descriptor)
        versions.sort(key=lambda x: x.version, reverse=True)
        logger.info(
            f"🆕 Skill 新增: {descriptor.name} v{descriptor.version} "
            f"(共 {len(versions)} 个版本)"
        )

    @classmethod
    def remove(cls, name: str, version: str) -> bool:
        """移除指定版本（通常用于清理已删除的 .md 文件）。"""
        versions = cls._skills.get(name)
        if not versions:
            return False
        for i, v in enumerate(versions):
            if v.version == version:
                versions.pop(i)
                if not versions:
                    del cls._skills[name]
                logger.info(f"🗑️ Skill 已移除: {name} v{version}")
                return True
        return False

    # ── 查找 ──

    @classmethod
    def get(cls, name: str) -> Optional[SkillDescriptor]:
        """按名称返回最新版本的 Skill。"""
        versions = cls._skills.get(name)
        if versions:
            return versions[0]  # 已降序排列
        return None

    @classmethod
    def resolve(cls, name: str, user_id: str = "") -> Optional[SkillDescriptor]:
        """按 user_id 一致性哈希路由，返回对应的 Skill 版本。

        算法：
          1. 获取该 name 的所有版本（按版本降序）
          2. md5(user_id) % 100 → [0, 100) 桶
          3. 按 rollout 累加，确定命中版本

        如果只有 1 个版本或 user_id 为空，直接返回最新版本。
        """
        versions = cls._skills.get(name)
        if not versions:
            return None

        if len(versions) == 1 or not user_id:
            return versions[0]

        bucket = int(hashlib.md5(user_id.encode()).hexdigest(), 16) % 100

        cumulative = 0
        for v in versions:  # 已降序
            cumulative += v.rollout
            if bucket < cumulative:
                return v

        # 剩余流量走最新版本
        return versions[0]

    @classmethod
    def all(cls) -> Dict[str, SkillDescriptor]:
        """返回所有 Skill 的最新版本。"""
        return {name: versions[0] for name, versions in cls._skills.items()}

    @classmethod
    def names(cls) -> List[str]:
        """返回所有已注册的 Skill 名称列表。"""
        return sorted(cls._skills.keys())

    @classmethod
    def has(cls, name: str) -> bool:
        """检查指定名称的 Skill 是否已注册。"""
        return name in cls._skills

    # ── 并发安全 ──

    @classmethod
    def set_lock(cls, lock: Any) -> None:
        """注入 asyncio.Lock，用于热重载并发安全。"""
        cls._lock = lock
