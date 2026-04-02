from faith_mcp.filesystem import FilesystemServer


def test_filesystem_server_round_trip(tmp_path):
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
        "workspace", "notes.txt", "hello", agent_id="dev", agent_mounts={"workspace": "readwrite"}
    )
    assert result["created"] is True
    payload = server.read(
        "workspace", "notes.txt", agent_id="dev", agent_mounts={"workspace": "readwrite"}
    )
    assert payload["content"] == "hello"

