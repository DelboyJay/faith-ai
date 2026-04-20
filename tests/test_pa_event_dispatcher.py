"""Description:
    Verify PA event dispatch registration and intervention recording behaviour.

Requirements:
    - Prove typed and wildcard handlers both run for matching events.
    - Prove dispatcher helper methods register stall-detector resources.
"""

from __future__ import annotations

import asyncio

import pytest

from faith_pa.pa.event_dispatcher import PAEventDispatcher
from faith_shared.protocol.events import EventType, FaithEvent


class FakeRedis:
    """Description:
        Provide a minimal Redis double for event-dispatcher tests.

    Requirements:
        - Return a fake pubsub object for the subscriber stack.
    """

    def pubsub(self):
        """Description:
            Return a fake pubsub object.

        Requirements:
            - Mirror the interface expected by the event subscriber.

        :returns: Fake pubsub instance.
        """

        return FakePubSub()


class FakePubSub:
    """Description:
        Provide a minimal pubsub double for event-dispatcher tests.

    Requirements:
        - Support the async subscription interface used by the event subscriber.
    """

    async def subscribe(self, channel: str) -> None:
        """Description:
            Accept one fake subscription request.

        Requirements:
            - Return without side effects.

        :param channel: Channel name to subscribe to.
        """

        del channel
        return None

    async def unsubscribe(self, channel: str) -> None:
        """Description:
            Accept one fake unsubscription request.

        Requirements:
            - Return without side effects.

        :param channel: Channel name to unsubscribe from.
        """

        del channel
        return None

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0):
        """Description:
            Return no message after yielding control once.

        Requirements:
            - Yield to the event loop so the subscriber can progress cleanly in tests.

        :param ignore_subscribe_messages: Whether subscribe messages should be ignored.
        :param timeout: Poll timeout in seconds.
        :returns: ``None`` to indicate no message was received.
        """

        del ignore_subscribe_messages, timeout
        await asyncio.sleep(0)
        return None

    async def aclose(self) -> None:
        """Description:
            Close the fake pubsub object.

        Requirements:
            - Return without side effects.
        """

        return None


@pytest.mark.asyncio
async def test_dispatcher_runs_typed_and_wildcard_handlers() -> None:
    """Description:
    Verify both typed and wildcard handlers run for a matching event.

    Requirements:
        - This test is needed to prove the dispatcher fans out events to all relevant handlers.
        - Verify intervention results from both handlers are recorded.
    """

    dispatcher = PAEventDispatcher(FakeRedis())
    called: list[str] = []

    async def typed(event: FaithEvent):
        """Description:
            Record invocation of the typed handler.

        Requirements:
            - Append a stable marker for the received event.

        :param event: Dispatched event payload.
        :returns: Structured handler result payload.
        """

        called.append(f"typed:{event.event.value}")
        return {"kind": "typed"}

    async def wildcard(event: FaithEvent):
        """Description:
            Record invocation of the wildcard handler.

        Requirements:
            - Append a stable marker for the received event.

        :param event: Dispatched event payload.
        :returns: Structured handler result payload.
        """

        called.append(f"wild:{event.event.value}")
        return {"kind": "wild"}

    dispatcher.register(EventType.AGENT_ERROR, typed)
    dispatcher.register_wildcard(wildcard)

    await dispatcher._dispatch(FaithEvent(event=EventType.AGENT_ERROR, source="tester"))

    assert called == ["typed:agent:error", "wild:agent:error"]
    assert len(dispatcher.recent_interventions) == 2


@pytest.mark.asyncio
async def test_dispatcher_registers_stall_detector_resources() -> None:
    """Description:
    Verify dispatcher helper methods register channels and agents with the stall detector.

    Requirements:
        - This test is needed to prove PA orchestration can track channels and agent heartbeats.
        - Verify the registered channel and agent appear in the underlying stall detector state.
    """

    dispatcher = PAEventDispatcher(FakeRedis())
    dispatcher.register_channel("chan-1")
    dispatcher.register_agent("agent-1")

    assert "chan-1" in dispatcher.stall_detector._channel_activity
    assert "agent-1" in dispatcher.stall_detector._agent_heartbeats
