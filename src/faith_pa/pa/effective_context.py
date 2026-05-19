"""Description:
    Build, cache, and persist the Project Agent effective instruction context.

Requirements:
    - Treat project-root ``AGENTS.md`` as the PA project-instruction source.
    - Resolve explicit and inferred markdown includes deterministically.
    - Persist redacted effective-context snapshots only when the compiled context changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from faith_pa.utils.tokens import count_text_tokens

DEFAULT_MAX_INCLUDE_DEPTH = 4
DEFAULT_MAX_INCLUDE_FILES = 24
DEFAULT_REDACTION_TOKEN = "[REDACTED]"
_EXPLICIT_INCLUDE_PATTERN = re.compile(r"^\s*!include\s+([^\s#]+)\s*$", re.MULTILINE)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)", re.IGNORECASE)
_PLAIN_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.md)\b",
    re.IGNORECASE,
)
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)([A-Za-z0-9._-]+)"),
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b\s*[:=]\s*([^\s]+)"),
)


@dataclass(frozen=True, slots=True)
class IncludeEntry:
    """Description:
        Describe one resolved markdown include that contributes to PA instructions.

    Requirements:
        - Preserve the relative path, resolution kind, and estimated token contribution.

    :param relative_path: Project-relative include path.
    :param source_kind: Include source such as ``explicit`` or ``inferred``.
    :param token_estimate: Estimated token count for the included file content.
    :param content: Raw included file content.
    """

    relative_path: str
    source_kind: str
    token_estimate: int
    content: str


@dataclass(frozen=True, slots=True)
class EffectiveContextSnapshot:
    """Description:
        Represent one compiled PA effective-context snapshot.

    Requirements:
        - Preserve the compiled and redacted text, include graph, warnings, hash, and persisted path.

    :param session_id: Session identifier associated with the compiled context.
    :param turn_id: Turn identifier associated with the compiled context.
    :param project_instruction_path: Source ``AGENTS.md`` path.
    :param include_entries: Resolved include entries used in the compiled context.
    :param warnings: Non-fatal include-resolution warnings.
    :param compiled_context: Full compiled system-context text used for the PA turn.
    :param redacted_context: Persisted redacted effective-context text.
    :param context_hash: Stable hash of the compiled context.
    :param snapshot_path: Persisted snapshot JSON path.
    """

    session_id: str
    turn_id: str
    project_instruction_path: Path
    include_entries: tuple[IncludeEntry, ...]
    warnings: tuple[str, ...]
    compiled_context: str
    redacted_context: str
    context_hash: str
    snapshot_path: Path


class ProjectAgentContextCompiler:
    """Description:
        Compile the final Project Agent system-context text from stable and runtime layers.

    Requirements:
        - Resolve ``AGENTS.md`` plus validated markdown includes.
        - Cache the stable project-instruction portion by content hash.
        - Persist one redacted snapshot per distinct compiled-context hash.

    :param project_root: Active project root containing ``AGENTS.md``.
    :param model_name: Model name used for token estimation.
    :param snapshot_root: Root directory used for persisted effective-context snapshots.
    :param max_include_depth: Maximum recursive include depth.
    :param max_include_files: Maximum number of resolved include files.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        model_name: str,
        snapshot_root: Path | None = None,
        max_include_depth: int = DEFAULT_MAX_INCLUDE_DEPTH,
        max_include_files: int = DEFAULT_MAX_INCLUDE_FILES,
    ) -> None:
        """Description:
            Initialise the PA effective-context compiler.

        Requirements:
            - Resolve the project root and snapshot root eagerly.

        :param project_root: Active project root containing ``AGENTS.md``.
        :param model_name: Model name used for token estimation.
        :param snapshot_root: Root directory used for persisted effective-context snapshots.
        :param max_include_depth: Maximum recursive include depth.
        :param max_include_files: Maximum number of resolved include files.
        """

        self.project_root = Path(project_root).resolve()
        self.model_name = model_name
        self.snapshot_root = Path(snapshot_root).resolve() if snapshot_root else self.project_root
        self.max_include_depth = max_include_depth
        self.max_include_files = max_include_files
        self.project_instruction_path = self.project_root / "AGENTS.md"
        self._stable_hash: str | None = None
        self._stable_block: str = ""
        self._cached_includes: tuple[IncludeEntry, ...] = ()
        self._cached_warnings: tuple[str, ...] = ()

    def compose_context_text(
        self,
        *,
        core_instructions: str,
        runtime_user_block: str,
        runtime_time_block: str,
        tool_manifest_block: str,
    ) -> str:
        """Description:
            Compose the effective PA context text without persisting a snapshot.

        Requirements:
            - Reuse the stable cached project-instruction block when possible.
            - Support preview usage for context-size estimation before compaction.

        :param core_instructions: Protected FAITH core instructions.
        :param runtime_user_block: Runtime user-context block.
        :param runtime_time_block: Runtime time-context block.
        :param tool_manifest_block: Runtime MCP tool-manifest block.
        :returns: Compiled PA context text without persistence side effects.
        """

        self._refresh_stable_block_if_needed()
        compiled_parts = [
            core_instructions.strip(),
            self._stable_block.strip(),
            runtime_user_block.strip(),
            runtime_time_block.strip(),
            tool_manifest_block.strip(),
        ]
        return "\n\n".join(part for part in compiled_parts if part)

    def read_project_instructions(self) -> str:
        """Description:
            Return the raw project instruction text from ``AGENTS.md``.

        Requirements:
            - Treat a missing project instruction file as empty content.

        :returns: Raw project instruction text.
        """

        if not self.project_instruction_path.exists():
            return ""
        return self.project_instruction_path.read_text(encoding="utf-8")

    def compile_for_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        core_instructions: str,
        runtime_user_block: str,
        runtime_time_block: str,
        tool_manifest_block: str,
    ) -> EffectiveContextSnapshot:
        """Description:
            Compile and persist the effective PA context for one turn.

        Requirements:
            - Reuse the cached stable project block when the underlying files have not changed.
            - Persist one redacted snapshot keyed by the compiled context hash.

        :param session_id: Session identifier associated with the compiled context.
        :param turn_id: Turn identifier associated with the compiled context.
        :param core_instructions: Protected FAITH core instructions.
        :param runtime_user_block: Runtime user-context block.
        :param runtime_time_block: Runtime time-context block.
        :param tool_manifest_block: Runtime MCP tool-manifest block.
        :returns: Compiled effective-context snapshot.
        """

        self._refresh_stable_block_if_needed()
        compiled_context = self.compose_context_text(
            core_instructions=core_instructions,
            runtime_user_block=runtime_user_block,
            runtime_time_block=runtime_time_block,
            tool_manifest_block=tool_manifest_block,
        )
        context_hash = hashlib.sha256(compiled_context.encode("utf-8")).hexdigest()
        snapshot_path = self._persist_snapshot(
            session_id=session_id,
            turn_id=turn_id,
            context_hash=context_hash,
            compiled_context=compiled_context,
            include_entries=self._cached_includes,
            warnings=self._cached_warnings,
        )
        return EffectiveContextSnapshot(
            session_id=session_id,
            turn_id=turn_id,
            project_instruction_path=self.project_instruction_path,
            include_entries=self._cached_includes,
            warnings=self._cached_warnings,
            compiled_context=compiled_context,
            redacted_context=self._redact_text(compiled_context),
            context_hash=context_hash,
            snapshot_path=snapshot_path,
        )

    def describe_context_files(self) -> list[dict[str, int | str]]:
        """Description:
            Return per-file token estimates for the current AGENTS.md instruction graph.

        Requirements:
            - Include the raw project-root ``AGENTS.md`` entry first.
            - Reuse cached include metadata when available.

        :returns: Ordered context-file token estimate entries.
        """

        self._refresh_stable_block_if_needed()
        project_text = self.read_project_instructions()
        entries = [
            {
                "path": "AGENTS.md",
                "tokens": count_text_tokens(project_text, self.model_name)
                if project_text.strip()
                else 0,
            }
        ]
        entries.extend(
            {"path": entry.relative_path, "tokens": entry.token_estimate}
            for entry in self._cached_includes
        )
        return entries

    def _refresh_stable_block_if_needed(self) -> None:
        """Description:
            Refresh the cached stable project-instruction block when the underlying files change.

        Requirements:
            - Recompute the include graph and stable block only when the stable hash changes.
        """

        project_text = self.read_project_instructions()
        stable_hash_input = self._build_stable_hash_input(project_text)
        stable_hash = hashlib.sha256(stable_hash_input.encode("utf-8")).hexdigest()
        if self._stable_hash == stable_hash:
            return
        include_entries, warnings = self._resolve_include_entries(project_text)
        self._cached_includes = include_entries
        self._cached_warnings = warnings
        self._stable_block = self._build_project_instruction_block(
            project_text=project_text,
            include_entries=include_entries,
        )
        self._stable_hash = stable_hash

    def _build_stable_hash_input(self, project_text: str) -> str:
        """Description:
            Build one stable hash input covering ``AGENTS.md`` and include file contents.

        Requirements:
            - Recompute the hash when the project instruction file or any included file changes.

        :param project_text: Raw ``AGENTS.md`` content.
        :returns: Stable hash input text.
        """

        include_hash_parts = [project_text]
        include_entries, _warnings = self._resolve_include_entries(project_text)
        for entry in include_entries:
            include_hash_parts.append(f"{entry.relative_path}\n{entry.content}")
        return "\n\n".join(include_hash_parts)

    def _resolve_include_entries(
        self, project_text: str
    ) -> tuple[tuple[IncludeEntry, ...], tuple[str, ...]]:
        """Description:
            Resolve explicit and inferred markdown include entries for ``AGENTS.md``.

        Requirements:
            - Keep include order deterministic.
            - Enforce workspace, recursion-depth, and include-count safeguards.

        :param project_text: Raw ``AGENTS.md`` content.
        :returns: Resolved include entries and non-fatal warnings.
        """

        warnings: list[str] = []
        include_entries: list[IncludeEntry] = []
        visited_paths: set[Path] = set()

        def resolve_children(owner_path: Path, owner_text: str, depth: int) -> None:
            if depth > self.max_include_depth:
                warnings.append(
                    f"Include depth exceeded the limit of {self.max_include_depth} at {owner_path.relative_to(self.project_root).as_posix()}."
                )
                return
            for candidate in self._enumerate_candidate_include_paths(
                project_text=owner_text,
                owner_path=owner_path,
            ):
                if len(include_entries) >= self.max_include_files:
                    warnings.append(
                        f"Include count exceeded the limit of {self.max_include_files}; remaining includes were skipped."
                    )
                    return
                resolved_path = self._resolve_workspace_path(
                    candidate=candidate,
                    owner_path=owner_path,
                )
                if resolved_path is None:
                    warnings.append(f"Skipped include outside the project workspace: {candidate}")
                    continue
                if not resolved_path.exists():
                    warnings.append(
                        f"Skipped missing include target: {resolved_path.relative_to(self.project_root).as_posix()}"
                    )
                    continue
                if resolved_path in visited_paths:
                    warnings.append(
                        f"Skipped recursive include loop involving {resolved_path.relative_to(self.project_root).as_posix()}."
                    )
                    continue
                visited_paths.add(resolved_path)
                content = resolved_path.read_text(encoding="utf-8")
                relative_path = resolved_path.relative_to(self.project_root).as_posix()
                source_kind = (
                    "explicit"
                    if candidate
                    in {
                        match.group(1).strip()
                        for match in _EXPLICIT_INCLUDE_PATTERN.finditer(owner_text)
                    }
                    else "inferred"
                )
                include_entries.append(
                    IncludeEntry(
                        relative_path=relative_path,
                        source_kind=source_kind,
                        token_estimate=count_text_tokens(content, self.model_name),
                        content=content,
                    )
                )
                resolve_children(resolved_path, content, depth + 1)

        resolve_children(self.project_instruction_path, project_text, 1)
        return tuple(include_entries), tuple(warnings)

    def _enumerate_candidate_include_paths(
        self,
        *,
        project_text: str,
        owner_path: Path,
    ) -> tuple[str, ...]:
        """Description:
            Enumerate deterministic explicit and inferred markdown include candidates.

        Requirements:
            - Preserve first-seen ordering and remove duplicates.
            - Ignore self-references to the owner file.

        :param project_text: Raw markdown content to inspect.
        :param owner_path: Markdown file currently being scanned.
        :returns: Ordered unique include-path candidates.
        """

        candidates: list[str] = []

        def append_candidate(candidate: str) -> None:
            normalised_candidate = candidate.strip()
            if not normalised_candidate:
                return
            owner_name = owner_path.name.casefold()
            if normalised_candidate.casefold() == owner_name:
                return
            if normalised_candidate not in candidates:
                candidates.append(normalised_candidate)

        for match in _EXPLICIT_INCLUDE_PATTERN.finditer(project_text):
            append_candidate(match.group(1))
        for match in _MARKDOWN_LINK_PATTERN.finditer(project_text):
            append_candidate(match.group(1))
        for match in _PLAIN_REFERENCE_PATTERN.finditer(project_text):
            append_candidate(match.group(1))
        return tuple(candidates)

    def _resolve_workspace_path(self, *, candidate: str, owner_path: Path) -> Path | None:
        """Description:
            Resolve one include candidate to a safe in-workspace file path.

        Requirements:
            - Support paths relative to the current owner file.
            - Reject paths that escape the project workspace.

        :param candidate: Candidate include path.
        :param owner_path: Markdown file currently being scanned.
        :returns: Resolved workspace path when safe, otherwise ``None``.
        """

        owner_dir = owner_path.parent
        resolved_path = (owner_dir / candidate).resolve()
        if self._is_within_workspace(resolved_path):
            return resolved_path
        resolved_path = (self.project_root / candidate).resolve()
        if self._is_within_workspace(resolved_path):
            return resolved_path
        return None

    def _is_within_workspace(self, candidate_path: Path) -> bool:
        """Description:
            Return whether one resolved path stays inside the active project workspace.

        Requirements:
            - Reject traversal outside the project root.

        :param candidate_path: Resolved path to check.
        :returns: ``True`` when the path is inside the project root.
        """

        try:
            candidate_path.relative_to(self.project_root)
        except ValueError:
            return False
        return True

    def _build_project_instruction_block(
        self,
        *,
        project_text: str,
        include_entries: tuple[IncludeEntry, ...],
    ) -> str:
        """Description:
            Build the stable project-instruction block for the PA system context.

        Requirements:
            - Include the raw ``AGENTS.md`` text first.
            - Append included markdown content in deterministic order.

        :param project_text: Raw ``AGENTS.md`` content.
        :param include_entries: Resolved include entries.
        :returns: Stable project-instruction block text.
        """

        parts: list[str] = []
        if project_text.strip():
            parts.append(f"[Project Instructions]\n{project_text.strip()}")
        for entry in include_entries:
            parts.append(
                f"[Included Project Instruction: {entry.relative_path}]\n{entry.content.strip()}"
            )
        return "\n\n".join(part for part in parts if part)

    def _persist_snapshot(
        self,
        *,
        session_id: str,
        turn_id: str,
        context_hash: str,
        compiled_context: str,
        include_entries: tuple[IncludeEntry, ...],
        warnings: tuple[str, ...],
    ) -> Path:
        """Description:
            Persist one redacted effective-context snapshot for later inspection.

        Requirements:
            - Persist snapshots under the host-backed session store.
            - Update the turn-reference list when a snapshot hash is reused.

        :param session_id: Session identifier associated with the snapshot.
        :param turn_id: Turn identifier associated with the snapshot.
        :param context_hash: Stable compiled-context hash.
        :param compiled_context: Full compiled context text.
        :param include_entries: Resolved include entries.
        :param warnings: Non-fatal include-resolution warnings.
        :returns: Persisted snapshot file path.
        """

        snapshot_dir = self.snapshot_root / ".faith" / "sessions" / session_id / "effective-context"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = snapshot_dir / f"{context_hash}.json"
        redacted_context = self._redact_text(compiled_context)
        payload = {
            "session_id": session_id,
            "turn_id": turn_id,
            "turn_ids": [turn_id],
            "context_hash": context_hash,
            "project_instruction_path": self.project_instruction_path.as_posix(),
            "include_entries": [
                {
                    "relative_path": entry.relative_path,
                    "source_kind": entry.source_kind,
                    "token_estimate": entry.token_estimate,
                }
                for entry in include_entries
            ],
            "warnings": list(warnings),
            "redacted_context": redacted_context,
        }
        if snapshot_path.exists():
            existing_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            turn_ids = list(existing_payload.get("turn_ids", []))
            if turn_id not in turn_ids:
                turn_ids.append(turn_id)
            existing_payload["turn_id"] = turn_id
            existing_payload["turn_ids"] = turn_ids
            snapshot_path.write_text(
                json.dumps(existing_payload, indent=2),
                encoding="utf-8",
            )
            return snapshot_path
        snapshot_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        return snapshot_path

    def _redact_text(self, text: str) -> str:
        """Description:
            Apply deterministic best-effort secret redaction to compiled context text.

        Requirements:
            - Redact common token-like and secret-like patterns before persistence.

        :param text: Compiled context text to redact.
        :returns: Redacted text safe for debug persistence.
        """

        redacted = text
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(DEFAULT_REDACTION_TOKEN, redacted)
        return redacted


__all__ = [
    "EffectiveContextSnapshot",
    "IncludeEntry",
    "ProjectAgentContextCompiler",
]
