"""Behavior checks for contention, cancellation, and bounded runtime work."""
import asyncio
import gc
import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import anyio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import main
from runtime_io import WebSocketWriter, run_bounded_sync


class Socket:
    def __init__(self, blocked=False):
        self.blocked = blocked
        self.entered = asyncio.Event()
        self.delivered = asyncio.Event()
        self.release = asyncio.Event()
        self.messages = []
        self.closed = False
        self.active = 0
        self.max_active = 0

    async def send_text(self, value):
        self.active += 1
        self.max_active = max(self.active, self.max_active)
        try:
            self.entered.set()
            if self.blocked:
                await self.release.wait()
            await asyncio.sleep(0)
            self.messages.append(value)
            self.delivered.set()
        finally:
            self.active -= 1

    async def close(self, **kwargs):
        self.closed = True


class RuntimeContentionTest(unittest.IsolatedAsyncioTestCase):
    async def test_slow_socket_does_not_delay_healthy_recipient(self):
        writer = WebSocketWriter(timeout=0.1)
        slow, healthy = Socket(True), Socket()
        task = asyncio.create_task(writer.broadcast([slow, healthy], 'hello'))
        await asyncio.wait_for(healthy.delivered.wait(), 0.05)
        self.assertFalse(task.done())
        self.assertEqual([slow], await task)
        self.assertTrue(slow.closed)
        with self.assertRaises(ConnectionError):
            await writer.send_text(slow, 'again')

    async def test_socket_writes_are_serialized_and_state_is_reclaimed(self):
        writer = WebSocketWriter()
        socket = Socket()
        await asyncio.gather(*(writer.send_text(socket, str(i)) for i in range(12)))
        self.assertEqual(1, socket.max_active)
        self.assertEqual([str(i) for i in range(12)], socket.messages)
        del socket
        gc.collect()
        self.assertEqual(0, len(writer._states))

    async def test_backlog_is_bounded_and_overflow_retires_socket(self):
        writer = WebSocketWriter(timeout=0.1, max_pending=1)
        socket = Socket(True)
        first = asyncio.create_task(writer.send_text(socket, 'first'))
        await socket.entered.wait()
        with self.assertRaises(ConnectionError):
            await writer.send_text(socket, 'overflow')
        self.assertTrue(socket.closed)
        with self.assertRaises(TimeoutError):
            await first
        self.assertEqual(0, writer._states[socket].pending)

    async def test_same_identity_in_other_organization_is_independent(self):
        entered = asyncio.Event()
        async with main.serialized_flight_submission('RID', organization_id='a'):
            async def other():
                async with main.serialized_flight_submission('RID', organization_id='b'):
                    entered.set()
            await asyncio.wait_for(other(), 0.1)
        self.assertTrue(entered.is_set())
        self.assertEqual({}, main._flight_submission_locks)

    async def test_cancelled_waiter_does_not_split_lock_or_leak(self):
        entered = asyncio.Event()
        async def waiter():
            async with main.serialized_flight_submission('RID', organization_id='a'):
                entered.set()
        async with main.serialized_flight_submission('RID', organization_id='a'):
            cancelled = asyncio.create_task(waiter())
            await asyncio.sleep(0)
            cancelled.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled
            survivor = asyncio.create_task(waiter())
            await asyncio.sleep(0)
            self.assertFalse(entered.is_set())
            self.assertEqual(1, len(main._flight_submission_locks))
        await survivor
        self.assertEqual({}, main._flight_submission_locks)
        self.assertEqual({}, main._flight_submission_lock_users)

    async def test_slow_weather_upload_allows_other_org_coordination(self):
        started, release = threading.Event(), threading.Event()
        def weather(*args):
            started.set()
            if not release.wait(2):
                raise RuntimeError('Test weather was not released')
            return dict.fromkeys(('temp', 'hum', 'dew', 'precip', 'wind', 'gusts', 'cloud'), 0)
        now = datetime.now(UTC)
        inputs = dict(spec=dict(rid='RID', sar_id='TEST', uas='test', incident='Test',
                                op_period='1', map_id='MAP'), title='test',
                      start_time=now, end_time=now + timedelta(minutes=2),
                      start_ts_sec=now.timestamp(), start_lat=39., start_lng=-121.,
                      duration_hrs=.033, processing_comments=[], distance=1.,
                      localized_start_time=now)
        result = Mock()
        result.scalars.return_value.first.return_value = None
        db = SimpleNamespace(add=Mock(), flush=AsyncMock())
        hub = main.R2CCoordinationHub()
        socket = Socket()
        hub._zones_by_map[('other-org', 'MAP')] = {'zone': SimpleNamespace(websocket=socket)}
        with patch.object(main, 'get_weather', weather), \
             patch.object(main, 'find_overlap', AsyncMock(return_value=result)), \
             patch.object(main, 'get_time_of_day', return_value='Day'), \
             patch.object(main, 'archive_flight_log', AsyncMock(return_value=('log', '/tmp/log'))):
            upload = asyncio.create_task(main.create_flight_and_archive(db, {}, inputs))
            try:
                async with asyncio.timeout(1):
                    while not started.is_set():
                        await asyncio.sleep(.001)
                await asyncio.wait_for(hub.broadcast('other-org', 'MAP', {'type':'heartbeat_ack'}), .1)
                self.assertFalse(upload.done())
                self.assertEqual('heartbeat_ack', json.loads(socket.messages[0])['type'])
            finally:
                release.set()
                await upload

    async def test_blocked_storage_does_not_block_coordination(self):
        started, release = threading.Event(), threading.Event()
        def write(*args):
            started.set()
            release.wait(2)
            return ('log', '/tmp/log')
        with patch.object(main, '_write_flight_log', write):
            task = asyncio.create_task(main.archive_flight_log('test', None, {}, 1))
            try:
                async with asyncio.timeout(1):
                    while not started.is_set():
                        await asyncio.sleep(.001)
                socket = Socket()
                await asyncio.wait_for(WebSocketWriter().send_text(socket, 'heartbeat'), .1)
                self.assertFalse(task.done())
            finally:
                release.set()
                await task

    async def test_cancelled_caller_keeps_capacity_until_thread_finishes(self):
        started, release = threading.Event(), threading.Event()
        second_started = threading.Event()
        limiter = anyio.CapacityLimiter(1)
        def blocked():
            started.set()
            release.wait(2)
        task = asyncio.create_task(run_bounded_sync(blocked, limiter=limiter))
        try:
            async with asyncio.timeout(1):
                while not started.is_set():
                    await asyncio.sleep(.001)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            second = asyncio.create_task(run_bounded_sync(second_started.set, limiter=limiter))
            await asyncio.sleep(.02)
            self.assertFalse(second_started.is_set())
            self.assertEqual(1, limiter.borrowed_tokens)
        finally:
            release.set()
        await asyncio.wait_for(second, 1)
        self.assertTrue(second_started.is_set())
        self.assertEqual(0, limiter.borrowed_tokens)

    async def test_weather_worker_concurrency_is_bounded(self):
        release = threading.Event()
        count_lock = threading.Lock()
        active = peak = 0
        def weather(*args):
            nonlocal active, peak
            with count_lock:
                active += 1
                peak = max(peak, active)
            release.wait(2)
            with count_lock:
                active -= 1
            return {}
        with patch.object(main, 'get_weather', weather), \
             patch.object(main, '_weather_workers', anyio.CapacityLimiter(2)):
            tasks = [asyncio.create_task(main.lookup_flight_weather(0, 0, 0)) for _ in range(8)]
            try:
                async with asyncio.timeout(1):
                    while peak < 2:
                        await asyncio.sleep(.001)
                await asyncio.sleep(.02)
                self.assertEqual(2, peak)
            finally:
                release.set()
                await asyncio.gather(*tasks)
            self.assertEqual(2, peak)


class RuntimeRetentionTest(unittest.IsolatedAsyncioTestCase):
    async def test_sighting_ingest_leaves_cleanup_to_bounded_scheduled_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_async_engine(f'sqlite+aiosqlite:///{directory}/runtime.db')
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(main.Base.metadata.create_all)
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                async with sessions() as session:
                    session.add_all([main.R2CRecentSighting(organization_id='a', map_id='M',
                                     remote_id=str(i), received_ms=0) for i in range(1005)])
                    await session.commit()
                with patch.object(main, 'AsyncSessionLocal', sessions), \
                     patch.object(main, 'control_plane_store', None):
                    hub = main.R2CCoordinationHub()
                    await hub._record_sighting('b', 'M', 'fresh', 'Z', 'G', 1, 0., 0., 0.)
                    async with sessions() as session:
                        self.assertEqual(1006, await session.scalar(select(func.count()).select_from(main.R2CRecentSighting)))
                    await hub._cleanup_persisted_state()
                    async with sessions() as session:
                        self.assertEqual(6, await session.scalar(select(func.count()).select_from(main.R2CRecentSighting)))
                    await hub._cleanup_persisted_state()
                    async with sessions() as session:
                        row = (await session.execute(select(main.R2CRecentSighting))).scalar_one()
                        self.assertEqual('b', row.organization_id)
            finally:
                await engine.dispose()
