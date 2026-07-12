"""
SkillWatcher — 监听 skills/ 目录文件变更，触发热重载。

实现方式：定时轮询文件 checksum。
不引入 watchdog 依赖，避免增加部署复杂度。

用法：
    watcher = SkillWatcher(loader, registry)
    asyncio.create_task(watcher.start())  # 后台协程
"""

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Dict, Optional

from agents.skill_loader import SkillLoader
from agents.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)


class SkillWatcher:
    """监听 skills/ 目录变化，触发热重载。"""

    CHECK_INTERVAL = 10  # 轮询间隔（秒）

    def __init__(
        self,
        loader: SkillLoader,
        registry: SkillRegistry,
        interval: Optional[int] = None,
    ):
        self.loader = loader
        self.registry = registry
        self.interval = interval or self.CHECK_INTERVAL
        self._checksums: Dict[str, str] = {}
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """启动后台轮询（永不返回，直到调用 stop()）。"""
        logger.info(
            f"SkillWatcher 已启动，轮询间隔 {self.interval}s"
        )

        # 先扫描一次，建立初始 checksum 表
        self._scan_all()

        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self.interval)
                self._scan_all()
            except asyncio.CancelledError:
                logger.info("SkillWatcher 已取消")
                break
            except Exception as exc:
                logger.error(f"SkillWatcher 轮询异常: {exc}")

    def stop(self) -> None:
        """停止轮询。"""
        self._stop_event.set()
        logger.info("SkillWatcher 已停止")

    def _scan_all(self) -> None:
        """扫描目录，发现变更则触发热重载。"""
        md5_hex = hashlib.md5  # 局部引用，略快

        for fpath in self.loader.skills_dir.glob("*.md"):
            try:
                new_cs = md5_hex(fpath.read_bytes()).hexdigest()
                old_cs = self._checksums.get(fpath.name)

                if old_cs is None:
                    # 新文件
                    self._checksums[fpath.name] = new_cs
                    descriptor = self.loader.parse_file(fpath)
                    self.registry.reload(descriptor)
                elif old_cs != new_cs:
                    # 文件已变更
                    self._checksums[fpath.name] = new_cs
                    descriptor = self.loader.parse_file(fpath)
                    self.registry.reload(descriptor)
            except Exception as exc:
                logger.error(
                    f"SkillWatcher 处理文件失败 [{fpath.name}]: {exc}"
                )

        # 检查已删除的文件
        current_files = {f.name for f in self.loader.skills_dir.glob("*.md")}
        for fname in list(self._checksums.keys()):
            if fname not in current_files:
                logger.info(f"Skill 文件已删除: {fname}")
                del self._checksums[fname]
                # 注意：不自动从注册表删除，避免灰度切换时误删
                # 如有需要，可手动调用 registry.remove(name, version)
