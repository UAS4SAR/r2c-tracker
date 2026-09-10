#!/usr/bin/env python3
"""Fresh local PostgreSQL + real HTTP/WebSocket runtime regression checks.

Creates its own temporary cluster; never accepts an existing database URL.
Requires PostgreSQL server tools (PG_BIN may select their directory).
"""
import asyncio
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def validate_child_environment():
    directory = Path(os.environ['R2C_RUNTIME_TEST_DIR']).resolve()
    # Internal subprocess modes may only use the cluster created by this runner.
    pid_lines = (directory / 'pgdata' / 'postmaster.pid').read_text().splitlines()
    assert directory.name.startswith('r2c-runtime-')
    assert Path(pid_lines[1]).resolve() == directory / 'pgdata'
    for variable, database in (
        ('DATABASE_URL', 'r2c_test_tracker'),
        ('CONTROL_PLANE_DATABASE_URL', 'r2c_test_control'),
        ('R2C_RUNTIME_ADMIN_URL', 'postgres'),
    ):
        url = urlsplit(os.environ[variable])
        assert url.hostname == '127.0.0.1' and url.port == int(pid_lines[3])
        assert url.username == 'r2c_test' and url.path == '/' + database


def serve():
    validate_child_environment()
    import main
    import uvicorn
    directory = Path(os.environ['R2C_RUNTIME_TEST_DIR'])
    main.BASE_LOG_DIRECTORY = str(directory / 'flightlogs')

    def delayed_weather(*args):
        (directory / 'weather-started').touch()
        deadline = time.monotonic() + 8
        while not (directory / 'weather-release').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('Test weather release timed out')
            time.sleep(.01)
        return dict.fromkeys(('temp','hum','dew','precip','wind','gusts','cloud'), 0)
    main.get_weather = delayed_weather
    # Surface unexpected errors in the test log even with custom middleware.
    @main.app.exception_handler(Exception)
    async def show_test_exception(request, exc):
        import traceback
        traceback.print_exception(exc)
        return main.Response(str(exc), status_code=500)
    uvicorn.run(main.app, host='127.0.0.1', port=int(os.environ['PORT']), log_level='warning')


async def prepare_and_check_pools():
    import asyncpg
    import main
    from sqlalchemy import func, select, text
    from scripts.create_release_check_credential import create_credential
    await main.init_db()
    tokens = {}
    for org in ('ALPHA', 'BRAVO'):
        tokens[org] = await create_credential(os.environ['CONTROL_PLANE_DATABASE_URL'], org)
    admin = await asyncpg.connect(os.environ['R2C_RUNTIME_ADMIN_URL'])
    try:
        for engine in (main.engine, main.control_plane_store.engine):
            async with engine.connect() as connection:
                pid = await connection.scalar(text('SELECT pg_backend_pid()'))
            # Kill an idle pooled connection, as can happen during DB maintenance.
            assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
            async with engine.connect() as connection:
                assert await connection.scalar(text('SELECT 1')) == 1
                assert await connection.scalar(text('SELECT pg_backend_pid()')) != pid
            await engine.dispose()
        print('PASS: both database pools recover from terminated idle connections', flush=True)
        async with main.AsyncSessionLocal() as session:
            session.add_all([main.R2CRecentSighting(organization_id='retention-test',
                             map_id='M', remote_id=str(i), received_ms=0) for i in range(1005)])
            await session.commit()
        hub = main.R2CCoordinationHub()
        await hub._record_sighting('fresh-org', 'M', 'fresh', 'Z', 'G', 1, 0., 0., 0.)
        await hub._cleanup_persisted_state()
        async with main.AsyncSessionLocal() as session:
            assert await session.scalar(select(func.count()).select_from(main.R2CRecentSighting)) == 6
        await hub._cleanup_persisted_state()
        async with main.AsyncSessionLocal() as session:
            row = (await session.execute(select(main.R2CRecentSighting))).scalar_one()
            assert row.organization_id == 'fresh-org'
        await main.engine.dispose()
        await main.control_plane_store.engine.dispose()
        print('PASS: PostgreSQL retention batches preserve fresh sightings', flush=True)
    finally:
        await admin.close()
    return tokens


async def coordination_check(base, token):
    import websockets
    async with websockets.connect(base.replace('http:', 'ws:') + '/bravo/ws/r2c',
            additional_headers={'X-SAR-Token': token}) as ws:
        await ws.send(json.dumps(dict(type='hello', mapId='SHARED', zoneId='BRAVO',
                                      guid='BRAVO', name='Test Bravo', lat=39.1, lng=-121.1)))
        async with asyncio.timeout(1):
            while True:
                message = json.loads(await ws.recv())
                if message.get('type') == 'hello_ack':
                    assert message['mapId'] == 'SHARED'
                    break


def checks():
    validate_child_environment()
    import requests
    directory = Path(os.environ['R2C_RUNTIME_TEST_DIR'])
    tokens = asyncio.run(prepare_and_check_pools())
    log = (directory / 'server.log').open('w')
    process = subprocess.Popen([sys.executable, __file__, '--serve'], cwd=ROOT, stdout=log, stderr=log)
    base = 'http://127.0.0.1:' + os.environ['PORT']
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError('Test server exited')
            try:
                if requests.get(base + '/readyz', timeout=.5).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError('Test server did not become ready')
        headers = {'X-SAR-Token': tokens['ALPHA']}
        assert requests.put(base + '/bravo/upload', headers=headers, json={}, timeout=2).status_code == 403
        stamp = 1788955200000
        payload = {'features': [{'type': 'Feature', 'properties': {
            'title': '1SAR7_test', 'r2c_prop': {'rid':'TEST-RID', 'mid':'1SAR7',
            'model':'test', 'incident':'Automated test', 'op_period':'1',
            'map_id':'SHARED', 'distance_mi':1.0}},
            'geometry': {'type':'LineString', 'coordinates': [
                [-121.1,39.1,20,stamp], [-121.102,39.102,20,stamp+120000]]}}]}
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            upload = pool.submit(requests.put, base + '/alpha/upload', headers=headers,
                                 json=payload, timeout=12)
            try:
                deadline = time.monotonic() + 5
                while not (directory / 'weather-started').exists():
                    if upload.done():
                        raise AssertionError('Upload did not reach weather: ' + upload.result().text)
                    if time.monotonic() > deadline:
                        raise TimeoutError('Weather was not reached')
                    time.sleep(.01)
                assert requests.get(base + '/livez', timeout=1).status_code == 200
                asyncio.run(coordination_check(base, tokens['BRAVO']))
                assert not upload.done(), 'Slow weather must still be held during coordination check'
                print('PASS: another organization coordinates while upload weather is stalled', flush=True)
            finally:
                (directory / 'weather-release').touch()
            result = upload.result()
            assert result.status_code == 200, result.text
        # Same identity is allowed in another org, but not duplicated within one.
        headers_b = {'X-SAR-Token': tokens['BRAVO']}
        result = requests.put(base + '/bravo/upload', headers=headers_b, json=payload, timeout=5)
        assert result.status_code == 200, result.text
        assert requests.put(base + '/alpha/upload', headers=headers, json=payload, timeout=5).status_code == 409
        for org in ('alpha', 'bravo'):
            assert list((directory / 'flightlogs' / 'organizations' / org).rglob('*.json'))
        print('PASS: authenticated uploads, tenant denial, duplicate rejection, isolated archives', flush=True)
    except BaseException:
        log.flush()
        print((directory / 'server.log').read_text()[-12000:], file=sys.stderr)
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()


def run():
    pg_bin = os.environ.get('PG_BIN')
    if not pg_bin:
        executable = shutil.which('initdb')
        if not executable:
            raise SystemExit('PostgreSQL server tools required; set PG_BIN to their bin directory.')
        pg_bin = str(Path(executable).parent)
    with tempfile.TemporaryDirectory(prefix='r2c-runtime-') as temporary:
        directory = Path(temporary)
        data = directory / 'pgdata'
        # Short Unix socket path avoids macOS socket pathname limits.
        pg_port, http_port = free_port(), free_port()
        subprocess.run([str(Path(pg_bin)/'initdb'), '-D', str(data), '-U', 'r2c_test',
                        '-A', 'trust', '--no-locale'], check=True, stdout=subprocess.DEVNULL)
        pg_ctl = str(Path(pg_bin)/'pg_ctl')
        subprocess.run([pg_ctl, '-D', str(data), '-l', str(directory/'postgres.log'),
                        '-o', f'-h 127.0.0.1 -p {pg_port} -k /tmp', '-w', 'start'], check=True)
        try:
            for database in ('r2c_test_tracker','r2c_test_control'):
                subprocess.run([str(Path(pg_bin)/'createdb'), '-h','127.0.0.1', '-p', str(pg_port),
                                '-U','r2c_test', database], check=True)
            prefix = f'postgresql+asyncpg://r2c_test@127.0.0.1:{pg_port}/'
            # Do not inherit cloud credentials or application integration settings.
            env = {key: os.environ[key] for key in ('PATH','HOME','TMPDIR') if key in os.environ}
            env.update(PYTHONUNBUFFERED='1', DATABASE_URL=prefix+'r2c_test_tracker',
                       CONTROL_PLANE_DATABASE_URL=prefix+'r2c_test_control',
                       R2C_RUNTIME_ADMIN_URL=f'postgresql://r2c_test@127.0.0.1:{pg_port}/postgres',
                       R2C_RUNTIME_TEST_DIR=str(directory), PORT=str(http_port),
                       SECRET_KEY='local-runtime-test-session-key',  # pragma: allowlist secret
                       CONTROL_PLANE_SIGNING_KEY='local-runtime-test-signing-key-32-characters',  # pragma: allowlist secret
                       CONTROL_PLANE_PUBLIC_URL='https://r2c-tracker.example',
                       CONTROL_PLANE_MODE='live', SESSION_COOKIE_HTTPS_ONLY='true')
            subprocess.run([sys.executable, __file__, '--checks'], env=env, cwd=ROOT, check=True)
        finally:
            subprocess.run([pg_ctl, '-D',str(data), '-m','immediate', '-w','stop'], check=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--serve']:
        serve()
    elif sys.argv[1:] == ['--checks']:
        checks()
    elif not sys.argv[1:]:
        run()
    else:
        raise SystemExit('Usage: test_runtime_postgres.py')
