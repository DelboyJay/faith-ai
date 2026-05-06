"""Description:
    Expose the FAITH logging helpers.

Requirements:
    - Provide stable imports for event, session, token, and retention helpers.
    - Keep package side effects minimal.
"""

from faith_pa.logging.event_log import EventLogEntry, EventLogWriter
from faith_pa.logging.log_rotator import (
    DEFAULT_ARCHIVE_SIZE_THRESHOLD_BYTES,
    DEFAULT_LOG_RETENTION_DAYS,
    DEFAULT_SESSION_RETENTION_DAYS,
    LogRotator,
)
from faith_pa.logging.session_log import (
    AgentIndexWriter,
    ChannelLogWriter,
    PAUserLogWriter,
    SessionLogWriter,
    SessionMeta,
    TaskLogWriter,
    TaskMeta,
)
from faith_pa.logging.token_logger import (
    DEFAULT_COST_THRESHOLD_USD,
    TokenEntry,
    TokenLogger,
)

__all__ = [
    "AgentIndexWriter",
    "ChannelLogWriter",
    "DEFAULT_ARCHIVE_SIZE_THRESHOLD_BYTES",
    "DEFAULT_COST_THRESHOLD_USD",
    "DEFAULT_LOG_RETENTION_DAYS",
    "DEFAULT_SESSION_RETENTION_DAYS",
    "EventLogEntry",
    "EventLogWriter",
    "LogRotator",
    "PAUserLogWriter",
    "SessionLogWriter",
    "SessionMeta",
    "TaskLogWriter",
    "TaskMeta",
    "TokenEntry",
    "TokenLogger",
]
