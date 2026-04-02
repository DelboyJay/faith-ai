"""Description:
    Re-export the shared FAITH communication protocol components.

Requirements:
    - Provide a stable import surface for compact messages, events, and subscriber utilities.
    - Avoid embedding runtime behaviour in the package export module.
"""

from faith_shared.protocol.compact import (
    ChannelMessageStore,
    CompactMessage,
    MessageFilter,
    MessagePriority,
    MessageStatus,
    MessageType,
)
from faith_shared.protocol.events import EventPublisher, EventType, FaithEvent
from faith_shared.protocol.subscriber import (
    CompletionBatcher,
    EventHandler,
    EventSubscriber,
    StallDetector,
)

__all__ = [
    "ChannelMessageStore",
    "CompactMessage",
    "MessageFilter",
    "MessagePriority",
    "MessageStatus",
    "MessageType",
    "FaithEvent",
    "EventType",
    "EventPublisher",
    "EventSubscriber",
    "EventHandler",
    "StallDetector",
    "CompletionBatcher",
]
