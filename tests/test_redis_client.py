"""Description:
    Verify the shared Redis helper functions used by the FAITH runtime.

Requirements:
    - Prove Redis URL resolution honours both defaults and environment overrides.
    - Prove connection checks report success and failure correctly.
    - Prove temporary async clients are closed after use.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from faith_pa.utils import redis_client


class FakeAsyncClient:
    """Description:
        Provide a minimal async Redis client double for connection tests.

    Requirements:
        - Simulate both successful and failing ping behaviour.
        - Track whether the client has been closed.

    :param should_ping: Whether ``ping`` should succeed.
    """

    def __init__(self, should_ping: bool = True):
        """Description:
            Initialise the fake async Redis client.

        Requirements:
            - Preserve whether ``ping`` should succeed.
            - Start with the client marked as open.

        :param should_ping: Whether ``ping`` should succeed.
        """

        self.should_ping = should_ping
        self.closed = False

    async def ping(self) -> bool:
        """Description:
            Simulate an asynchronous Redis ``PING`` call.

        Requirements:
            - Raise an ``OSError`` when the fake client is configured to fail.

        :returns: ``True`` when the fake ping succeeds.
        :raises OSError: If the fake client is configured to fail.
        """

        if not self.should_ping:
            raise OSError("redis unavailable")
        return True

    async def aclose(self) -> None:
        """Description:
            Mark the fake async client as closed.

        Requirements:
            - Preserve the closed state for test assertions.
        """

        self.closed = True


class RedisClientTests(unittest.IsolatedAsyncioTestCase):
    """Description:
        Verify the Redis helper functions behave consistently for the runtime.

    Requirements:
        - Cover URL resolution, client creation, and connection checking.
    """

    def test_get_redis_url_defaults_to_local(self) -> None:
        """Description:
            Verify the Redis URL defaults to the internal FAITH service address.

        Requirements:
            - This test is needed to prove the runtime works without an explicit environment override.
            - Verify the default URL matches the expected internal Redis service address.
        """

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(redis_client.get_redis_url(), "redis://redis:6379/0")

    def test_get_redis_url_honors_env(self) -> None:
        """Description:
            Verify an explicit environment variable overrides the default Redis URL.

        Requirements:
            - This test is needed to prove deployment-specific Redis endpoints can be configured externally.
            - Verify the helper returns the exact environment-provided URL.
        """

        with patch.dict(os.environ, {"FAITH_REDIS_URL": "redis://example:6380/1"}, clear=True):
            self.assertEqual(redis_client.get_redis_url(), "redis://example:6380/1")

    def test_get_sync_client_uses_requested_url(self) -> None:
        """Description:
            Verify the synchronous client helper uses the caller-supplied Redis URL.

        Requirements:
            - This test is needed to prove callers can target non-default Redis endpoints.
            - Verify the resulting client is configured with the supplied host, port, and database.
        """

        client = redis_client.get_sync_client("redis://example:6381/2")
        self.assertEqual(client.connection_pool.connection_kwargs["host"], "example")
        self.assertEqual(client.connection_pool.connection_kwargs["port"], 6381)
        self.assertEqual(client.connection_pool.connection_kwargs["db"], 2)
        client.close()

    async def test_check_connection_succeeds(self) -> None:
        """Description:
            Verify the async connection check returns success when Redis responds.

        Requirements:
            - This test is needed to prove healthy Redis connections are reported correctly.
            - Verify a caller-supplied client is not closed by the helper.
        """

        client = FakeAsyncClient(should_ping=True)
        self.assertTrue(await redis_client.check_connection(client))
        self.assertFalse(client.closed)

    async def test_check_connection_closes_temporary_client(self) -> None:
        """Description:
            Verify the async connection check closes a temporary client it creates itself.

        Requirements:
            - This test is needed to prove helper-owned Redis clients do not leak.
            - Verify the created client is closed after the health check completes.
        """

        created_clients: list[FakeAsyncClient] = []

        async def fake_get_async_client(*args, **kwargs):
            """Description:
                Create one fake async client for the connection-check test.

            Requirements:
                - Append the created client so the test can inspect its closed state later.

            :returns: Fake async Redis client instance.
            """

            del args, kwargs
            client = FakeAsyncClient(should_ping=True)
            created_clients.append(client)
            return client

        with patch.object(redis_client, "get_async_client", side_effect=fake_get_async_client):
            self.assertTrue(await redis_client.check_connection())

        self.assertEqual(len(created_clients), 1)
        self.assertTrue(created_clients[0].closed)

    async def test_check_connection_handles_errors(self) -> None:
        """Description:
            Verify the async connection check reports failure when Redis is unavailable.

        Requirements:
            - This test is needed to prove runtime health checks fail closed on Redis errors.
            - Verify the helper returns ``False`` when ``ping`` raises an ``OSError``.
        """

        client = FakeAsyncClient(should_ping=False)
        self.assertFalse(await redis_client.check_connection(client))
        self.assertFalse(client.closed)


if __name__ == "__main__":
    unittest.main()
