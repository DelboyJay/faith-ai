"""Description:
    Provide a FAITH MCP-style management facade for Ollama.

Requirements:
    - Let the PA list, inspect, pull, delete, probe, and select Ollama models.
    - Require explicit approval for mutating model and configuration actions.
    - Keep Ollama HTTP details outside the PA orchestration core.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import yaml

DEFAULT_OLLAMA_BASE_URL = "http://ollama:11434"
DEFAULT_PROBE_PROMPT = "Reply with exactly: ok"


class OllamaMCPServer:
    """Description:
        Coordinate safe Ollama model-management actions for the PA.

    Requirements:
        - Use the Ollama HTTP API for model inspection and lifecycle actions.
        - Persist default-model changes to project ``.faith/system.yaml``.
        - Expose a small action dispatcher compatible with the existing MCP tool pattern.

    :param base_url: Ollama API base URL.
    :param client: Optional async HTTP client used by tests or advanced embedding.
    :param faith_dir: Project ``.faith`` directory used for config updates.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        client: Any | None = None,
        faith_dir: Path | None = None,
    ) -> None:
        """Description:
            Initialise the Ollama management server.

        Requirements:
            - Create a default async HTTP client when one is not supplied.
            - Resolve the FAITH directory when provided.

        :param base_url: Ollama API base URL.
        :param client: Optional async HTTP client used by tests or advanced embedding.
        :param faith_dir: Project ``.faith`` directory used for config updates.
        """

        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(base_url=self.base_url, timeout=120.0)
        self.faith_dir = Path(faith_dir).resolve() if faith_dir is not None else None

    async def list_models(self) -> dict[str, Any]:
        """Description:
            Return installed Ollama models.

        Requirements:
            - Query Ollama's model tag endpoint.
            - Return the parsed payload without hiding model metadata.

        :returns: Ollama ``/api/tags`` response payload.
        """

        response = await self.client.get("/api/tags")
        response.raise_for_status()
        return response.json()

    async def list_running_models(self) -> dict[str, Any]:
        """Description:
            Return currently loaded Ollama models.

        Requirements:
            - Query Ollama's process endpoint.
            - Preserve processor split data so the PA can detect CPU/GPU offload.

        :returns: Ollama ``/api/ps`` response payload.
        """

        response = await self.client.get("/api/ps")
        response.raise_for_status()
        return response.json()

    async def pull_model(self, model: str, *, approved: bool = False) -> dict[str, Any]:
        """Description:
            Pull one Ollama model after explicit approval.

        Requirements:
            - Block unapproved downloads because they consume bandwidth and disk.
            - Use non-streaming pulls so MCP callers receive one stable payload.

        :param model: Ollama model tag to download.
        :param approved: Whether the PA approval layer approved the action.
        :returns: Ollama pull response payload.
        :raises PermissionError: If approval was not provided.
        """

        self._require_approval(approved, "Pulling an Ollama model requires approval.")
        response = await self.client.post("/api/pull", json={"model": model, "stream": False})
        response.raise_for_status()
        return response.json()

    async def delete_model(self, model: str, *, approved: bool = False) -> dict[str, Any]:
        """Description:
            Delete one Ollama model after explicit approval.

        Requirements:
            - Block unapproved deletes because they remove local model state.
            - Use the Ollama delete endpoint with the requested model tag.

        :param model: Ollama model tag to delete.
        :param approved: Whether the PA approval layer approved the action.
        :returns: Ollama delete response payload, or a success payload when Ollama returns empty JSON.
        :raises PermissionError: If approval was not provided.
        """

        self._require_approval(approved, "Deleting an Ollama model requires approval.")
        response = await self.client.request("DELETE", "/api/delete", json={"model": model})
        response.raise_for_status()
        return response.json() or {"status": "deleted", "model": model}

    async def probe_model(
        self,
        model: str,
        *,
        prompt: str = DEFAULT_PROBE_PROMPT,
    ) -> dict[str, Any]:
        """Description:
            Run a tiny inference probe and report Ollama runtime status.

        Requirements:
            - Generate a very small response to measure whether the model runs.
            - Include current ``ollama ps`` data so the PA can inspect CPU/GPU split.

        :param model: Ollama model tag to probe.
        :param prompt: Probe prompt sent to Ollama.
        :returns: Probe result containing generation metrics and running model status.
        """

        response = await self.client.post(
            "/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 8},
            },
        )
        response.raise_for_status()
        payload = response.json()
        running = await self.list_running_models()
        return {
            "model": model,
            "response": payload.get("response", ""),
            "total_duration": payload.get("total_duration"),
            "eval_count": payload.get("eval_count"),
            "eval_duration": payload.get("eval_duration"),
            "running_models": running.get("models", []),
        }

    async def set_default_model(
        self,
        model: str,
        *,
        target: str = "pa",
        approved: bool = False,
    ) -> dict[str, Any]:
        """Description:
            Persist a new PA or specialist default model in project config.

        Requirements:
            - Block unapproved config changes.
            - Update only the requested target unless ``target`` is ``all``.
            - Preserve unrelated ``system.yaml`` keys.

        :param model: Model string to write, such as ``ollama/llama3.2:3b``.
        :param target: One of ``pa``, ``agents``, or ``all``.
        :param approved: Whether the PA approval layer approved the action.
        :returns: Update summary with the written path and target.
        :raises PermissionError: If approval was not provided.
        :raises ValueError: If no FAITH directory is configured or the target is invalid.
        """

        self._require_approval(approved, "Changing the default model requires approval.")
        if self.faith_dir is None:
            raise ValueError("Cannot update defaults without a configured faith_dir.")
        if target not in {"pa", "agents", "all"}:
            raise ValueError("target must be one of: pa, agents, all")

        system_path = self.faith_dir / "system.yaml"
        data = self._read_system_config(system_path)
        if target in {"pa", "all"}:
            pa_config = data.setdefault("pa", {})
            if not isinstance(pa_config, dict):
                pa_config = {}
                data["pa"] = pa_config
            pa_config["model"] = model
        if target in {"agents", "all"}:
            data["default_agent_model"] = model

        system_path.parent.mkdir(parents=True, exist_ok=True)
        system_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return {"updated": True, "path": str(system_path), "target": target, "model": model}

    async def handle_tool_call(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """Description:
            Dispatch one MCP-style tool action.

        Requirements:
            - Support the PA-facing Ollama management action names.
            - Raise a clear error for unknown actions.

        :param action: Tool action name.
        :param args: Tool arguments supplied by the PA.
        :returns: Structured action response payload.
        :raises ValueError: If the action is unknown.
        """

        if action == "list_models":
            return await self.list_models()
        if action == "list_running_models":
            return await self.list_running_models()
        if action == "pull_model":
            return await self.pull_model(str(args["model"]), approved=bool(args.get("approved")))
        if action == "delete_model":
            return await self.delete_model(str(args["model"]), approved=bool(args.get("approved")))
        if action == "probe_model":
            return await self.probe_model(
                str(args["model"]),
                prompt=str(args.get("prompt", DEFAULT_PROBE_PROMPT)),
            )
        if action == "set_default_model":
            return await self.set_default_model(
                str(args["model"]),
                target=str(args.get("target", "pa")),
                approved=bool(args.get("approved")),
            )
        raise ValueError(f"Unknown Ollama MCP action: {action}")

    def _read_system_config(self, system_path: Path) -> dict[str, Any]:
        """Description:
            Read a project ``system.yaml`` file into a mutable mapping.

        Requirements:
            - Return a minimal valid structure when the file does not exist.
            - Reject non-mapping YAML content.

        :param system_path: Project system config path.
        :returns: Mutable system config mapping.
        :raises ValueError: If the YAML content is not a mapping.
        """

        if not system_path.exists():
            return {
                "schema_version": "1.0",
                "privacy_profile": "internal",
                "pa": {},
                "default_agent_model": "",
            }
        loaded = yaml.safe_load(system_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError("system.yaml must contain a mapping.")
        return loaded

    @staticmethod
    def _require_approval(approved: bool, message: str) -> None:
        """Description:
            Enforce approval for mutating Ollama management actions.

        Requirements:
            - Raise a permission error before any state-changing work begins.

        :param approved: Whether approval has been granted.
        :param message: Error message to raise when approval is missing.
        :raises PermissionError: If approval is false.
        """

        if not approved:
            raise PermissionError(message)
