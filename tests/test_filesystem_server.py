"""
Description:
    Verify the filesystem server facade wires the underlying filesystem
    operations together correctly.

Requirements:
    - Cover a simple write and read round trip through the server facade.
    - Verify the returned payloads reflect the created file state.
"""

from faith_mcp.filesystem import FilesystemServer


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
