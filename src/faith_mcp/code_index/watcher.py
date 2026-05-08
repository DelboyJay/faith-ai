"""
Description:
    Provide a small polling watcher for the code index.

Requirements:
    - Re-index changed files and remove deleted files within the configured
      debounce window.
    - Keep the watcher dependency-free so it works in the current repo setup.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from faith_mcp.code_index.index import DEFAULT_EXCLUDED_DIRS, CodeIndex


class FileWatcher:
    """Poll the workspace and refresh the code index when files change."""

    def __init__(
        self,
        workspace_root: Path,
        index: CodeIndex,
        *,
        debounce_ms: int = 200,
    ) -> None:
        """
        Create a polling watcher for one workspace.

        :param workspace_root: Workspace root to watch.
        :param index: Code index to refresh.
        :param debounce_ms: Polling interval in milliseconds.
        """
        self.workspace_root = Path(workspace_root).resolve()
        self.index = index
        self.debounce_ms = debounce_ms
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._known_mtimes: dict[str, int] = {}

    async def start(self) -> None:
        """
        Start the polling loop.

        Requirements:
            - Perform an initial scan before entering the loop.
        """
        if self._running:
            return
        self._running = True
        await self.scan_once()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """
        Stop the polling loop.

        Requirements:
            - Cancel the background task cleanly when the watcher stops.
        """
        self._running = False
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def scan_once(self) -> None:
        """
        Refresh the index from the current filesystem state.

        Requirements:
            - Index new and modified files.
            - Remove files that disappeared from the workspace.
        """
        current_mtimes: dict[str, int] = {}
        for file_path in self._iter_files():
            try:
                mtime = file_path.stat().st_mtime_ns
            except OSError:
                continue
            relative_path = file_path.relative_to(self.workspace_root).as_posix()
            current_mtimes[relative_path] = mtime
            if self._known_mtimes.get(relative_path) != mtime:
                self.index.index_file(file_path)

        for relative_path in set(self._known_mtimes) - set(current_mtimes):
            self.index.remove_file(self.workspace_root / relative_path)

        self._known_mtimes = current_mtimes

    async def _run(self) -> None:
        """Run the polling loop until stopped."""
        try:
            while self._running:
                await asyncio.sleep(self.debounce_ms / 1000)
                await self.scan_once()
        except asyncio.CancelledError:
            raise

    def _iter_files(self):
        """Yield source files that should be watched."""
        for path in sorted(self.workspace_root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in DEFAULT_EXCLUDED_DIRS for part in path.parts):
                continue
            yield path
