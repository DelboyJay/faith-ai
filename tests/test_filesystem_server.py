"""
Description:
    Verify the filesystem server facade wires the underlying filesystem
    operations together correctly.

Requirements:
    - Cover a simple write and read round trip through the server facade.
    - Verify the returned payloads reflect the created file state.
    - Verify lifecycle events, hot reload, and file-watch polling are surfaced.
"""

from pathlib import Path

from faith_mcp.filesystem import FilesystemServer
from faith_shared.protocol.events import EventType, FaithEvent


class DummyPublisher:
    """
    Description:
        Record filesystem lifecycle and file-change events for later assertions.

    Requirements:
        - Preserve the published event objects in call order.
    """

    def __init__(self) -> None:
        """
        Description:
            Initialise the dummy publisher with no recorded events.

        Requirements:
            - Start with an empty event list.
        """

        self.events = []

    async def publish(self, event) -> None:
        """
        Description:
            Record one published event object.

        Requirements:
            - Preserve the raw event object for assertions.

        :param event: Event payload published by the filesystem server.
        """

        self.events.append(event)


def test_filesystem_server_round_trip(tmp_path) -> None:
    """
    Description:
        Verify the filesystem server can write and then read the same file
        through one configured mount.

    Requirements:
        - This test is needed to prove the server facade delegates write and read
          operations correctly.
        - Verify the write reports file creation and the read returns the stored
          content.

        :param tmp_path: Temporary directory provided by pytest.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = FilesystemServer(
        tmp_path / ".faith",
        {
            "mounts": {
                "workspace": {
                    "host_path": str(workspace),
                    "access": "readwrite",
                    "history": True,
                    "history_depth": 3,
                }
            }
        },
    )

    result = server.write(
        "workspace",
        "notes.txt",
        "hello",
        agent_id="dev",
        agent_mounts={"workspace": "readwrite"},
    )
    assert result["created"] is True
    payload = server.read(
        "workspace",
        "notes.txt",
        agent_id="dev",
        agent_mounts={"workspace": "readwrite"},
    )
    assert payload["content"] == "hello"


def test_filesystem_server_restore_version_round_trip(tmp_path) -> None:
    """
    Description:
        Verify the filesystem server can restore a stored history version.

    Requirements:
        - This test is needed to prove the server facade wires file history restoration correctly.
        - Verify restoring a previous version rewrites the workspace file content.

    :param tmp_path: Temporary directory provided by pytest.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = FilesystemServer(
        tmp_path / ".faith",
        {
            "mounts": {
                "workspace": {
                    "host_path": str(workspace),
                    "access": "readwrite",
                    "history": True,
                    "history_depth": 3,
                }
            }
        },
    )

    server.write(
        "workspace",
        "notes.txt",
        "one",
        agent_id="dev",
        agent_mounts={"workspace": "readwrite"},
    )
    server.write(
        "workspace",
        "notes.txt",
        "two",
        agent_id="dev",
        agent_mounts={"workspace": "readwrite"},
    )

    history = server.list_history("workspace", "notes.txt")
    assert len(history) >= 1
    restored = server.restore_version(
        "workspace", "notes.txt", history[0]["version"], agent_id="dev"
    )
    assert restored is True
    payload = server.read(
        "workspace",
        "notes.txt",
        agent_id="dev",
        agent_mounts={"workspace": "readwrite"},
    )
    assert payload["content"] == "one"


def test_filesystem_server_emits_lifecycle_events(tmp_path) -> None:
    """
    Description:
        Verify filesystem operations publish lifecycle events when a publisher is configured.

    Requirements:
        - This test is needed to prove the filesystem tool participates in the shared event bus.
        - Verify a write emits both start and completion events.

    :param tmp_path: Temporary directory provided by pytest.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    publisher = DummyPublisher()
    server = FilesystemServer(
        tmp_path / ".faith",
        {
            "mounts": {
                "workspace": {
                    "host_path": str(workspace),
                    "access": "readwrite",
                }
            }
        },
        event_publisher=publisher,
    )

    server.write(
        "workspace",
        "notes.txt",
        "hello",
        agent_id="dev",
        agent_mounts={"workspace": "readwrite"},
    )

    assert [event.event.value for event in publisher.events] == [
        "tool:call_started",
        "tool:call_complete",
    ]


def test_filesystem_server_polls_file_events(tmp_path) -> None:
    """
    Description:
        Verify dynamic file-watch subscriptions detect created files.

    Requirements:
        - This test is needed to prove the filesystem server wires the watcher into a usable facade.
        - Verify polling a subscribed pattern returns a created-file event for the agent.

    :param tmp_path: Temporary directory provided by pytest.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = FilesystemServer(
        tmp_path / ".faith",
        {
            "mounts": {
                "workspace": {
                    "host_path": str(workspace),
                    "access": "readwrite",
                }
            }
        },
    )

    server.register_dynamic_subscription(
        "dev",
        "workspace",
        "*.txt",
        ["file:created", "file:changed", "file:deleted"],
    )
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")

    events = server.poll_file_events()
    assert events == [{"agent_id": "dev", "event": "file:created", "path": "notes.txt"}]


def test_filesystem_server_loads_static_subscriptions(tmp_path: Path) -> None:
    """
    Description:
        Verify static file-watch subscriptions are loaded from agent config payloads.

    Requirements:
        - This test is needed to prove the filesystem watcher honours file watches declared in agent config.
        - Verify a static subscription can detect a created file on the configured mount.

    :param tmp_path: Temporary directory provided by pytest.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = FilesystemServer(
        tmp_path / ".faith",
        {"mounts": {"workspace": {"host_path": str(workspace), "access": "readwrite"}}},
    )
    server.load_static_subscriptions(
        {
            "dev": {
                "file_watches": [
                    {"pattern": "workspace/*.txt", "events": ["file:created", "file:changed"]},
                ]
            }
        }
    )
    (workspace / "watched.txt").write_text("hello", encoding="utf-8")
    assert server.poll_file_events() == [
        {"agent_id": "dev", "event": "file:created", "path": "watched.txt"}
    ]


def test_filesystem_server_handles_config_change_reload(tmp_path: Path) -> None:
    """
    Description:
        Verify the filesystem server reloads mount config in response to a filesystem config change event.

    Requirements:
        - This test is needed to prove filesystem config hot reload works without restarting the server.
        - Verify a new mount becomes available after the reload handler runs.

    :param tmp_path: Temporary directory provided by pytest.
    """

    workspace = tmp_path / "workspace"
    docs = tmp_path / "docs"
    workspace.mkdir()
    docs.mkdir()
    server = FilesystemServer(
        tmp_path / ".faith",
        {"mounts": {"workspace": {"host_path": str(workspace), "access": "readwrite"}}},
    )
    changed = server.handle_config_changed(
        FaithEvent(
            event=EventType.SYSTEM_CONFIG_CHANGED,
            source="config-watcher",
            data={
                "file": "filesystem.yaml",
                "path": str(tmp_path / ".faith" / "tools" / "filesystem.yaml"),
            },
        ),
        {
            "mounts": {
                "workspace": {"host_path": str(workspace), "access": "readwrite"},
                "docs": {"host_path": str(docs), "access": "readonly"},
            }
        },
    )
    assert changed is True
    assert server.mount_registry.get("docs") is not None


def test_filesystem_server_dispatches_handle_tool_call(tmp_path: Path) -> None:
    """
    Description:
        Verify the filesystem server exposes a stable MCP-facing dispatch surface.

    Requirements:
        - This test is needed to prove callers can use action names instead of direct method calls.
        - Verify the dispatch surface can perform a write followed by a read.

    :param tmp_path: Temporary directory provided by pytest.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = FilesystemServer(
        tmp_path / ".faith",
        {"mounts": {"workspace": {"host_path": str(workspace), "access": "readwrite"}}},
    )
    payload = __import__("asyncio").run(
        server.handle_tool_call(
            "write",
            {"mount": "workspace", "path": "notes.txt", "content": "hello"},
            agent_id="dev",
            agent_mounts={"workspace": "readwrite"},
        )
    )
    assert payload["created"] is True
    read_payload = __import__("asyncio").run(
        server.handle_tool_call(
            "read",
            {"mount": "workspace", "path": "notes.txt"},
            agent_id="dev",
            agent_mounts={"workspace": "readwrite"},
        )
    )
    assert read_payload["content"] == "hello"
