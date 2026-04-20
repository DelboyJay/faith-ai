"""Description:
    Cover the FAITH Ollama MCP management server.

Requirements:
    - Verify read-only Ollama model inspection helpers.
    - Verify mutating Ollama and config actions require explicit approval.
    - Verify default-model updates preserve the project system config.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from faith_mcp.ollama.server import OllamaMCPServer


class FakeResponse:
    """Description:
        Provide a minimal HTTP response object for Ollama MCP tests.

    Requirements:
        - Preserve the status code and JSON body returned by the fake client.
        - Raise a runtime error when a test accidentally configures a failing response.

    :param payload: JSON payload returned by ``json()``.
    :param status_code: HTTP response status code.
    """

    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        """Description:
            Initialise the fake response.

        Requirements:
            - Store the payload and status code exactly as supplied.

        :param payload: JSON payload returned by ``json()``.
        :param status_code: HTTP response status code.
        """

        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """Description:
            Raise when the fake response represents an HTTP failure.

        Requirements:
            - Match the minimal behaviour the production server expects from httpx.

        :raises RuntimeError: If the response status is 400 or above.
        """

        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        """Description:
            Return the configured JSON payload.

        Requirements:
            - Return the payload unchanged so tests can assert exact structures.

        :returns: Configured response payload.
        """

        return self.payload


class FakeOllamaClient:
    """Description:
        Capture Ollama HTTP calls without contacting a real Ollama server.

    Requirements:
        - Provide the async subset used by ``OllamaMCPServer``.
        - Preserve request history for exact assertions.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake client with deterministic Ollama payloads.

        Requirements:
            - Start with an empty request log.
            - Provide representative model, process, and generation responses.
        """

        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def get(self, path: str) -> FakeResponse:
        """Description:
            Return a deterministic response for one Ollama GET endpoint.

        Requirements:
            - Support model listing and running-model listing.

        :param path: Ollama API path.
        :returns: Fake HTTP response for the requested path.
        """

        self.calls.append(("GET", path, None))
        if path == "/api/tags":
            return FakeResponse({"models": [{"name": "llama3.2:3b", "size": 2019393189}]})
        if path == "/api/ps":
            return FakeResponse(
                {
                    "models": [
                        {
                            "name": "llama3:8b",
                            "processor": "40%/60% CPU/GPU",
                            "size": 6012954214,
                        }
                    ]
                }
            )
        return FakeResponse({}, status_code=404)

    async def post(self, path: str, *, json: dict[str, Any]) -> FakeResponse:
        """Description:
            Return a deterministic response for one Ollama POST endpoint.

        Requirements:
            - Support model pulls and lightweight probes.

        :param path: Ollama API path.
        :param json: JSON payload sent to Ollama.
        :returns: Fake HTTP response for the requested path.
        """

        self.calls.append(("POST", path, json))
        if path == "/api/pull":
            return FakeResponse({"status": "success"})
        if path == "/api/generate":
            return FakeResponse(
                {
                    "response": "probe ok",
                    "total_duration": 1000,
                    "eval_count": 8,
                    "eval_duration": 500,
                }
            )
        return FakeResponse({}, status_code=404)

    async def request(self, method: str, path: str, *, json: dict[str, Any]) -> FakeResponse:
        """Description:
            Return a deterministic response for one generic Ollama request.

        Requirements:
            - Support the DELETE model endpoint used by Ollama.

        :param method: HTTP method.
        :param path: Ollama API path.
        :param json: JSON payload sent to Ollama.
        :returns: Fake HTTP response for the requested path.
        """

        self.calls.append((method, path, json))
        if method == "DELETE" and path == "/api/delete":
            return FakeResponse({"status": "deleted"})
        return FakeResponse({}, status_code=404)


@pytest.fixture
def fake_client() -> FakeOllamaClient:
    """Description:
        Provide a fake Ollama HTTP client.

    Requirements:
        - Keep each test isolated with an empty call log.

    :returns: Fake Ollama client.
    """

    return FakeOllamaClient()


@pytest.fixture
def server(fake_client: FakeOllamaClient, tmp_path: Path) -> OllamaMCPServer:
    """Description:
        Provide an Ollama MCP server with fake dependencies.

    Requirements:
        - Avoid network access.
        - Use a temporary FAITH directory for config-writing tests.

    :param fake_client: Fake Ollama client fixture.
    :param tmp_path: Temporary workspace path.
    :returns: Configured Ollama MCP server.
    """

    return OllamaMCPServer(client=fake_client, faith_dir=tmp_path / ".faith")


@pytest.mark.asyncio
async def test_list_models_returns_installed_ollama_models(server: OllamaMCPServer) -> None:
    """Description:
        Verify installed Ollama models can be listed through the MCP server.

    Requirements:
        - This test is needed so the PA can discover available local models before switching.
        - Verify the server returns the Ollama ``models`` payload.

    :param server: Ollama MCP server under test.
    """

    result = await server.list_models()

    assert result["models"][0]["name"] == "llama3.2:3b"


@pytest.mark.asyncio
async def test_list_running_models_returns_processor_split(server: OllamaMCPServer) -> None:
    """Description:
        Verify running Ollama model status includes the CPU/GPU processor split.

    Requirements:
        - This test is needed so the PA can detect slow CPU-bound Ollama models.
        - Verify the running-model payload preserves the processor field.

    :param server: Ollama MCP server under test.
    """

    result = await server.list_running_models()

    assert result["models"][0]["processor"] == "40%/60% CPU/GPU"


@pytest.mark.asyncio
async def test_pull_model_requires_approval(server: OllamaMCPServer) -> None:
    """Description:
        Verify model downloads require explicit approval.

    Requirements:
        - This test is needed because model pulls consume disk, bandwidth, and time.
        - Verify the server blocks unapproved pull requests.

    :param server: Ollama MCP server under test.
    """

    with pytest.raises(PermissionError):
        await server.pull_model("llama3.2:3b")


@pytest.mark.asyncio
async def test_pull_model_calls_ollama_when_approved(
    server: OllamaMCPServer,
    fake_client: FakeOllamaClient,
) -> None:
    """Description:
        Verify approved model downloads call the Ollama pull endpoint.

    Requirements:
        - This test is needed so the PA can download requested local models after user approval.
        - Verify pulls are non-streaming for deterministic MCP responses.

    :param server: Ollama MCP server under test.
    :param fake_client: Fake Ollama client fixture.
    """

    result = await server.pull_model("llama3.2:3b", approved=True)

    assert result["status"] == "success"
    assert fake_client.calls[-1] == (
        "POST",
        "/api/pull",
        {"model": "llama3.2:3b", "stream": False},
    )


@pytest.mark.asyncio
async def test_delete_model_requires_approval(server: OllamaMCPServer) -> None:
    """Description:
        Verify model deletion requires explicit approval.

    Requirements:
        - This test is needed because deleting a model removes local state.
        - Verify the server blocks unapproved delete requests.

    :param server: Ollama MCP server under test.
    """

    with pytest.raises(PermissionError):
        await server.delete_model("llama3:8b")


@pytest.mark.asyncio
async def test_probe_model_reports_generation_and_running_status(
    server: OllamaMCPServer,
) -> None:
    """Description:
        Verify lightweight model probes include generation and processor data.

    Requirements:
        - This test is needed so the PA can recommend faster local models based on runtime evidence.
        - Verify the probe result combines generation metrics with ``ollama ps`` status.

    :param server: Ollama MCP server under test.
    """

    result = await server.probe_model("llama3:8b")

    assert result["model"] == "llama3:8b"
    assert result["response"] == "probe ok"
    assert result["running_models"][0]["processor"] == "40%/60% CPU/GPU"


@pytest.mark.asyncio
async def test_set_default_model_updates_system_config_when_approved(
    server: OllamaMCPServer,
    tmp_path: Path,
) -> None:
    """Description:
        Verify approved default-model changes update ``system.yaml``.

    Requirements:
        - This test is needed so the PA can persist a user-requested local model switch.
        - Verify both PA and specialist defaults can be updated together.

    :param server: Ollama MCP server under test.
    :param tmp_path: Temporary workspace path.
    """

    faith_dir = tmp_path / ".faith"
    faith_dir.mkdir()
    system_path = faith_dir / "system.yaml"
    system_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "privacy_profile": "internal",
                "pa": {"model": "ollama/llama3:8b"},
                "default_agent_model": "ollama/llama3:8b",
            }
        ),
        encoding="utf-8",
    )

    result = await server.set_default_model(
        "ollama/llama3.2:3b",
        target="all",
        approved=True,
    )

    updated = yaml.safe_load(system_path.read_text(encoding="utf-8"))
    assert result["updated"] is True
    assert updated["pa"]["model"] == "ollama/llama3.2:3b"
    assert updated["default_agent_model"] == "ollama/llama3.2:3b"


@pytest.mark.asyncio
async def test_handle_tool_call_dispatches_supported_actions(
    server: OllamaMCPServer,
) -> None:
    """Description:
        Verify MCP-style action dispatch reaches the correct Ollama helper.

    Requirements:
        - This test is needed so the PA can call the server through the generic tool interface.
        - Verify the ``list_models`` action returns installed model data.

    :param server: Ollama MCP server under test.
    """

    result = await server.handle_tool_call("list_models", {})

    assert result["models"][0]["name"] == "llama3.2:3b"
