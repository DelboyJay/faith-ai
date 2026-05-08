"""Description:
    Discover and prepare project-root ``cag/`` documents for automatic loading.

Requirements:
    - Discover supported markdown/text files beneath the project-root ``cag/`` directory.
    - Return discovered files in a deterministic order.
    - Merge configured and discovered CAG paths without rewriting user files.
    - Format actionable guidance when a discovered corpus exceeds budget.
"""

from __future__ import annotations

from pathlib import Path

SUPPORTED_CAG_SUFFIXES = {".md", ".markdown", ".txt"}


def _resolve_project_root(project_root: Path | None) -> Path | None:
    """Description:
        Resolve one project root for project ``cag/`` discovery.

    Requirements:
        - Return ``None`` when no project root is available.
        - Normalise the project root before deriving paths from it.

    :param project_root: Project root to resolve.
    :returns: Resolved project root or ``None``.
    """

    if project_root is None:
        return None
    return Path(project_root).resolve()


def _resolve_document_path(path_str: str, project_root: Path | None) -> Path:
    """Description:
        Resolve one CAG path for deduplication and discovery merging.

    Requirements:
        - Use the project root for relative paths when available.
        - Preserve absolute paths unchanged after normalisation.

    :param path_str: Configured or discovered document path.
    :param project_root: Project root used for relative path resolution.
    :returns: Resolved absolute path.
    """

    path = Path(path_str)
    if path.is_absolute() or project_root is None:
        return path.resolve()
    return (_resolve_project_root(project_root) / path).resolve()


def discover_project_cag_documents(project_root: Path | None) -> list[Path]:
    """Description:
        Discover supported project-root ``cag/`` documents.

    Requirements:
        - Return an empty list when the project root or ``cag/`` directory is unavailable.
        - Discover only supported markdown/text files.
        - Return files in deterministic path order.

    :param project_root: Project root containing the ``cag/`` directory.
    :returns: Sorted absolute file paths for supported documents.
    """

    resolved_root = _resolve_project_root(project_root)
    if resolved_root is None:
        return []
    cag_root = resolved_root / "cag"
    if not cag_root.exists():
        return []
    discovered = [
        path.resolve()
        for path in cag_root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_CAG_SUFFIXES
    ]
    return sorted(discovered, key=lambda path: path.relative_to(cag_root).as_posix())


def merge_project_cag_document_paths(
    document_paths: list[str], project_root: Path | None
) -> list[str]:
    """Description:
        Merge configured CAG paths with discovered project ``cag/`` documents.

    Requirements:
        - Preserve the caller-provided path ordering for explicitly configured documents.
        - Append discovered project ``cag/`` documents in deterministic order.
        - Avoid duplicate loads when a file is already registered manually.

    :param document_paths: Explicitly configured CAG document paths.
    :param project_root: Project root used to resolve relative configured paths.
    :returns: Combined CAG path list for loading.
    """

    resolved_root = _resolve_project_root(project_root)
    merged: list[str] = []
    seen: set[Path] = set()

    for path_str in document_paths:
        resolved = _resolve_document_path(path_str, resolved_root)
        if resolved in seen:
            continue
        seen.add(resolved)
        merged.append(path_str)

    for discovered in discover_project_cag_documents(resolved_root):
        if discovered in seen:
            continue
        seen.add(discovered)
        if resolved_root is not None:
            merged.append(discovered.relative_to(resolved_root).as_posix())
        else:
            merged.append(str(discovered))

    return merged


def format_project_cag_budget_guidance(
    documents: list[tuple[str, int]], total_tokens: int, max_tokens: int
) -> str:
    """Description:
        Format actionable guidance for an over-budget project ``cag/`` corpus.

    Requirements:
        - Identify the largest token contributors first.
        - Suggest summary files, splitting stable rules from bulky background, and RAG migration.
        - State that FAITH did not rewrite or compress user files automatically.

    :param documents: Pairs of document path and estimated token count.
    :param total_tokens: Combined token count for the loaded corpus.
    :param max_tokens: Effective budget limit.
    :returns: Human-readable guidance string.
    """

    if documents:
        largest = ", ".join(
            f"{path} ({tokens} tokens)"
            for path, tokens in sorted(documents, key=lambda item: (-item[1], item[0]))[:3]
        )
        contributors = f"Largest contributors: {largest}. "
    else:
        contributors = ""
    return (
        f"CAG token budget exceeded: {total_tokens}/{max_tokens} tokens. "
        f"{contributors}"
        "Suggested reductions: create a shorter curated summary, split stable rules from bulky "
        "background material, or move lower-value content to RAG. "
        "No files were rewritten or compressed automatically."
    )
