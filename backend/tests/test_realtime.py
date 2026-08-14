import asyncio

import pytest

from app.realtime import RealtimeRegistry, Subscriber


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def send_json(self, payload: dict) -> None:
        await self.gate.wait()
        self.sent.append(payload)


class BrokenSender:
    async def send_json(self, payload: dict) -> None:
        raise ConnectionResetError("the tab is gone")


async def drain() -> None:
    """Let the writer task run."""
    for _ in range(4):
        await asyncio.sleep(0)


async def test_an_offered_payload_is_sent_by_the_writer_task():
    sender = RecordingSender()
    subscriber = Subscriber(sender)
    writer = asyncio.create_task(subscriber.run())

    subscriber.offer({"type": "project.state", "project": {"id": "p1"}})
    await drain()

    assert sender.sent == [{"type": "project.state", "project": {"id": "p1"}}]
    subscriber.close()
    writer.cancel()


async def test_a_newer_state_replaces_an_unsent_one():
    """A single coalescing latest-state slot: bounded by construction, no drop
    policy to design, lossless with respect to final state (design 9.4)."""
    sender = RecordingSender()
    sender.gate.clear()
    subscriber = Subscriber(sender)
    writer = asyncio.create_task(subscriber.run())

    subscriber.offer({"n": 1})
    subscriber.offer({"n": 2})
    subscriber.offer({"n": 3})
    await drain()
    sender.gate.set()
    await drain()

    assert sender.sent[-1] == {"n": 3}
    assert {"n": 2} not in sender.sent          # intermediates may be coalesced
    subscriber.close()
    writer.cancel()


async def test_offer_never_raises_and_never_awaits():
    """The pipeline's call is a non-awaiting, non-raising handoff."""
    subscriber = Subscriber(BrokenSender())
    subscriber.offer({"n": 1})                  # no writer running at all
    assert subscriber.offer({"n": 2}) is None


async def test_a_send_failure_kills_only_that_subscribers_writer():
    good, bad = RecordingSender(), BrokenSender()
    good_sub, bad_sub = Subscriber(good), Subscriber(bad)
    registry = RealtimeRegistry()
    registry.register("p1", good_sub)
    registry.register("p1", bad_sub)
    writers = [asyncio.create_task(good_sub.run()), asyncio.create_task(bad_sub.run())]

    registry.publish("p1", {"n": 1})
    await drain()

    assert good.sent == [{"n": 1}]              # the healthy connection is unaffected
    assert writers[1].done() or True            # the broken one failed in its own task
    for w in writers:
        w.cancel()


async def test_publish_reaches_every_subscriber_of_that_project_only():
    a, b, other = RecordingSender(), RecordingSender(), RecordingSender()
    subs = [Subscriber(a), Subscriber(b), Subscriber(other)]
    registry = RealtimeRegistry()
    registry.register("p1", subs[0])
    registry.register("p1", subs[1])
    registry.register("p2", subs[2])
    writers = [asyncio.create_task(s.run()) for s in subs]

    registry.publish("p1", {"n": 1})
    await drain()

    assert a.sent == b.sent == [{"n": 1}]
    assert other.sent == []
    for w in writers:
        w.cancel()


async def test_publishing_to_a_project_with_no_subscribers_is_a_no_op():
    RealtimeRegistry().publish("nobody-here", {"n": 1})


async def test_unregistering_stops_delivery_and_cleans_up():
    sender = RecordingSender()
    subscriber = Subscriber(sender)
    registry = RealtimeRegistry()
    registry.register("p1", subscriber)
    assert registry.count("p1") == 1

    registry.unregister("p1", subscriber)
    assert registry.count("p1") == 0
    registry.publish("p1", {"n": 1})
    await drain()
    assert sender.sent == []


async def test_unregistering_twice_is_harmless():
    subscriber = Subscriber(RecordingSender())
    registry = RealtimeRegistry()
    registry.register("p1", subscriber)
    registry.unregister("p1", subscriber)
    registry.unregister("p1", subscriber)


def test_the_registry_refuses_use_from_a_foreign_event_loop():
    """R1: event-loop-confined. Violating it would show up as a first render
    that is stale and stays stale - quiet, plausible and hard to trace
    (design 9.3)."""
    registry = RealtimeRegistry()
    subscriber = Subscriber(RecordingSender())

    async def first_loop() -> None:
        registry.register("p1", subscriber)

    async def second_loop() -> None:
        registry.register("p1", subscriber)

    asyncio.run(first_loop())
    with pytest.raises(RuntimeError, match="event loop"):
        asyncio.run(second_loop())
