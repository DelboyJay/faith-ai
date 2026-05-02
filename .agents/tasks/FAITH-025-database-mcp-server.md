# FAITH-025 — PostgreSQL Database MCP Server

**Phase:** 6 — Tool MCP Servers
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-003, FAITH-008
**FRS Reference:** Section 4.4

---

## Objective

External-first task. Prefer a public/external PostgreSQL MCP server in v1. Only implement a FAITH-owned database MCP server if external options cannot satisfy FAITH's permission validation, audit logging, declared-access enforcement, and per-agent access controls. In v1, database access is read-only by default; writes are allowed only when the connection is explicitly declared `readwrite` and the user approves each mutating action through FAITH's approval flow. `permission_override: true` only acknowledges a DB-role mismatch; it does not bypass declared access or per-action approval. If FAITH does implement its own fallback server later, it would expose named database connections defined in `.faith/tools/database.yaml`, enforce the same layered permission model, validate actual database role permissions on startup, apply per-agent access caps from `.faith/agents/{id}/config.yaml`, enforce row and data size limits on query results, and log every query to the audit log. Credentials are never stored in tool config — they are resolved via `secret_ref` from `config/secrets.yaml`.

Implementation note: the embedded in-house server sketches below are fallback material only. The normative v1 path is integration of an approved external PostgreSQL MCP server through FAITH-035.

---

## Architecture

```
faith/tools/database/
├── __init__.py
├── server.py          ← MCP server entrypoint and tool registration
├── connection.py      ← Connection pool manager and named connection registry
├── permissions.py     ← Access rule engine and permission validator
├── query.py           ← Query execution, result limiting, and truncation
└── config.py          ← Pydantic models for database tool configuration

faith/tools/database/models/
├── __init__.py
└── schemas.py         ← Request/response models for MCP tool calls

tests/
├── test_database_server.py
├── test_database_permissions.py
├── test_database_query.py
└── test_database_connection.py
```

---

## Files to Create

### 1. `faith/tools/database/config.py`

```python
"""Pydantic models for database tool configuration.

Parses `.faith/tools/database.yaml` into typed models. Credentials
are never stored here — only a `secret_ref` key that the secret
resolver (FAITH-003) uses to look up the actual password from
`config/secrets.yaml`.

FRS Reference: Section 4.4.1
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("faith.tools.database.config")


class AccessLevel(str, Enum):
    """Database connection access level."""
    READONLY = "readonly"
    READWRITE = "readwrite"


class ConnectionConfig(BaseModel):
    """Configuration for a single named database connection.

    Attributes:
        host: Database hostname.
        port: Database port (default 5432).
        database: Database name.
        user: Database username.
        secret_ref: Key in config/secrets.yaml for the password.
        access: Declared access level (default readonly).
        max_rows: Maximum rows returned per query (default 1000).
        max_result_mb: Maximum result size in MB (default 5).
        permission_override: User has acknowledged a permission
            mismatch between declared access and actual DB role.
        ssl_mode: PostgreSQL SSL mode (default "prefer").
    """
    host: str
    port: int = 5432
    database: str
    user: str
    secret_ref: str
    access: AccessLevel = AccessLevel.READONLY
    max_rows: int = Field(default=1000, ge=1, le=100_000)
    max_result_mb: float = Field(default=5.0, ge=0.1, le=100.0)
    permission_override: bool = False
    ssl_mode: str = "prefer"

    @field_validator("database")
    @classmethod
    def validate_database_name(cls, v: str) -> str:
        """Ensure database name is not empty."""
        if not v.strip():
            raise ValueError("Database name must not be empty")
        return v

    def is_test_database(self) -> bool:
        """Check if this is a test database (name contains 'test_').

        Test databases are auto-granted readwrite access regardless
        of the declared access level.
        """
        return "test_" in self.database

    def effective_access(self) -> AccessLevel:
        """Return the effective access level after applying rules.

        - test_* databases: always readwrite
        - Non-test databases: always readonly (production never writable)
        """
        if self.is_test_database():
            return AccessLevel.READWRITE
        # Production connections are NEVER writable, regardless of config
        return AccessLevel.READONLY


class AgentDatabaseAccess(BaseModel):
    """Per-agent database access from .faith/agents/{id}/config.yaml.

    Attributes:
        connection_name: The named connection to grant access to.
        access: The agent's requested access level.
    """
    connection_name: str
    access: AccessLevel = AccessLevel.READONLY


class DatabaseToolConfig(BaseModel):
    """Top-level database tool configuration.

    Parsed from `.faith/tools/database.yaml`.

    Attributes:
        connections: Map of connection name to connection config.
    """
    connections: dict[str, ConnectionConfig] = Field(default_factory=dict)
```

### 2. `faith/tools/database/connection.py`

```python
"""Connection pool manager for named PostgreSQL connections.

Manages asyncpg connection pools for each named connection defined
in `.faith/tools/database.yaml`. Passwords are resolved from
`config/secrets.yaml` via the secret_ref mechanism (FAITH-003).

FRS Reference: Section 4.4.1, 4.4.3
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import asyncpg

from faith.tools.database.config import (
    AccessLevel,
    ConnectionConfig,
    DatabaseToolConfig,
)

logger = logging.getLogger("faith.tools.database.connection")


class ConnectionManager:
    """Manages asyncpg connection pools for named database connections.

    Attributes:
        config: The parsed database tool configuration.
        _pools: Map of connection name to asyncpg pool.
        _secrets: Resolved secrets dict (secret_ref -> password).
    """

    def __init__(
        self,
        config: DatabaseToolConfig,
        secrets: dict[str, str],
    ):
        """Initialise the connection manager.

        Args:
            config: Parsed database tool configuration.
            secrets: Dict mapping secret_ref keys to resolved passwords.
        """
        self.config = config
        self._secrets = secrets
        self._pools: dict[str, asyncpg.Pool] = {}

    async def initialise(self) -> None:
        """Create connection pools for all configured connections.

        Called once on server startup. Raises on failure so the
        server can report the issue and exit cleanly.
        """
        for name, conn_config in self.config.connections.items():
            password = self._secrets.get(conn_config.secret_ref)
            if password is None:
                raise ValueError(
                    f"Connection '{name}': secret_ref '{conn_config.secret_ref}' "
                    f"not found in config/secrets.yaml"
                )

            dsn = (
                f"postgresql://{conn_config.user}:{password}"
                f"@{conn_config.host}:{conn_config.port}"
                f"/{conn_config.database}"
                f"?sslmode={conn_config.ssl_mode}"
            )

            try:
                pool = await asyncpg.create_pool(
                    dsn,
                    min_size=1,
                    max_size=5,
                    command_timeout=30.0,
                )
                self._pools[name] = pool
                logger.info(
                    f"Connection pool created for '{name}' "
                    f"({conn_config.host}:{conn_config.port}/"
                    f"{conn_config.database})"
                )
            except Exception as e:
                logger.error(
                    f"Failed to create pool for connection '{name}': {e}"
                )
                raise

    async def get_connection(
        self, connection_name: str
    ) -> asyncpg.pool.PoolConnectionProxy:
        """Acquire a connection from a named pool.

        Args:
            connection_name: The named connection to acquire from.

        Returns:
            An asyncpg connection proxy.

        Raises:
            KeyError: If the connection name is not configured.
        """
        if connection_name not in self._pools:
            raise KeyError(
                f"Unknown connection '{connection_name}'. "
                f"Available: {list(self._pools.keys())}"
            )
        return await self._pools[connection_name].acquire()

    async def release_connection(
        self, connection_name: str, conn: asyncpg.pool.PoolConnectionProxy
    ) -> None:
        """Release a connection back to its pool.

        Args:
            connection_name: The named connection the conn belongs to.
            conn: The connection to release.
        """
        if connection_name in self._pools:
            await self._pools[connection_name].release(conn)

    def get_config(self, connection_name: str) -> ConnectionConfig:
        """Get the configuration for a named connection.

        Args:
            connection_name: The connection name.

        Returns:
            The ConnectionConfig for this connection.

        Raises:
            KeyError: If the connection name is not configured.
        """
        if connection_name not in self.config.connections:
            raise KeyError(f"Unknown connection '{connection_name}'")
        return self.config.connections[connection_name]

    async def close_all(self) -> None:
        """Close all connection pools. Called on server shutdown."""
        for name, pool in self._pools.items():
            try:
                await pool.close()
                logger.info(f"Connection pool closed for '{name}'")
            except Exception as e:
                logger.warning(f"Error closing pool for '{name}': {e}")
        self._pools.clear()
```

### 3. `faith/tools/database/permissions.py`

```python
"""Permission validation for database connections.

Enforces the layered permission model:
1. Connection-level access from .faith/tools/database.yaml
2. Test database auto-grant (test_* -> readwrite)
3. Production never writable
4. Agent-level cap from .faith/agents/{id}/config.yaml
5. Startup validation: actual DB role vs declared access

FRS Reference: Section 4.4.2, 4.4.3, 4.4.4
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from faith.tools.database.config import (
    AccessLevel,
    AgentDatabaseAccess,
    ConnectionConfig,
)

logger = logging.getLogger("faith.tools.database.permissions")


class PermissionMismatchError(Exception):
    """Raised when actual DB role permissions exceed declared access.

    This error triggers a user alert via the Web UI approval panel.
    The user must set `permission_override: true` on the connection
    to acknowledge the mismatch and proceed.
    """

    def __init__(self, connection_name: str, declared: str, actual: str):
        self.connection_name = connection_name
        self.declared = declared
        self.actual = actual
        super().__init__(
            f"Connection '{connection_name}': declared access is "
            f"'{declared}' but DB role has '{actual}' permissions. "
            f"Set 'permission_override: true' to acknowledge."
        )


class PermissionDeniedError(Exception):
    """Raised when an agent attempts an operation beyond its access level."""
    pass


class PermissionValidator:
    """Validates database permissions at startup and per-query.

    Attributes:
        _agent_access: Map of agent_id -> {connection_name: AccessLevel}.
    """

    def __init__(self):
        self._agent_access: dict[str, dict[str, AccessLevel]] = {}

    def register_agent_access(
        self,
        agent_id: str,
        databases: dict[str, str],
        connection_configs: dict[str, ConnectionConfig],
    ) -> None:
        """Register an agent's database access from its config.yaml.

        The agent's requested access is capped at the connection's
        effective access level (the agent cannot exceed the connection
        permission).

        Args:
            agent_id: The agent identifier.
            databases: Map of connection_name -> requested access level
                from .faith/agents/{id}/config.yaml.
            connection_configs: All configured connections.

        Raises:
            KeyError: If the agent references an unknown connection.
        """
        resolved: dict[str, AccessLevel] = {}

        for conn_name, requested_access_str in databases.items():
            if conn_name not in connection_configs:
                raise KeyError(
                    f"Agent '{agent_id}' references unknown connection "
                    f"'{conn_name}'"
                )

            conn_config = connection_configs[conn_name]
            connection_effective = conn_config.effective_access()
            requested = AccessLevel(requested_access_str)

            # Agent cap: cannot exceed connection-level permission
            if (
                requested == AccessLevel.READWRITE
                and connection_effective == AccessLevel.READONLY
            ):
                logger.warning(
                    f"Agent '{agent_id}' requested readwrite on "
                    f"'{conn_name}' but connection is readonly — "
                    f"capping to readonly"
                )
                resolved[conn_name] = AccessLevel.READONLY
            else:
                resolved[conn_name] = requested

        self._agent_access[agent_id] = resolved
        logger.info(
            f"Registered database access for agent '{agent_id}': "
            f"{resolved}"
        )

    def check_agent_access(
        self,
        agent_id: str,
        connection_name: str,
        requires_write: bool = False,
    ) -> None:
        """Check if an agent has access to a connection.

        Args:
            agent_id: The agent identifier.
            connection_name: The connection to check.
            requires_write: True if the operation requires write access.

        Raises:
            PermissionDeniedError: If the agent lacks access.
        """
        agent_conns = self._agent_access.get(agent_id)
        if agent_conns is None:
            raise PermissionDeniedError(
                f"Agent '{agent_id}' has no database access configured"
            )

        if connection_name not in agent_conns:
            raise PermissionDeniedError(
                f"Agent '{agent_id}' does not have access to "
                f"connection '{connection_name}'"
            )

        agent_level = agent_conns[connection_name]
        if requires_write and agent_level == AccessLevel.READONLY:
            raise PermissionDeniedError(
                f"Agent '{agent_id}' has readonly access to "
                f"'{connection_name}' — write operation denied"
            )

    async def validate_connection_permissions(
        self,
        connection_name: str,
        conn_config: ConnectionConfig,
        conn: asyncpg.Connection,
    ) -> None:
        """Validate actual DB role permissions against declared access.

        Queries PostgreSQL system catalogs to determine what the
        connection's user role can actually do, then compares against
        the declared access level in tools.yaml.

        If the actual permissions are MORE permissive than declared
        and `permission_override` is not set, raises
        PermissionMismatchError.

        Args:
            connection_name: The named connection being validated.
            conn_config: The connection's configuration.
            conn: An active asyncpg connection to query against.

        Raises:
            PermissionMismatchError: If actual > declared and no override.
        """
        has_write = await self._check_role_has_write(
            conn, conn_config.user, conn_config.database
        )

        declared = conn_config.effective_access()

        if has_write and declared == AccessLevel.READONLY:
            if conn_config.permission_override:
                logger.warning(
                    f"Connection '{connection_name}': DB role "
                    f"'{conn_config.user}' has write access but "
                    f"declared readonly — proceeding due to "
                    f"permission_override=true"
                )
            else:
                raise PermissionMismatchError(
                    connection_name=connection_name,
                    declared="readonly",
                    actual="readwrite",
                )

        logger.info(
            f"Permission validation passed for connection "
            f"'{connection_name}' (declared={declared.value}, "
            f"role_has_write={has_write})"
        )

    async def _check_role_has_write(
        self,
        conn: asyncpg.Connection,
        username: str,
        database: str,
    ) -> bool:
        """Query PostgreSQL to determine if a role has write privileges.

        Checks for INSERT, UPDATE, or DELETE grants on any table in
        the public schema.

        Args:
            conn: Active database connection.
            username: The PostgreSQL role name.
            database: The database name.

        Returns:
            True if the role has any write privileges.
        """
        query = """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.role_table_grants
                WHERE grantee = $1
                  AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE')
                  AND table_catalog = $2
            )
        """
        result = await conn.fetchval(query, username, database)
        return bool(result)
```

### 4. `faith/tools/database/query.py`

```python
"""Query execution with result limiting and truncation.

Executes SQL queries against named connections, enforcing:
- Read-only mode for non-test connections (SET TRANSACTION READ ONLY)
- Row limits (default 1000)
- Data size limits (default 5MB)
- Truncation flagging

FRS Reference: Section 4.4.5, 4.4.6
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Optional

import asyncpg

from faith.tools.database.config import AccessLevel, ConnectionConfig

logger = logging.getLogger("faith.tools.database.query")

# 5MB default data size limit
DEFAULT_MAX_RESULT_BYTES = 5 * 1024 * 1024


class QueryResult:
    """Structured query result with truncation metadata.

    Attributes:
        columns: List of column names.
        rows: List of row dicts.
        row_count: Number of rows returned (after truncation).
        total_row_count: Total rows matched (before truncation).
        truncated: Whether the result was truncated.
        truncation_reason: Why the result was truncated (if applicable).
        execution_time_ms: Query execution time in milliseconds.
        data_size_bytes: Approximate size of the result data.
    """

    def __init__(
        self,
        columns: list[str],
        rows: list[dict[str, Any]],
        row_count: int,
        total_row_count: int,
        truncated: bool,
        truncation_reason: Optional[str],
        execution_time_ms: float,
        data_size_bytes: int,
    ):
        self.columns = columns
        self.rows = rows
        self.row_count = row_count
        self.total_row_count = total_row_count
        self.truncated = truncated
        self.truncation_reason = truncation_reason
        self.execution_time_ms = execution_time_ms
        self.data_size_bytes = data_size_bytes

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for MCP response."""
        result = {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "execution_time_ms": round(self.execution_time_ms, 2),
        }
        if self.truncated:
            result["truncated"] = True
            result["total_row_count"] = self.total_row_count
            if self.truncation_reason:
                result["truncation_reason"] = self.truncation_reason
        return result


class QueryExecutor:
    """Executes queries with result limiting and audit logging.

    Enforces read-only mode at the driver level for non-test
    connections by wrapping queries in a READ ONLY transaction.
    """

    def __init__(self, audit_logger: Any = None):
        """Initialise the query executor.

        Args:
            audit_logger: Audit log writer (FAITH-021). If None,
                audit entries are logged via the standard logger
                as a fallback.
        """
        self._audit_logger = audit_logger

    async def execute(
        self,
        conn: asyncpg.Connection,
        conn_config: ConnectionConfig,
        connection_name: str,
        query: str,
        agent_id: str,
        params: Optional[list[Any]] = None,
    ) -> QueryResult:
        """Execute a SQL query with limits and audit logging.

        For readonly connections, the query is wrapped in a
        READ ONLY transaction at the driver level.

        Args:
            conn: Active asyncpg connection.
            conn_config: Configuration for this connection.
            connection_name: The named connection identifier.
            query: SQL query text.
            agent_id: The agent executing the query.
            params: Optional query parameters.

        Returns:
            QueryResult with rows, truncation metadata, and timing.

        Raises:
            PermissionError: If a write query is attempted on a
                readonly connection.
        """
        effective_access = conn_config.effective_access()
        max_rows = conn_config.max_rows
        max_bytes = int(conn_config.max_result_mb * 1024 * 1024)

        # Detect write operations on readonly connections
        if effective_access == AccessLevel.READONLY:
            normalised = query.strip().upper()
            if normalised.startswith(("INSERT", "UPDATE", "DELETE", "DROP",
                                      "ALTER", "CREATE", "TRUNCATE")):
                raise PermissionError(
                    f"Write operation denied on readonly connection "
                    f"'{connection_name}'"
                )

        start_time = time.monotonic()

        try:
            if effective_access == AccessLevel.READONLY:
                # Enforce read-only at the driver level
                result = await self._execute_readonly(
                    conn, query, params, max_rows
                )
            else:
                result = await self._execute_readwrite(
                    conn, query, params, max_rows
                )
        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            await self._log_audit(
                agent_id=agent_id,
                connection_name=connection_name,
                query=query,
                row_count=0,
                execution_time_ms=elapsed_ms,
                truncated=False,
                error=str(e),
            )
            raise

        elapsed_ms = (time.monotonic() - start_time) * 1000

        # Process results
        if not result:
            query_result = QueryResult(
                columns=[],
                rows=[],
                row_count=0,
                total_row_count=0,
                truncated=False,
                truncation_reason=None,
                execution_time_ms=elapsed_ms,
                data_size_bytes=0,
            )
        else:
            columns = list(result[0].keys()) if result else []
            rows = [dict(row) for row in result]
            total_row_count = len(rows)
            truncated = False
            truncation_reason = None

            # Apply row limit
            if len(rows) > max_rows:
                rows = rows[:max_rows]
                truncated = True
                truncation_reason = (
                    f"Row limit exceeded: {total_row_count} rows "
                    f"truncated to {max_rows}"
                )

            # Apply data size limit
            data_size = self._estimate_size(rows)
            if data_size > max_bytes:
                rows, data_size = self._truncate_by_size(
                    rows, max_bytes
                )
                truncated = True
                truncation_reason = (
                    f"Data size limit exceeded: result truncated to "
                    f"fit within {conn_config.max_result_mb}MB"
                )

            query_result = QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                total_row_count=total_row_count,
                truncated=truncated,
                truncation_reason=truncation_reason,
                execution_time_ms=elapsed_ms,
                data_size_bytes=data_size,
            )

        # Audit log
        await self._log_audit(
            agent_id=agent_id,
            connection_name=connection_name,
            query=query,
            row_count=query_result.row_count,
            execution_time_ms=elapsed_ms,
            truncated=query_result.truncated,
        )

        return query_result

    async def _execute_readonly(
        self,
        conn: asyncpg.Connection,
        query: str,
        params: Optional[list[Any]],
        max_rows: int,
    ) -> list[asyncpg.Record]:
        """Execute a query inside a READ ONLY transaction.

        This is the driver-level enforcement of read-only mode
        for non-test connections. Even if the SQL parser misses
        a write statement, PostgreSQL will reject it.
        """
        async with conn.transaction(readonly=True):
            # Fetch max_rows + 1 to detect truncation
            if params:
                return await conn.fetch(query, *params)
            return await conn.fetch(query)

    async def _execute_readwrite(
        self,
        conn: asyncpg.Connection,
        query: str,
        params: Optional[list[Any]],
        max_rows: int,
    ) -> list[asyncpg.Record]:
        """Execute a query with full read-write access (test DBs only)."""
        if params:
            return await conn.fetch(query, *params)
        return await conn.fetch(query)

    def _estimate_size(self, rows: list[dict[str, Any]]) -> int:
        """Estimate the serialized size of result rows in bytes."""
        return len(json.dumps(rows, default=str).encode("utf-8"))

    def _truncate_by_size(
        self,
        rows: list[dict[str, Any]],
        max_bytes: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Truncate rows to fit within the data size limit.

        Uses binary search on the row count to find the largest
        subset that fits.
        """
        low, high = 0, len(rows)
        best = 0
        best_size = 0

        while low <= high:
            mid = (low + high) // 2
            subset = rows[:mid]
            size = self._estimate_size(subset)

            if size <= max_bytes:
                best = mid
                best_size = size
                low = mid + 1
            else:
                high = mid - 1

        return rows[:best], best_size

    async def _log_audit(
        self,
        agent_id: str,
        connection_name: str,
        query: str,
        row_count: int,
        execution_time_ms: float,
        truncated: bool,
        error: Optional[str] = None,
    ) -> None:
        """Log query execution to the audit log.

        Uses the audit logger from FAITH-021 if available,
        otherwise falls back to standard logging.

        Fields logged: timestamp, agent, connection name, query text,
        row count returned, execution time (ms), truncated flag.
        """
        audit_entry = {
            "tool": "database",
            "agent": agent_id,
            "connection": connection_name,
            "query": query,
            "row_count": row_count,
            "execution_time_ms": round(execution_time_ms, 2),
            "truncated": truncated,
        }
        if error:
            audit_entry["error"] = error

        if self._audit_logger is not None:
            await self._audit_logger.log_tool_operation(audit_entry)
        else:
            logger.info(f"AUDIT: {audit_entry}")
```

### 5. `faith/tools/database/server.py`

```python
"""PostgreSQL Database MCP Tool Server.

Exposes database query capabilities to FAITH agents via the MCP
protocol. In the fallback FAITH-owned implementation, it would run
inside a dedicated project-scoped database tool container.

MCP Tools:
- db_query: Execute a SQL query against a named connection.
- db_list_connections: List available connections for the calling agent.
- db_describe_table: Get column metadata for a table.
- db_list_tables: List tables in a named connection's database.

FRS Reference: Section 4.4
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from faith.mcp.server import MCPServer, MCPTool, MCPToolResult
from faith.tools.database.config import (
    AccessLevel,
    DatabaseToolConfig,
)
from faith.tools.database.connection import ConnectionManager
from faith.tools.database.permissions import (
    PermissionDeniedError,
    PermissionMismatchError,
    PermissionValidator,
)
from faith.tools.database.query import QueryExecutor

logger = logging.getLogger("faith.tools.database.server")


class DatabaseMCPServer(MCPServer):
    """MCP server for PostgreSQL database operations.

    Lifecycle:
    1. Load config from .faith/tools/database.yaml
    2. Resolve credentials via secret_ref from config/secrets.yaml
    3. Create connection pools for all named connections
    4. Validate permissions on startup (actual DB role vs declared)
    5. Register agent access from .faith/agents/{id}/config.yaml
    6. Serve MCP tool calls

    Attributes:
        config: Parsed database tool configuration.
        connection_manager: Manages named connection pools.
        permission_validator: Enforces the layered permission model.
        query_executor: Executes queries with limits and audit.
    """

    def __init__(
        self,
        faith_dir: Path,
        secrets: dict[str, str],
        audit_logger: Any = None,
    ):
        """Initialise the database MCP server.

        Args:
            faith_dir: Path to the .faith directory.
            secrets: Resolved secrets dict (secret_ref -> password).
            audit_logger: Audit log writer (FAITH-021).
        """
        super().__init__(name="database")
        self.faith_dir = faith_dir
        self._secrets = secrets

        # Load tool config
        config_path = faith_dir / "tools" / "database.yaml"
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.config = DatabaseToolConfig(**raw.get("database", raw))

        # Initialise components
        self.connection_manager = ConnectionManager(self.config, secrets)
        self.permission_validator = PermissionValidator()
        self.query_executor = QueryExecutor(audit_logger=audit_logger)

        # Register MCP tools
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all MCP tools exposed by this server."""
        self.register_tool(MCPTool(
            name="db_query",
            description=(
                "Execute a SQL query against a named database connection. "
                "Results are limited by row count and data size."
            ),
            parameters={
                "connection": {
                    "type": "string",
                    "description": "Named connection from database.yaml",
                },
                "query": {
                    "type": "string",
                    "description": "SQL query to execute",
                },
                "params": {
                    "type": "array",
                    "description": "Optional query parameters",
                    "items": {"type": "string"},
                    "default": [],
                },
            },
            required=["connection", "query"],
            handler=self._handle_db_query,
        ))

        self.register_tool(MCPTool(
            name="db_list_connections",
            description=(
                "List database connections available to the calling agent."
            ),
            parameters={},
            required=[],
            handler=self._handle_list_connections,
        ))

        self.register_tool(MCPTool(
            name="db_list_tables",
            description=(
                "List all tables in a named connection's database."
            ),
            parameters={
                "connection": {
                    "type": "string",
                    "description": "Named connection from database.yaml",
                },
            },
            required=["connection"],
            handler=self._handle_list_tables,
        ))

        self.register_tool(MCPTool(
            name="db_describe_table",
            description=(
                "Get column names, types, and constraints for a table."
            ),
            parameters={
                "connection": {
                    "type": "string",
                    "description": "Named connection from database.yaml",
                },
                "table": {
                    "type": "string",
                    "description": "Table name (optionally schema-qualified)",
                },
            },
            required=["connection", "table"],
            handler=self._handle_describe_table,
        ))

    async def startup(self) -> None:
        """Server startup: create pools, validate permissions, register agents.

        Called once before the server begins accepting MCP requests.

        Raises:
            PermissionMismatchError: If a connection's actual DB role
                permissions exceed declared access and no override is set.
        """
        # 1. Create connection pools
        await self.connection_manager.initialise()

        # 2. Validate permissions on each connection
        for name, conn_config in self.config.connections.items():
            conn = await self.connection_manager.get_connection(name)
            try:
                await self.permission_validator.validate_connection_permissions(
                    connection_name=name,
                    conn_config=conn_config,
                    conn=conn,
                )
            finally:
                await self.connection_manager.release_connection(name, conn)

        # 3. Register per-agent access from agent config files
        agents_dir = self.faith_dir / "agents"
        if agents_dir.exists():
            for agent_dir in agents_dir.iterdir():
                if not agent_dir.is_dir():
                    continue
                config_path = agent_dir / "config.yaml"
                if not config_path.exists():
                    continue
                agent_config = yaml.safe_load(
                    config_path.read_text(encoding="utf-8")
                )
                databases = agent_config.get("databases", {})
                if databases:
                    self.permission_validator.register_agent_access(
                        agent_id=agent_dir.name,
                        databases=databases,
                        connection_configs=self.config.connections,
                    )

        logger.info(
            f"Database MCP server started with "
            f"{len(self.config.connections)} connection(s)"
        )

    async def shutdown(self) -> None:
        """Server shutdown: close all connection pools."""
        await self.connection_manager.close_all()
        logger.info("Database MCP server shut down")

    async def _handle_db_query(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> MCPToolResult:
        """Handle the db_query MCP tool call.

        Args:
            params: Tool parameters (connection, query, params).
            context: MCP context (includes agent_id).

        Returns:
            MCPToolResult with query results or error.
        """
        agent_id = context.get("agent_id", "unknown")
        connection_name = params["connection"]
        query = params["query"]
        query_params = params.get("params")

        # Check agent access
        try:
            requires_write = self._is_write_query(query)
            self.permission_validator.check_agent_access(
                agent_id=agent_id,
                connection_name=connection_name,
                requires_write=requires_write,
            )
        except PermissionDeniedError as e:
            return MCPToolResult(error=str(e))

        # Execute query
        conn_config = self.connection_manager.get_config(connection_name)
        conn = await self.connection_manager.get_connection(connection_name)
        try:
            result = await self.query_executor.execute(
                conn=conn,
                conn_config=conn_config,
                connection_name=connection_name,
                query=query,
                agent_id=agent_id,
                params=query_params,
            )
            return MCPToolResult(data=result.to_dict())
        except PermissionError as e:
            return MCPToolResult(error=str(e))
        except Exception as e:
            logger.error(
                f"Query error on '{connection_name}': {e}", exc_info=True
            )
            return MCPToolResult(error=f"Query execution failed: {e}")
        finally:
            await self.connection_manager.release_connection(
                connection_name, conn
            )

    async def _handle_list_connections(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> MCPToolResult:
        """List connections available to the calling agent."""
        agent_id = context.get("agent_id", "unknown")
        agent_conns = self.permission_validator._agent_access.get(
            agent_id, {}
        )

        connections = []
        for name, access in agent_conns.items():
            conn_config = self.config.connections.get(name)
            if conn_config:
                connections.append({
                    "name": name,
                    "database": conn_config.database,
                    "access": access.value,
                    "max_rows": conn_config.max_rows,
                    "max_result_mb": conn_config.max_result_mb,
                })

        return MCPToolResult(data={"connections": connections})

    async def _handle_list_tables(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> MCPToolResult:
        """List tables in a named connection's database."""
        agent_id = context.get("agent_id", "unknown")
        connection_name = params["connection"]

        try:
            self.permission_validator.check_agent_access(
                agent_id=agent_id,
                connection_name=connection_name,
            )
        except PermissionDeniedError as e:
            return MCPToolResult(error=str(e))

        conn_config = self.connection_manager.get_config(connection_name)
        conn = await self.connection_manager.get_connection(connection_name)
        try:
            rows = await conn.fetch(
                """
                SELECT table_schema, table_name, table_type
                FROM information_schema.tables
                WHERE table_schema NOT IN (
                    'information_schema', 'pg_catalog'
                )
                ORDER BY table_schema, table_name
                """
            )
            tables = [
                {
                    "schema": row["table_schema"],
                    "name": row["table_name"],
                    "type": row["table_type"],
                }
                for row in rows
            ]
            return MCPToolResult(data={"tables": tables})
        except Exception as e:
            return MCPToolResult(error=f"Failed to list tables: {e}")
        finally:
            await self.connection_manager.release_connection(
                connection_name, conn
            )

    async def _handle_describe_table(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> MCPToolResult:
        """Get column metadata for a table."""
        agent_id = context.get("agent_id", "unknown")
        connection_name = params["connection"]
        table = params["table"]

        try:
            self.permission_validator.check_agent_access(
                agent_id=agent_id,
                connection_name=connection_name,
            )
        except PermissionDeniedError as e:
            return MCPToolResult(error=str(e))

        # Parse schema.table if provided
        schema = "public"
        table_name = table
        if "." in table:
            schema, table_name = table.split(".", 1)

        conn = await self.connection_manager.get_connection(connection_name)
        try:
            rows = await conn.fetch(
                """
                SELECT
                    column_name,
                    data_type,
                    is_nullable,
                    column_default,
                    character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = $1
                  AND table_name = $2
                ORDER BY ordinal_position
                """,
                schema,
                table_name,
            )
            columns = [
                {
                    "name": row["column_name"],
                    "type": row["data_type"],
                    "nullable": row["is_nullable"] == "YES",
                    "default": row["column_default"],
                    "max_length": row["character_maximum_length"],
                }
                for row in rows
            ]
            return MCPToolResult(data={"table": table, "columns": columns})
        except Exception as e:
            return MCPToolResult(error=f"Failed to describe table: {e}")
        finally:
            await self.connection_manager.release_connection(
                connection_name, conn
            )

    @staticmethod
    def _is_write_query(query: str) -> bool:
        """Heuristic check for write operations in SQL."""
        normalised = query.strip().upper()
        return normalised.startswith((
            "INSERT", "UPDATE", "DELETE", "DROP",
            "ALTER", "CREATE", "TRUNCATE",
        ))
```

### 6. `faith/tools/database/__init__.py`

```python
"""FAITH Database MCP Tool Server — PostgreSQL query access for agents."""

from faith.tools.database.server import DatabaseMCPServer

__all__ = ["DatabaseMCPServer"]
```

### 7. `faith/tools/database/models/__init__.py`

```python
"""Database tool request/response models."""
```

### 8. `faith/tools/database/models/schemas.py`

```python
"""Request and response schemas for database MCP tool calls.

These models define the structured data returned by the database
tool's MCP endpoints.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Request schema for db_query tool."""
    connection: str
    query: str
    params: list[Any] = Field(default_factory=list)


class QueryResponse(BaseModel):
    """Response schema for db_query tool."""
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    total_row_count: Optional[int] = None
    truncated: bool = False
    truncation_reason: Optional[str] = None
    execution_time_ms: float = 0.0


class ConnectionInfo(BaseModel):
    """A single connection summary for db_list_connections."""
    name: str
    database: str
    access: str
    max_rows: int
    max_result_mb: float


class TableInfo(BaseModel):
    """A single table summary for db_list_tables."""
    schema_name: str = Field(alias="schema")
    name: str
    type: str


class ColumnInfo(BaseModel):
    """A single column summary for db_describe_table."""
    name: str
    type: str
    nullable: bool
    default: Optional[str] = None
    max_length: Optional[int] = None
```

### 9. `tests/test_database_permissions.py`

```python
"""Tests for database permission validation.

Covers the layered permission model: connection-level access,
test database auto-grant, production write block, agent caps,
and startup permission validation.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from faith.tools.database.config import (
    AccessLevel,
    ConnectionConfig,
)
from faith.tools.database.permissions import (
    PermissionDeniedError,
    PermissionMismatchError,
    PermissionValidator,
)


# ──────────────────────────────────────────────────
# Connection effective access tests
# ──────────────────────────────────────────────────


def _make_conn_config(**overrides) -> ConnectionConfig:
    """Helper to create a ConnectionConfig with defaults."""
    defaults = {
        "host": "localhost",
        "port": 5432,
        "database": "myapp",
        "user": "agent_user",
        "secret_ref": "db_password",
    }
    defaults.update(overrides)
    return ConnectionConfig(**defaults)


def test_default_access_is_readonly():
    """Connections default to readonly access."""
    config = _make_conn_config()
    assert config.effective_access() == AccessLevel.READONLY


def test_test_database_auto_granted_readwrite():
    """Databases with test_ in name are auto-granted readwrite."""
    config = _make_conn_config(database="test_myapp")
    assert config.effective_access() == AccessLevel.READWRITE


def test_test_database_readwrite_even_if_declared_readonly():
    """test_* databases are readwrite regardless of declared access."""
    config = _make_conn_config(
        database="test_myapp", access="readonly"
    )
    assert config.effective_access() == AccessLevel.READWRITE


def test_production_never_writable():
    """Non-test databases are always readonly, even if declared readwrite."""
    config = _make_conn_config(
        database="production_app", access="readwrite"
    )
    assert config.effective_access() == AccessLevel.READONLY


def test_is_test_database_detection():
    """test_ substring detection in database name."""
    assert _make_conn_config(database="test_app").is_test_database()
    assert _make_conn_config(database="my_test_db").is_test_database()
    assert not _make_conn_config(database="production").is_test_database()
    assert not _make_conn_config(database="testing").is_test_database()


# ──────────────────────────────────────────────────
# Agent access registration and checking
# ──────────────────────────────────────────────────


def test_register_agent_access():
    """Agent access is registered and retrievable."""
    validator = PermissionValidator()
    configs = {
        "test-db": _make_conn_config(database="test_myapp"),
    }
    validator.register_agent_access(
        "dev-agent", {"test-db": "readwrite"}, configs
    )
    # Should not raise
    validator.check_agent_access("dev-agent", "test-db", requires_write=True)


def test_agent_access_capped_at_connection_level():
    """Agent cannot exceed connection's effective access."""
    validator = PermissionValidator()
    configs = {
        "prod-db": _make_conn_config(database="production"),
    }
    validator.register_agent_access(
        "dev-agent", {"prod-db": "readwrite"}, configs
    )
    # Agent requested readwrite but connection is readonly —
    # should be capped to readonly
    with pytest.raises(PermissionDeniedError):
        validator.check_agent_access(
            "dev-agent", "prod-db", requires_write=True
        )


def test_agent_without_access_denied():
    """Agent with no configured access is denied."""
    validator = PermissionValidator()
    with pytest.raises(PermissionDeniedError):
        validator.check_agent_access("unknown-agent", "prod-db")


def test_agent_unknown_connection_denied():
    """Agent accessing unconfigured connection is denied."""
    validator = PermissionValidator()
    configs = {
        "test-db": _make_conn_config(database="test_myapp"),
    }
    validator.register_agent_access(
        "dev-agent", {"test-db": "readonly"}, configs
    )
    with pytest.raises(PermissionDeniedError):
        validator.check_agent_access("dev-agent", "other-db")


def test_agent_references_unknown_connection_raises():
    """Registering agent with unknown connection name raises KeyError."""
    validator = PermissionValidator()
    with pytest.raises(KeyError):
        validator.register_agent_access(
            "dev-agent", {"nonexistent": "readonly"}, {}
        )


def test_readonly_agent_cannot_write():
    """Agent with readonly access is denied write operations."""
    validator = PermissionValidator()
    configs = {
        "test-db": _make_conn_config(database="test_myapp"),
    }
    validator.register_agent_access(
        "dev-agent", {"test-db": "readonly"}, configs
    )
    with pytest.raises(PermissionDeniedError):
        validator.check_agent_access(
            "dev-agent", "test-db", requires_write=True
        )


# ──────────────────────────────────────────────────
# Startup permission validation
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permission_validation_passes_when_aligned():
    """Validation passes when DB role matches declared access."""
    validator = PermissionValidator()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=False)  # no write grants

    config = _make_conn_config(access="readonly")
    await validator.validate_connection_permissions(
        "prod-db", config, conn
    )
    # No exception means pass


@pytest.mark.asyncio
async def test_permission_validation_fails_on_mismatch():
    """Validation fails when DB role has more permissions than declared."""
    validator = PermissionValidator()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # has write grants

    config = _make_conn_config(access="readonly")
    with pytest.raises(PermissionMismatchError):
        await validator.validate_connection_permissions(
            "prod-db", config, conn
        )


@pytest.mark.asyncio
async def test_permission_validation_override_allows_mismatch():
    """permission_override=true allows mismatched permissions."""
    validator = PermissionValidator()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # has write grants

    config = _make_conn_config(
        access="readonly", permission_override=True
    )
    # Should not raise
    await validator.validate_connection_permissions(
        "prod-db", config, conn
    )
```

### 10. `tests/test_database_query.py`

```python
"""Tests for query execution, result limiting, and truncation.

Covers row limits, data size limits, truncation flagging,
read-only enforcement, and audit logging.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from faith.tools.database.config import (
    AccessLevel,
    ConnectionConfig,
)
from faith.tools.database.query import QueryExecutor, QueryResult


def _make_conn_config(**overrides) -> ConnectionConfig:
    """Helper to create a ConnectionConfig with defaults."""
    defaults = {
        "host": "localhost",
        "port": 5432,
        "database": "test_myapp",
        "user": "agent_user",
        "secret_ref": "db_password",
        "access": "readwrite",
        "max_rows": 10,
        "max_result_mb": 1.0,
    }
    defaults.update(overrides)
    return ConnectionConfig(**defaults)


def _make_fake_records(count: int) -> list:
    """Create fake asyncpg-like records."""
    records = []
    for i in range(count):
        record = MagicMock()
        record.keys.return_value = ["id", "name"]
        record.__getitem__ = lambda self, key, i=i: {
            "id": i, "name": f"row_{i}"
        }[key]
        record.items.return_value = [("id", i), ("name", f"row_{i}")]
        # Make dict(record) work
        records.append(record)
    return records


class FakeRecord(dict):
    """Fake asyncpg.Record that supports dict() conversion."""
    def keys(self):
        return super().keys()


def make_records(count: int) -> list[FakeRecord]:
    """Create fake records that behave like asyncpg Records."""
    return [
        FakeRecord(id=i, name=f"row_{i}")
        for i in range(count)
    ]


# ──────────────────────────────────────────────────
# QueryResult tests
# ──────────────────────────────────────────────────


def test_query_result_to_dict_no_truncation():
    """Non-truncated result serializes correctly."""
    result = QueryResult(
        columns=["id", "name"],
        rows=[{"id": 1, "name": "test"}],
        row_count=1,
        total_row_count=1,
        truncated=False,
        truncation_reason=None,
        execution_time_ms=5.0,
        data_size_bytes=50,
    )
    d = result.to_dict()
    assert d["row_count"] == 1
    assert "truncated" not in d
    assert "total_row_count" not in d


def test_query_result_to_dict_with_truncation():
    """Truncated result includes truncation metadata."""
    result = QueryResult(
        columns=["id"],
        rows=[{"id": i} for i in range(10)],
        row_count=10,
        total_row_count=5000,
        truncated=True,
        truncation_reason="Row limit exceeded",
        execution_time_ms=12.5,
        data_size_bytes=200,
    )
    d = result.to_dict()
    assert d["truncated"] is True
    assert d["total_row_count"] == 5000
    assert d["truncation_reason"] == "Row limit exceeded"


# ──────────────────────────────────────────────────
# Write query detection tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_query_blocked_on_readonly():
    """Write queries are rejected on readonly connections."""
    executor = QueryExecutor()
    conn = AsyncMock()
    config = _make_conn_config(database="production", access="readonly")

    for stmt in ["INSERT INTO t VALUES (1)", "UPDATE t SET x=1",
                 "DELETE FROM t", "DROP TABLE t", "ALTER TABLE t ADD c INT",
                 "CREATE TABLE t (id INT)", "TRUNCATE t"]:
        with pytest.raises(PermissionError):
            await executor.execute(
                conn=conn,
                conn_config=config,
                connection_name="prod-db",
                query=stmt,
                agent_id="test-agent",
            )


@pytest.mark.asyncio
async def test_select_allowed_on_readonly():
    """SELECT queries are permitted on readonly connections."""
    executor = QueryExecutor()
    conn = AsyncMock()
    # Simulate asyncpg transaction context manager
    tx = AsyncMock()
    conn.transaction.return_value = tx
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])

    config = _make_conn_config(database="production", access="readonly")
    result = await executor.execute(
        conn=conn,
        conn_config=config,
        connection_name="prod-db",
        query="SELECT * FROM users",
        agent_id="test-agent",
    )
    assert result.row_count == 0
    assert not result.truncated


# ──────────────────────────────────────────────────
# Row limit tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_row_limit_truncation():
    """Results exceeding max_rows are truncated with flag."""
    executor = QueryExecutor()
    conn = AsyncMock()
    records = make_records(25)
    conn.fetch = AsyncMock(return_value=records)

    config = _make_conn_config(max_rows=10)
    result = await executor.execute(
        conn=conn,
        conn_config=config,
        connection_name="test-db",
        query="SELECT * FROM large_table",
        agent_id="test-agent",
    )
    assert result.row_count == 10
    assert result.total_row_count == 25
    assert result.truncated is True
    assert "Row limit" in result.truncation_reason


@pytest.mark.asyncio
async def test_within_row_limit_not_truncated():
    """Results within max_rows are not truncated."""
    executor = QueryExecutor()
    conn = AsyncMock()
    records = make_records(5)
    conn.fetch = AsyncMock(return_value=records)

    config = _make_conn_config(max_rows=10)
    result = await executor.execute(
        conn=conn,
        conn_config=config,
        connection_name="test-db",
        query="SELECT * FROM small_table",
        agent_id="test-agent",
    )
    assert result.row_count == 5
    assert not result.truncated


# ──────────────────────────────────────────────────
# Audit logging tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_logged_to_audit():
    """Every query is logged to the audit logger."""
    audit = AsyncMock()
    executor = QueryExecutor(audit_logger=audit)
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])

    config = _make_conn_config()
    await executor.execute(
        conn=conn,
        conn_config=config,
        connection_name="test-db",
        query="SELECT 1",
        agent_id="test-agent",
    )
    audit.log_tool_operation.assert_called_once()
    entry = audit.log_tool_operation.call_args[0][0]
    assert entry["tool"] == "database"
    assert entry["agent"] == "test-agent"
    assert entry["connection"] == "test-db"
    assert entry["query"] == "SELECT 1"
    assert "execution_time_ms" in entry
    assert "truncated" in entry


@pytest.mark.asyncio
async def test_failed_query_logged_with_error():
    """Failed queries are logged to audit with error detail."""
    audit = AsyncMock()
    executor = QueryExecutor(audit_logger=audit)
    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=Exception("syntax error"))

    # Use a transaction mock for readonly
    tx = AsyncMock()
    conn.transaction.return_value = tx
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)

    config = _make_conn_config(database="production", access="readonly")
    with pytest.raises(Exception, match="syntax error"):
        await executor.execute(
            conn=conn,
            conn_config=config,
            connection_name="prod-db",
            query="SELECT bad syntax",
            agent_id="test-agent",
        )

    audit.log_tool_operation.assert_called_once()
    entry = audit.log_tool_operation.call_args[0][0]
    assert "error" in entry
```

### 11. `tests/test_database_connection.py`

```python
"""Tests for the connection pool manager.

Covers pool creation, connection acquisition/release, secret
resolution, and cleanup.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from faith.tools.database.config import (
    ConnectionConfig,
    DatabaseToolConfig,
)
from faith.tools.database.connection import ConnectionManager


def _make_config(connections: dict | None = None) -> DatabaseToolConfig:
    """Helper to create a DatabaseToolConfig."""
    if connections is None:
        connections = {
            "test-db": ConnectionConfig(
                host="localhost",
                port=5432,
                database="test_myapp",
                user="agent_user",
                secret_ref="test_db_password",
            ),
        }
    return DatabaseToolConfig(connections=connections)


def test_get_config_known_connection():
    """get_config returns config for known connection."""
    config = _make_config()
    manager = ConnectionManager(config, {"test_db_password": "secret"})
    result = manager.get_config("test-db")
    assert result.database == "test_myapp"


def test_get_config_unknown_connection_raises():
    """get_config raises KeyError for unknown connection."""
    config = _make_config()
    manager = ConnectionManager(config, {})
    with pytest.raises(KeyError):
        manager.get_config("nonexistent")


@pytest.mark.asyncio
async def test_initialise_missing_secret_raises():
    """Initialise raises ValueError when secret_ref not found."""
    config = _make_config()
    manager = ConnectionManager(config, {})  # empty secrets
    with pytest.raises(ValueError, match="secret_ref"):
        await manager.initialise()


@pytest.mark.asyncio
async def test_close_all_clears_pools():
    """close_all closes all pools and clears the pool map."""
    config = _make_config()
    manager = ConnectionManager(config, {"test_db_password": "s"})
    mock_pool = AsyncMock()
    manager._pools["test-db"] = mock_pool

    await manager.close_all()
    mock_pool.close.assert_called_once()
    assert len(manager._pools) == 0
```

### 12. `tests/test_database_server.py`

```python
"""Tests for the DatabaseMCPServer.

Covers MCP tool registration, startup lifecycle, agent access
enforcement via tool handlers, and error responses.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from faith.tools.database.config import AccessLevel
from faith.tools.database.permissions import PermissionDeniedError
from faith.tools.database.query import QueryResult


# ──────────────────────────────────────────────────
# Server tool handler tests (unit level)
# ──────────────────────────────────────────────────


def test_is_write_query_detection():
    """Write query detection heuristic identifies DML/DDL."""
    from faith.tools.database.server import DatabaseMCPServer

    assert DatabaseMCPServer._is_write_query("INSERT INTO t VALUES (1)")
    assert DatabaseMCPServer._is_write_query("  UPDATE t SET x=1")
    assert DatabaseMCPServer._is_write_query("DELETE FROM t")
    assert DatabaseMCPServer._is_write_query("DROP TABLE t")
    assert not DatabaseMCPServer._is_write_query("SELECT * FROM t")
    assert not DatabaseMCPServer._is_write_query("  select 1")
    assert not DatabaseMCPServer._is_write_query("WITH cte AS (SELECT 1)")
```

---

## Configuration Examples

### `.faith/tools/database.yaml`

```yaml
database:
  connections:
    prod-db:
      host: db.internal
      port: 5432
      database: myapp
      user: agent_readonly
      secret_ref: prod_db_password
      access: readonly
      max_rows: 1000
      max_result_mb: 5
    test-db:
      host: localhost
      port: 5432
      database: test_myapp
      user: agent_user
      secret_ref: test_db_password
      access: readwrite
      max_rows: 5000
      max_result_mb: 10
```

### `config/secrets.yaml` (relevant entries)

```yaml
prod_db_password: "${PROD_DB_PASSWORD}"
test_db_password: "${TEST_DB_PASSWORD}"
```

### `.faith/agents/software-developer/config.yaml` (relevant section)

```yaml
databases:
  test-db: readwrite
```

### `.faith/agents/fds-architect/config.yaml` (relevant section)

```yaml
databases:
  prod-db: readonly
  test-db: readonly
```

---

## Integration Points

The Database MCP Server integrates with several other FAITH components:

```python
# FAITH-003: Configuration loading and secret resolution
# The server loads .faith/tools/database.yaml via the FAITH-003
# configuration system. Passwords are resolved through secret_ref
# keys that map to config/secrets.yaml entries.

config = load_config(".faith/tools/database.yaml", DatabaseToolConfig)
secrets = resolve_secrets(config)  # FAITH-003 secret resolver

# FAITH-008: Event system
# Permission mismatches are surfaced as system events to the
# Web UI approval panel.

await event_publisher.publish(FaithEvent(
    event=EventType.TOOL_PERMISSION_ALERT,
    source="database",
    data={
        "connection": "prod-db",
        "declared": "readonly",
        "actual": "readwrite",
        "message": "DB role has write access but connection is readonly",
    },
))

# FAITH-021: Audit logging
# Every query — reads and writes — is logged to the audit log.

await audit_logger.log_tool_operation({
    "tool": "database",
    "agent": "software-developer",
    "connection": "test-db",
    "query": "SELECT * FROM users WHERE active = true",
    "row_count": 42,
    "execution_time_ms": 12.5,
    "truncated": False,
})
```

---

## Acceptance Criteria

1. Named connections are loaded from `.faith/tools/database.yaml` and validated via Pydantic models. Missing or malformed config produces a clear error on startup.
2. Credentials are resolved exclusively via `secret_ref` from `config/secrets.yaml` — no passwords appear in tool config or logs.
3. All connections default to read-only. No database is auto-promoted to writable based on naming conventions such as `test_*`.
4. A mutating query is allowed only when the connection is explicitly declared `access: readwrite` and the user approves the mutating action through FAITH's approval flow.
5. If the connection is declared `readonly`, FAITH classifies and blocks mutating SQL before execution even if the underlying external server or DB role could technically write.
6. On startup, the integration queries actual PostgreSQL role permissions and compares them against declared access. A mismatch (role more permissive than declared) blocks startup unless `permission_override: true` is set on the connection.
7. `permission_override: true` acknowledges the role mismatch only; it does not make a readonly connection writable and does not bypass per-action approval for writes.
8. Per-agent database access is loaded from `.faith/agents/{id}/config.yaml`. An agent's access level for a connection cannot exceed the connection-level effective access (agent cap).
9. Query results are limited to 1,000 rows (configurable via `max_rows`) and 5MB (configurable via `max_result_mb`) per result set.
10. When a result is truncated, the response includes `truncated: true`, the `total_row_count`, and a `truncation_reason` — enabling the agent to refine its query.
11. All queries (reads and writes) are logged to the audit log (FAITH-021) with: timestamp, agent, connection name, query text, row count, execution time (ms), and truncated flag.
12. If FAITH falls back to an in-house implementation, MCP tools `db_query`, `db_list_connections`, `db_list_tables`, and `db_describe_table` are registered and callable via the MCP protocol.
13. All tests in `tests/test_database_permissions.py`, `tests/test_database_query.py`, `tests/test_database_connection.py`, and `tests/test_database_server.py` pass for whichever implementation path is active.

---

## Notes for Implementer

- **asyncpg is the driver.** Use `asyncpg` (not `psycopg2`) for async PostgreSQL access. Connection pools are created via `asyncpg.create_pool()`. The `asyncpg` library is already listed in the project's dependencies.
- **Read-only enforcement is dual-layer.** First, a SQL statement prefix check rejects obvious write statements before they reach the database. Second, all queries on readonly connections execute inside a PostgreSQL `READ ONLY` transaction (`conn.transaction(readonly=True)`), so even if the prefix check misses something, PostgreSQL itself will reject the write.
- **Production write block is absolute.** The `effective_access()` method on `ConnectionConfig` enforces that non-`test_*` databases always resolve to `readonly`, regardless of what `access:` says in the YAML. This is a hard rule with no override — `permission_override` only acknowledges a role mismatch, it does not grant write access to production.
- **Secret resolution is external.** This server receives a pre-resolved `secrets` dict at construction time. The actual secret loading and environment variable interpolation is handled by FAITH-003. The server never reads `config/secrets.yaml` directly.
- **Audit logger may be None.** If FAITH-021 is not yet available, the `QueryExecutor` falls back to standard Python logging with an `AUDIT:` prefix. The audit logger is injected at construction time to keep the dependency optional.
- **Connection pool sizing.** Default pool is `min_size=1, max_size=5` per connection. This is conservative — agents are not expected to run many concurrent queries. The pool settings are not yet user-configurable but the architecture supports adding `pool_min_size` / `pool_max_size` to `ConnectionConfig` in a future iteration.
- **`test_` detection uses substring match.** `"test_" in database_name` matches `test_myapp`, `my_test_db`, etc. The string `testing` does NOT match because it lacks the underscore. This matches the FRS wording "database whose name contains `test_`".
- **No schema migration.** This server does not create or modify database schemas. It is a pure query tool. Schema management is out of scope.
