"""Description:
    Persist scoped user files for deterministic retrieval and session reuse.

Requirements:
    - Store original uploaded bytes exactly once using SHA-256 as the canonical file identifier.
    - Preserve scope, description, and session-binding metadata on the host-backed project volume.
    - Support reversible trash and deterministic one-time cleanup.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANONICAL_STORAGE_SCOPES = {"global", "scoped", "session", "one-time"}


def _utc_now_iso() -> str:
    """Description:
        Return the current UTC time in stable storage-registry format.

    Requirements:
        - Use timezone-aware UTC timestamps ending with ``Z``.

    :returns: Current UTC timestamp string.
    """

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class StorageConflictError(Exception):
    """Description:
        Represent an identical-content upload that conflicts with existing metadata.

    Requirements:
        - Preserve the canonical file identifier and differing metadata fields.
        - Expose the current stored record for browser conflict resolution.

    :param file_id: Canonical SHA-256 identifier of the existing file.
    :param conflicts: Metadata field names that differ from the existing record.
    :param existing_record: Current stored-file record involved in the conflict.
    """

    file_id: str
    conflicts: list[str]
    existing_record: dict[str, Any]

    def __str__(self) -> str:
        """Description:
            Return a concise user-facing conflict description.

        Requirements:
            - Mention the differing metadata fields clearly.

        :returns: Human-readable conflict message.
        """

        return f"Stored file conflict for {self.file_id}: {', '.join(self.conflicts)}"


class FileStorageRegistry:
    """Description:
        Manage host-backed file storage, deduplication, and trash lifecycle.

    Requirements:
        - Keep one canonical registry JSON file under ``.faith/storage``.
        - Preserve original file bytes under a stable SHA-256-derived path.
        - Prevent duplicate physical storage of identical content.

    :param project_root: Project root that owns the host-backed ``.faith`` tree.
    """

    def __init__(self, project_root: Path) -> None:
        """Description:
            Initialise the storage registry directories and manifest.

        Requirements:
            - Create the storage root, files root, and trash root when missing.
            - Create an empty registry manifest lazily on first use.

        :param project_root: Project root that owns the host-backed ``.faith`` tree.
        """

        self.project_root = Path(project_root).resolve()
        self.faith_dir = self.project_root / ".faith"
        self.storage_dir = self.faith_dir / "storage"
        self.files_dir = self.storage_dir / "files"
        self.trash_dir = self.storage_dir / "trash"
        self.registry_path = self.storage_dir / "registry.json"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self._write_registry({"files": {}, "trash": {}, "settings": {}})

    def _read_registry(self) -> dict[str, Any]:
        """Description:
            Load the current storage registry manifest from disk.

        Requirements:
            - Return the canonical empty structure when the manifest is missing or malformed.

        :returns: Parsed storage manifest payload.
        """

        if not self.registry_path.exists():
            return {"files": {}, "trash": {}, "settings": {}}
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"files": {}, "trash": {}, "settings": {}}
        if not isinstance(payload, dict):
            return {"files": {}, "trash": {}, "settings": {}}
        payload.setdefault("files", {})
        payload.setdefault("trash", {})
        payload.setdefault("settings", {})
        return payload

    def _write_registry(self, payload: dict[str, Any]) -> None:
        """Description:
            Persist the storage registry manifest to disk.

        Requirements:
            - Write indented JSON for inspectability.

        :param payload: Registry payload to persist.
        """

        self.registry_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _hash_content(content: bytes) -> str:
        """Description:
            Return the canonical SHA-256 identifier for one file payload.

        Requirements:
            - Use the full content bytes as the only identity input.

        :param content: File content bytes to hash.
        :returns: Canonical SHA-256 hex digest.
        """

        return hashlib.sha256(content).hexdigest()

    def _record_path(self, file_id: str) -> Path:
        """Description:
            Return the canonical stored-byte path for one file identifier.

        Requirements:
            - Keep the stored path stable and independent of the original filename.

        :param file_id: Canonical SHA-256 identifier.
        :returns: Host-backed stored-byte path.
        """

        return self.files_dir / file_id

    @staticmethod
    def _normalize_session_bindings(session_bindings: list[str] | None) -> list[str]:
        """Description:
            Normalize session bindings into a stable sorted unique list.

        Requirements:
            - Remove empty values.
            - Preserve deterministic ordering.

        :param session_bindings: Optional raw session-binding list.
        :returns: Sorted unique session bindings.
        """

        return sorted({binding for binding in session_bindings or [] if binding})

    def ingest_bytes(
        self,
        *,
        filename: str,
        content: bytes,
        scope: str,
        session_bindings: list[str] | None,
        description: str,
        inference_id: str | None = None,
    ) -> dict[str, Any]:
        """Description:
            Persist one uploaded file or reuse an existing identical-content record.

        Requirements:
            - Refuse unsupported storage-scope values.
            - Raise a conflict when identical content arrives with different filename, description, or scope metadata.
            - Store the original bytes only once physically.

        :param filename: Original user-facing filename.
        :param content: Uploaded file content bytes.
        :param scope: Requested sharing scope.
        :param session_bindings: Session identifiers associated with the file.
        :param description: Short user-facing description.
        :param inference_id: Optional one-time inference identifier.
        :raises StorageConflictError: If identical content already exists with conflicting metadata.
        :returns: Stored-file record payload.
        """

        normalized_scope = str(scope).strip().lower()
        if normalized_scope not in CANONICAL_STORAGE_SCOPES:
            raise ValueError(f"Unsupported storage scope: {scope}")
        normalized_filename = filename.strip() or "upload.bin"
        normalized_description = description.strip()
        normalized_bindings = self._normalize_session_bindings(session_bindings)
        file_id = self._hash_content(content)
        registry = self._read_registry()
        existing = registry["files"].get(file_id) or registry["trash"].get(file_id)
        if isinstance(existing, dict):
            conflicts: list[str] = []
            if existing.get("filename") != normalized_filename:
                conflicts.append("filename")
            if existing.get("description", "") != normalized_description:
                conflicts.append("description")
            if existing.get("scope") != normalized_scope:
                conflicts.append("scope")
            if existing.get("session_bindings", []) != normalized_bindings:
                conflicts.append("session_bindings")
            if conflicts:
                raise StorageConflictError(
                    file_id=file_id,
                    conflicts=conflicts,
                    existing_record=dict(existing),
                )
            if file_id in registry["trash"]:
                registry["files"][file_id] = registry["trash"].pop(file_id)
                registry["files"][file_id]["trashed_at"] = None
                self._write_registry(registry)
            return dict(registry["files"].get(file_id, existing))

        record_path = self._record_path(file_id)
        if not record_path.exists():
            record_path.write_bytes(content)
        now = _utc_now_iso()
        record = {
            "file_id": file_id,
            "sha256": file_id,
            "filename": normalized_filename,
            "description": normalized_description,
            "scope": normalized_scope,
            "session_bindings": normalized_bindings,
            "inference_id": inference_id,
            "created_at": now,
            "updated_at": now,
            "trashed_at": None,
            "path": record_path.as_posix(),
            "size_bytes": len(content),
        }
        registry["files"][file_id] = record
        self._write_registry(registry)
        return dict(record)

    def list_files(self) -> list[dict[str, Any]]:
        """Description:
            Return the active stored-file inventory.

        Requirements:
            - Exclude trashed records.
            - Sort records by filename, then file identifier for stability.

        :returns: Active stored-file records.
        """

        registry = self._read_registry()
        return sorted(
            [dict(record) for record in registry["files"].values()],
            key=lambda record: (str(record.get("filename", "")), str(record.get("file_id", ""))),
        )

    def list_trash(self) -> list[dict[str, Any]]:
        """Description:
            Return the trashed stored-file inventory.

        Requirements:
            - Sort records newest-trash-first for review workflows.

        :returns: Trashed stored-file records.
        """

        registry = self._read_registry()
        return sorted(
            [dict(record) for record in registry["trash"].values()],
            key=lambda record: str(record.get("trashed_at") or ""),
            reverse=True,
        )

    def update_file(
        self,
        file_id: str,
        *,
        filename: str | None = None,
        description: str | None = None,
        scope: str | None = None,
        session_bindings: list[str] | None = None,
    ) -> dict[str, Any]:
        """Description:
            Update one active stored-file record in place.

        Requirements:
            - Refuse updates for unknown file identifiers.
            - Preserve the canonical file identifier and stored-byte path.

        :param file_id: Canonical SHA-256 identifier to update.
        :param filename: Optional replacement filename.
        :param description: Optional replacement description.
        :param scope: Optional replacement scope.
        :param session_bindings: Optional replacement session bindings.
        :returns: Updated stored-file record.
        :raises KeyError: If the file identifier is unknown.
        """

        registry = self._read_registry()
        record = registry["files"].get(file_id)
        if not isinstance(record, dict):
            raise KeyError(file_id)
        if filename is not None:
            record["filename"] = filename.strip() or record["filename"]
        if description is not None:
            record["description"] = description.strip()
        if scope is not None:
            normalized_scope = str(scope).strip().lower()
            if normalized_scope not in CANONICAL_STORAGE_SCOPES:
                raise ValueError(f"Unsupported storage scope: {scope}")
            record["scope"] = normalized_scope
        if session_bindings is not None:
            record["session_bindings"] = self._normalize_session_bindings(session_bindings)
        record["updated_at"] = _utc_now_iso()
        self._write_registry(registry)
        return dict(record)

    def trash_file(self, file_id: str) -> dict[str, Any]:
        """Description:
            Move one active stored file into trash.

        Requirements:
            - Remove the record from the active inventory immediately.
            - Preserve the stored bytes for later restore until final purge.

        :param file_id: Canonical SHA-256 identifier to trash.
        :returns: Trashed stored-file record.
        :raises KeyError: If the file identifier is unknown.
        """

        registry = self._read_registry()
        record = registry["files"].pop(file_id, None)
        if not isinstance(record, dict):
            raise KeyError(file_id)
        record["trashed_at"] = _utc_now_iso()
        registry["trash"][file_id] = record
        self._write_registry(registry)
        return dict(record)

    def restore_file(self, file_id: str) -> dict[str, Any]:
        """Description:
            Restore one trashed file back into the active inventory.

        Requirements:
            - Clear the trashed timestamp when restore succeeds.

        :param file_id: Canonical SHA-256 identifier to restore.
        :returns: Restored stored-file record.
        :raises KeyError: If the file identifier is unknown in trash.
        """

        registry = self._read_registry()
        record = registry["trash"].pop(file_id, None)
        if not isinstance(record, dict):
            raise KeyError(file_id)
        record["trashed_at"] = None
        registry["files"][file_id] = record
        self._write_registry(registry)
        return dict(record)

    def hard_delete_file(self, file_id: str) -> dict[str, Any]:
        """Description:
            Permanently delete one stored file record and its bytes from either inventory.

        Requirements:
            - Remove the record from active inventory or trash.
            - Delete the stored bytes when the canonical record is removed.

        :param file_id: Canonical SHA-256 identifier to delete permanently.
        :returns: Deleted stored-file record.
        :raises KeyError: If the file identifier is unknown.
        """

        registry = self._read_registry()
        record = registry["trash"].pop(file_id, None)
        if not isinstance(record, dict):
            record = registry["files"].pop(file_id, None)
        if not isinstance(record, dict):
            raise KeyError(file_id)
        Path(str(record.get("path", ""))).unlink(missing_ok=True)
        self._write_registry(registry)
        return dict(record)

    def cleanup_one_time_files(self, inference_id: str) -> list[str]:
        """Description:
            Permanently remove all one-time files associated with one inference round.

        Requirements:
            - Delete both metadata and the stored bytes for matching one-time files.
            - Return the removed file identifiers in deterministic order.

        :param inference_id: Inference-round identifier whose one-time files should be removed.
        :returns: Removed file identifiers.
        """

        registry = self._read_registry()
        removed_ids: list[str] = []
        for file_id, record in list(registry["files"].items()):
            if (
                isinstance(record, dict)
                and record.get("scope") == "one-time"
                and record.get("inference_id") == inference_id
            ):
                registry["files"].pop(file_id, None)
                Path(str(record.get("path", ""))).unlink(missing_ok=True)
                removed_ids.append(file_id)
        self._write_registry(registry)
        return sorted(removed_ids)

    def iter_linked_files_for_session(self, session_id: str) -> list[dict[str, Any]]:
        """Description:
            Return stored files that should be linked into one session export.

        Requirements:
            - Include active Global files.
            - Include active Session files bound to the requested session.
            - Include active Scoped files bound to the requested session.
            - Exclude One-time and trashed files.

        :param session_id: Session identifier whose linked files are being exported.
        :returns: Active linked stored-file records.
        """

        linked: list[dict[str, Any]] = []
        for record in self.list_files():
            scope = str(record.get("scope"))
            bindings = set(record.get("session_bindings", []))
            if scope == "global":
                linked.append(record)
            elif scope in {"session", "scoped"} and session_id in bindings:
                linked.append(record)
        return linked
