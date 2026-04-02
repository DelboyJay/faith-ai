from __future__ import annotations

import asyncio

import pytest

from faith_pa.pa.event_dispatcher import PAEventDispatcher
from faith_shared.protocol.events import EventType, FaithEvent


class FakeRedis:
    def pubsub(self):
        return FakePubSub()


class FakePubSub:
    async def subscribe(self, channel: str) -> None:
        return None

    async def unsubscribe(self, channel: str) -> None:
        return None

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0):
        await asyncio.sleep(0)
        return None

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_dispatcher_runs_typed_and_wildcard_handlers() -> None:
    dispatcher = PAEventDispatcher(FakeRedis())
    called: list[str] = []

    async def typed(event: FaithEvent):
        called.append(f"typed:{event.event.value}")
        return {"kind": "typed"}

    async def wildcard(event: FaithEvent):
        called.append(f"wild:{event.event.value}")
        return {"kind": "wild"}

    dispatcher.register(EventType.AGENT_ERROR, typed)
    dispatcher.register_wildcard(wildcard)

    await dispatcher._dispatch(FaithEvent(event=EventType.AGENT_ERROR, source="tester"))

    assert called == ["typed:agent:error", "wild:agent:error"]
    assert len(dispatcher.recent_interventions) == 2


@pytest.mark.asyncio
async def test_dispatcher_registers_stall_detector_resources() -> None:
    dispatcher = PAEventDispatcher(FakeRedis())
    dispatcher.register_channel("chan-1")
    dispatcher.register_agent("agent-1")

    assert "chan-1" in dispatcher.stall_detector._channel_activity
    assert "agent-1" in dispatcher.stall_detector._agent_heartbeats

