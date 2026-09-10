"""Bounded output for the single-process coordination runtime."""

import asyncio
import json
import weakref
from dataclasses import dataclass, field

import anyio


_blocking_jobs = set()


async def run_bounded_sync(function, *args, limiter):
    """Keep the capacity reservation until the worker actually finishes.

    A disconnected HTTP caller may cancel its asyncio task, but Python cannot
    stop an already running thread. Shield the worker and transfer ownership of
    the reservation so cancellation cannot admit extra work beyond the limit.
    Callers still waiting for capacity can cancel without starting a worker.
    """
    reservation = object()
    await limiter.acquire_on_behalf_of(reservation)

    async def work():
        try:
            return await anyio.to_thread.run_sync(function, *args)
        finally:
            limiter.release_on_behalf_of(reservation)

    task = asyncio.create_task(work())
    _blocking_jobs.add(task)

    def finished(job):
        _blocking_jobs.discard(job)
        if not job.cancelled():
            job.exception()  # Retrieve failures even if the HTTP caller left.

    task.add_done_callback(finished)
    return await asyncio.shield(task)


@dataclass
class _OutputState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: int = 0
    failed: bool = False


class WebSocketWriter:
    """Serialize socket writes, bound backlog, and retire stalled transports.

    State uses weak keys so disconnected sockets are not retained. The deadline
    includes time waiting for another writer; overflow closes rather than
    silently dropping ownership or confirmation messages.
    """

    def __init__(self, timeout=2.0, max_pending=16):
        self.timeout = timeout
        self.max_pending = max_pending
        self._states = weakref.WeakKeyDictionary()

    async def _retire(self, websocket, state):
        if state.failed:
            return
        state.failed = True
        try:
            await asyncio.wait_for(
                websocket.close(code=1013, reason="Client output stalled; reconnect"),
                timeout=0.25,
            )
        except Exception:
            pass

    async def send_text(self, websocket, value):
        state = self._states.setdefault(websocket, _OutputState())
        if state.failed:
            raise ConnectionError("Socket output already retired")
        if state.pending >= self.max_pending:
            await self._retire(websocket, state)
            raise ConnectionError("Socket output backlog exceeded")
        state.pending += 1
        sending = False
        try:
            async with asyncio.timeout(self.timeout):
                async with state.lock:
                    if state.failed:
                        raise ConnectionError("Socket output already retired")
                    sending = True
                    await websocket.send_text(value)
        except asyncio.CancelledError:
            if sending:
                await self._retire(websocket, state)
            raise
        except Exception:
            await self._retire(websocket, state)
            raise
        finally:
            state.pending -= 1

    async def send_json(self, websocket, value):
        await self.send_text(websocket, json.dumps(value))

    async def broadcast(self, websockets, value):
        """Healthy recipients progress without waiting for a stalled peer."""
        sockets = tuple(websockets)
        results = await asyncio.gather(
            *(self.send_text(socket, value) for socket in sockets),
            return_exceptions=True,
        )
        return [socket for socket, result in zip(sockets, results)
                if isinstance(result, BaseException)]
