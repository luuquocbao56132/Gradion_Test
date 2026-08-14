"""Connection registry and fan-out of an opaque payload.

This module knows nothing about projects, the store or DTOs. pipeline.py builds
the payload after COMMIT and hands it over; realtime.py only moves it
(design 3.1, 9.4).
"""
from __future__ import annotations

import asyncio
from typing import Protocol


class Sender(Protocol):
    async def send_json(self, payload: dict) -> None: ...


class Subscriber:
    """One connection's writer task plus its single latest-state slot.

    The broadcaster never sends. It writes to this slot; the writer task
    performs the send, so a dead socket fails in its own task and the pipeline's
    call remains a non-awaiting, non-raising handoff (design 9.4).
    """

    def __init__(self, sender: Sender) -> None:
        self._sender = sender
        self._slot: dict | None = None
        self._ready = asyncio.Event()
        self._closed = False

    def offer(self, payload: dict) -> None:
        """Coalescing: a newer state replaces an unsent one. Bounded by
        construction, so there is no queue and no drop policy to design. The
        only cost is intermediate frames - a stalled client may see two
        portraits arrive together rather than in sequence."""
        self._slot = payload
        self._ready.set()

    async def run(self) -> None:
        while not self._closed:
            await self._ready.wait()
            self._ready.clear()
            payload, self._slot = self._slot, None
            if payload is None:
                continue
            try:
                await self._sender.send_json(payload)
            except Exception:
                self._closed = True
                return

    def close(self) -> None:
        self._closed = True
        self._ready.set()


class RealtimeRegistry:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: dict[str, set[Subscriber]] = {}

    def _assert_loop(self) -> None:
        """R1: created on, mutated by and read from the event loop thread only -
        never from a worker thread, run_in_executor or asyncio.to_thread."""
        running = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = running
        elif self._loop is not running:
            raise RuntimeError(
                "RealtimeRegistry was used from a different event loop than the one "
                "it was first used on."
            )

    def register(self, project_id: str, subscriber: Subscriber) -> None:
        self._assert_loop()
        self._subscribers.setdefault(project_id, set()).add(subscriber)

    def unregister(self, project_id: str, subscriber: Subscriber) -> None:
        self._assert_loop()
        subscribers = self._subscribers.get(project_id)
        if subscribers is None:
            return
        subscribers.discard(subscriber)
        if not subscribers:
            self._subscribers.pop(project_id, None)

    def publish(self, project_id: str, payload: dict) -> None:
        self._assert_loop()
        for subscriber in tuple(self._subscribers.get(project_id, ())):
            subscriber.offer(payload)

    def count(self, project_id: str) -> int:
        return len(self._subscribers.get(project_id, ()))
