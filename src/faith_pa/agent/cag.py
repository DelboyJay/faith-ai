"""Description:
    Provide the dedicated Cache-Augmented Generation document manager for FAITH agents.

Requirements:
    - Load configured CAG documents from disk and track their metadata.
    - Validate combined token usage against the configured agent budget.
    - Support targeted reloads when watched CAG files change.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from faith_pa.utils.tokens import count_text_tokens


@dataclass(slots=True)
class CAGDocument:
    """Description:
        Represent one loaded CAG reference document and its metadata.

    Requirements:
        - Preserve the configured source path, loaded content, token count, and content hash.
        - Preserve load failures without raising so session validation can report them cleanly.

    :param path: Resolved absolute source path.
    :param relative_path: Configured relative or absolute path string.
    :param content: Loaded document content.
    :param token_count: Estimated token count for the formatted document block.
    :param sha256: SHA-256 hash of the loaded content.
    :param loaded: Whether the document loaded successfully.
    :param error: Human-readable load error when loading failed.
    """

    path: Path
    relative_path: str
    content: str = ""
    token_count: int = 0
    sha256: str = ""
    loaded: bool = False
    error: str = ""

    def format_for_context(self) -> str:
        """Description:
            Format one loaded CAG document for prompt inclusion.

        Requirements:
            - Prefix the content with a stable source-path header.
            - Return an empty string for unloaded documents.

        :returns: Formatted prompt block for the document.
        """

        if not self.loaded:
            return ""
        return f"--- CAG Reference: {self.relative_path} ---\n{self.content.strip()}"


@dataclass(slots=True)
class CAGValidationResult:
    """Description:
        Represent the validation outcome for one agent's configured CAG documents.

    Requirements:
        - Preserve aggregate counts, token usage, and any validation errors or warnings.
        - Provide a compact text summary suitable for PA session-start reporting.

    :param success: Whether the CAG load succeeded within budget and without missing files.
    :param total_tokens: Combined token count of loaded documents.
    :param max_tokens: Configured CAG token budget.
    :param document_count: Number of configured documents.
    :param loaded_count: Number of successfully loaded documents.
    :param errors: Human-readable validation errors.
    :param warnings: Human-readable validation warnings.
    """

    success: bool
    total_tokens: int
    max_tokens: int
    document_count: int
    loaded_count: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Description:
            Build a human-readable summary of the validation result.

        Requirements:
            - Include the loaded-document count and token budget usage.
            - Append each error and warning on its own line.

        :returns: Multi-line validation summary.
        """

        lines = [
            (
                f"CAG: {self.loaded_count}/{self.document_count} documents loaded, "
                f"{self.total_tokens}/{self.max_tokens} tokens used."
            )
        ]
        lines.extend(f"ERROR: {error}" for error in self.errors)
        lines.extend(f"WARNING: {warning}" for warning in self.warnings)
        return "\n".join(lines)


class CAGManager:
    """Description:
        Manage one agent's configured CAG documents for prompt assembly and reloads.

    Requirements:
        - Resolve configured paths against the active project root.
        - Enforce the configured total CAG token budget at validation time.
        - Reload only matching documents when a watched file changes.

    :param project_root: Project root used to resolve relative document paths.
    :param model_name: Model name used for token estimation.
    :param document_paths: Configured CAG document path strings.
    :param max_tokens: Total CAG token budget for the agent.
    """

    def __init__(
        self,
        *,
        project_root: Path | None,
        model_name: str,
        document_paths: list[str],
        max_tokens: int,
    ) -> None:
        """Description:
            Initialise the CAG manager for one agent.

        Requirements:
            - Preserve the configured project root, model name, document list, and budget.

        :param project_root: Project root used to resolve relative document paths.
        :param model_name: Model name used for token estimation.
        :param document_paths: Configured CAG document path strings.
        :param max_tokens: Total CAG token budget for the agent.
        """

        self.project_root = project_root.resolve() if project_root is not None else None
        self.model_name = model_name
        self.document_paths = list(document_paths)
        self.max_tokens = max_tokens
        self.documents: list[CAGDocument] = []

    def _resolve_path(self, path_str: str) -> Path:
        """Description:
            Resolve one configured CAG path to an absolute filesystem path.

        Requirements:
            - Use the configured project root for relative paths when available.
            - Leave absolute paths unchanged.

        :param path_str: Configured document path string.
        :returns: Resolved absolute source path.
        """

        path = Path(path_str)
        if path.is_absolute() or self.project_root is None:
            return path.resolve()
        return (self.project_root / path).resolve()

    @staticmethod
    def _hash_content(content: str) -> str:
        """Description:
            Compute a stable content hash for one loaded document.

        Requirements:
            - Use SHA-256 for deterministic change detection.

        :param content: Document content to hash.
        :returns: Hex-encoded SHA-256 digest.
        """

        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _load_single(self, relative_path: str) -> CAGDocument:
        """Description:
            Load one configured CAG document from disk.

        Requirements:
            - Return a structured failure result instead of raising on missing files or read errors.
            - Count tokens for the formatted prompt block, not just the raw file content.

        :param relative_path: Configured document path string.
        :returns: Loaded or failed CAG document record.
        """

        path = self._resolve_path(relative_path)
        document = CAGDocument(path=path, relative_path=relative_path)
        if not path.exists() or not path.is_file():
            document.error = f"File not found: {path}"
            return document
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            document.error = f"Read error: {exc}"
            return document

        formatted = f"--- CAG Reference: {relative_path} ---\n{content.strip()}"
        document.content = content
        document.token_count = count_text_tokens(formatted, self.model_name)
        document.sha256 = self._hash_content(content)
        document.loaded = True
        return document

    def load_all(self) -> CAGValidationResult:
        """Description:
            Load all configured CAG documents and validate the total token budget.

        Requirements:
            - Preserve per-document failures while still loading other configured paths.
            - Report an over-budget warning that points at the largest loaded document.

        :returns: Aggregate validation result for the configured documents.
        """

        self.documents = [self._load_single(path_str) for path_str in self.document_paths]
        errors = [doc.error for doc in self.documents if not doc.loaded and doc.error]
        total_tokens = sum(doc.token_count for doc in self.documents if doc.loaded)
        warnings: list[str] = []

        if total_tokens > self.max_tokens:
            largest = max(
                (doc for doc in self.documents if doc.loaded),
                key=lambda item: item.token_count,
                default=None,
            )
            if largest is None:
                warnings.append(
                    f"CAG token budget exceeded: {total_tokens}/{self.max_tokens} tokens."
                )
            else:
                warnings.append(
                    f"CAG token budget exceeded: {total_tokens}/{self.max_tokens} tokens. "
                    f"Consider moving '{largest.relative_path}' ({largest.token_count} tokens) to RAG."
                )

        return CAGValidationResult(
            success=not errors and total_tokens <= self.max_tokens,
            total_tokens=total_tokens,
            max_tokens=self.max_tokens,
            document_count=len(self.document_paths),
            loaded_count=sum(1 for doc in self.documents if doc.loaded),
            errors=errors,
            warnings=warnings,
        )

    def reload_document(self, changed_path: str | Path) -> CAGDocument | None:
        """Description:
            Reload one configured CAG document after a file-change notification.

        Requirements:
            - Update the in-memory document entry in place when the path matches.
            - Ignore unrelated paths cleanly by returning ``None``.

        :param changed_path: Changed file path to match against the configured CAG set.
        :returns: Updated document when a configured path matched, otherwise ``None``.
        """

        resolved = Path(changed_path).resolve()
        for index, document in enumerate(self.documents):
            if document.path == resolved:
                updated = self._load_single(document.relative_path)
                self.documents[index] = updated
                return updated
        return None

    def is_cag_path(self, path: str | Path) -> bool:
        """Description:
            Determine whether a path belongs to the configured CAG document set.

        Requirements:
            - Match on resolved absolute paths.

        :param path: Candidate path to inspect.
        :returns: ``True`` when the path belongs to the configured CAG set.
        """

        resolved = Path(path).resolve()
        return any(document.path == resolved for document in self.documents)

    def get_absolute_paths(self) -> list[Path]:
        """Description:
            Return the resolved absolute paths for the configured CAG documents.

        Requirements:
            - Return the currently resolved paths even when a document failed to load.

        :returns: Resolved document paths.
        """

        if self.documents:
            return [document.path for document in self.documents]
        return [self._resolve_path(path_str) for path_str in self.document_paths]

    @property
    def total_tokens(self) -> int:
        """Description:
            Return the combined token count for all successfully loaded documents.

        Requirements:
            - Ignore failed document loads when summing tokens.

        :returns: Combined token count.
        """

        return sum(document.token_count for document in self.documents if document.loaded)

    @property
    def loaded_contents(self) -> list[str]:
        """Description:
            Return the raw content for all successfully loaded documents.

        Requirements:
            - Preserve document order from the original configuration.

        :returns: Loaded document contents.
        """

        return [document.content for document in self.documents if document.loaded]

    def format_for_context(self) -> str:
        """Description:
            Format all loaded CAG documents into one prompt block.

        Requirements:
            - Include stable per-document source headers.
            - Return an empty string when no documents are loaded.

        :returns: Formatted CAG prompt block.
        """

        sections = [
            document.format_for_context() for document in self.documents if document.loaded
        ]
        return "\n\n".join(section for section in sections if section)
