"""Redis helper tests for the FAITH POC."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from faith.utils import redis_client


class FakeAsyncClient:
    def __init__(self, should_ping: bool = True):
        self.should_ping = should_ping
        self.closed = False

    async def ping(self) -> bool:
        if not self.should_ping:
            raise OSError("redis unavailable")
        return True

    async def aclose(self) -> None:
        self.closed = True


class RedisClientTests(unittest.IsolatedAsyncioTestCase):
    def test_get_redis_url_defaults_to_local(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(redis_client.get_redis_url(), "redis://redis:6379/0")

    def test_get_redis_url_honors_env(self) -> None:
        with patch.dict(os.environ, {"FAITH_REDIS_URL": "redis://example:6380/1"}, clear=True):
            self.assertEqual(redis_client.get_redis_url(), "redis://example:6380/1")

    def test_get_sync_client_uses_requested_url(self) -> None:
        client = redis_client.get_sync_client("redis://example:6381/2")
        self.assertEqual(client.connection_pool.connection_kwargs["host"], "example")
        self.assertEqual(client.connection_pool.connection_kwargs["port"], 6381)
        self.assertEqual(client.connection_pool.connection_kwargs["db"], 2)
        client.close()

    async def test_check_connection_succeeds(self) -> None:
        client = FakeAsyncClient(should_ping=True)
        self.assertTrue(await redis_client.check_connection(client))
        self.assertFalse(client.closed)

    async def test_check_connection_closes_temporary_client(self) -> None:
        created_clients: list[FakeAsyncClient] = []

        async def fake_get_async_client(*args, **kwargs):
            client = FakeAsyncClient(should_ping=True)
            created_clients.append(client)
            return client

        with patch.object(redis_client, "get_async_client", side_effect=fake_get_async_client):
            self.assertTrue(await redis_client.check_connection())

        self.assertEqual(len(created_clients), 1)
        self.assertTrue(created_clients[0].closed)

    async def test_check_connection_handles_errors(self) -> None:
        client = FakeAsyncClient(should_ping=False)
        self.assertFalse(await redis_client.check_connection(client))
        self.assertFalse(client.closed)


if __name__ == "__main__":
    unittest.main()
