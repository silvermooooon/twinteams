"""Build and run real browser E2E with isolated PG/Redis/two APIs/worker.

The default model provider is deterministic/offline; --live-model enables only
the explicit bounded scenario. No production process, schema, workspace or
frontend/dist is changed. Use --serve while developing browser cases.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet
from redis.asyncio import Redis

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_stack import environment
from postgres import TestDatabase

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / 'frontend'
FIXTURE = Path(__file__).with_name('browser_e2e_fixture.py')


def free_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        port = connection.getsockname()[1]
        if port in {8000, 8001}:
            return free_port()
        return port


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true', help='Keep the disposable stack alive until interrupted')
    parser.add_argument('--artifact-dir', type=Path)
    parser.add_argument('--node', default=os.getenv('TWINTEAMS_E2E_NODE'), help='Node >=22.12 executable')
    parser.add_argument('--live-model', action='store_true', help='Explicit bounded real gpt-6-luna scenario; offline remains the default')
    parser.add_argument('--scenario', choices=['continuous-workflow', 'tracking-resumption', 'first-private-group-tracking', 'scheduled-task-workflow', 'personal-onboarding', 'file-delivery-preview', 'ask-user', 'proactivity', 'interaction-live', 'read-unread-live'])
    parser.add_argument('--live-max-attempts', type=int, default=60, help='Frozen per-campaign HTTP-attempt ceiling, including smoke and worker restarts')
    args = parser.parse_args()
    if not 1 <= args.live_max_attempts <= 60:
        parser.error('--live-max-attempts must be between 1 and 60')
    if (args.live_model and not args.scenario) or (not args.live_model and args.scenario not in {None, 'personal-onboarding', 'file-delivery-preview', 'ask-user', 'proactivity'}):
        parser.error('--live-model requires an explicit dedicated --scenario')
    if not args.live_model and args.live_max_attempts != 60:
        parser.error('--live-max-attempts requires --live-model')
    if args.live_model and os.environ.get('TWINTEAMS_E2E_CASE'):
        parser.error('Live mode uses its dedicated scenario, not an offline case filter')
    live = {}
    if args.live_model:
        sys.path.insert(0, str(BACKEND))
        from checks.browser_e2e_live import configuration
        live = configuration()
    candidates = [args.node or shutil.which('node')]
    if not args.node:
        candidates += [str(path) for path in Path('/usr/local/n/versions/node').glob('*/bin/node')]
    node = None
    for candidate in candidates:
        if not candidate:
            continue
        version = (await asyncio.to_thread(subprocess.check_output, [candidate, '-p', 'process.versions.node'], text=True)).strip()
        numbers = tuple(int(part) for part in version.split('.'))
        if numbers >= (22, 12, 0):
            node = candidate
            break
    if node is None:
        raise RuntimeError('Node >=22.12 is required; pass --node /path/to/node')
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    private = environment('api')  # Only local PG/Redis configuration is retained.
    previous = os.environ.get('TWINTEAMS_TEST_DB_URL')
    os.environ['TWINTEAMS_TEST_DB_URL'] = private['DATABASE_URL']
    try:
        database = TestDatabase()
    finally:
        if previous is None:
            os.environ.pop('TWINTEAMS_TEST_DB_URL', None)
        else:
            os.environ['TWINTEAMS_TEST_DB_URL'] = previous
    nonce = uuid4().hex
    namespace = 'browser-e2e-' + nonce
    processes, handles, workers = [], [], []
    monitor = None
    artifacts = (args.artifact_dir or Path(tempfile.mkdtemp(prefix='twinteams-e2e-artifacts-'))).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix='twinteams-browser-e2e-')
    with temporary as directory:
        root = Path(directory)
        (root / '.fixture').write_text(nonce)
        # Do not inherit real provider keys, HTTP proxies or app config.
        env = {name: os.environ[name] for name in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'SYSTEMROOT') if name in os.environ}
        env['PATH'] = str(Path(node).parent) + os.pathsep + env.get('PATH', '')
        env.update(TMPDIR=str(root / 'tmp'), GIT_CONFIG_GLOBAL=str(root / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1',
            PYTHONPATH=str(BACKEND), PYTHONUNBUFFERED='1', NO_PROXY='127.0.0.1,localhost,::1',
            TWINTEAMS_ENV='local', TWINTEAMS_DEVELOPMENT_MODE='true', TWINTEAMS_TENANT_ID='browser-e2e',
            TWINTEAMS_BROWSER_E2E='true', TWINTEAMS_E2E_INSTANCE_ID=nonce,
            TWINTEAMS_E2E_ROOT=str(root), TWINTEAMS_E2E_DIST=str(root / 'dist'),
            DATABASE_URL=database.url, AGENT_DB_URL=database.url,
            TWINTEAMS_REDIS_URL=private['TWINTEAMS_REDIS_URL'], REDIS_URL=private['TWINTEAMS_REDIS_URL'],
            TWINTEAMS_REDIS_PREFIX=namespace, TWINTEAMS_COMPENSATION_SECONDS='2',
            TWINTEAMS_WORKSPACE_ROOT=str(root / 'workspaces'), AGENT_MEMORY_DIR=str(root / 'memory'),
            AGENT_SECRETS_DIR=str(root / 'secrets'), AGENT_SECRETS_KEY=Fernet.generate_key().decode(),
            AGENT_PROVIDER='openai', AGENT_API='browser-e2e-offline', AGENT_MODEL='gpt-6-luna',
            OPENAI_API_KEY='explicit-offline-not-a-provider-key')
        env['TWINTEAMS_ONBOARDING_ENABLED'] = 'true' if args.scenario == 'personal-onboarding' else 'false'
        if args.scenario:
            env['TWINTEAMS_E2E_SCENARIO'] = args.scenario
        if args.live_model:
            env.pop('OPENAI_API_KEY')
            env.update(AGENT_API='openai-responses', AGENT_MAX_TOKENS='4096',
                TWINTEAMS_E2E_MODEL_MODE='live-gpt-6-luna', TWINTEAMS_E2E_SCENARIO=args.scenario)
        (root / 'gitconfig').write_text('')
        for folder in ('tmp', 'workspaces', 'memory', 'secrets'):
            (root / folder).mkdir(mode=0o700)
        state = root / 'state.json'
        state.write_text(json.dumps({'env': env, 'root': str(root)}))
        state.chmod(0o600)

        def spawn(role, port=None):
            log = (artifacts / f'{role}-{port or 0}.log').open('a' if role == 'worker' and workers else 'w')
            handles.append(log)
            command = [sys.executable, '-u', str(FIXTURE), role]
            if port:
                command += ['--port', str(port)]
            child_env = {**env, 'TWINTEAMS_E2E_ROLE': role}
            if args.live_model and role == 'worker':
                child_env.update(live)
            process = subprocess.Popen(command, cwd=BACKEND, env=child_env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(process)
            if role == 'worker':
                workers.append(process)
            return process

        async def ready(client):
            for _ in range(150):
                if any((process.returncode if isinstance(process, asyncio.subprocess.Process) else process.poll()) not in {None, 0} for process in processes):
                    raise RuntimeError('Fixture process exited; inspect isolated artifacts')
                try:
                    response = await client.get('/health')
                    health = response.json()
                    if response.status_code == 200 and health.get('browser_e2e') is True and health.get('e2e_instance_id') == nonce and health.get('model_configured') is True:
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(1 if args.live_model else .1)
            raise RuntimeError('Fixture API readiness timed out')

        try:
            with (artifacts / 'build.log').open('w') as output:
                build = await asyncio.create_subprocess_exec('npm', 'run', 'build', '--', '--outDir', str(root / 'dist'),
                    cwd=FRONTEND, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                processes.append(build)
                if await build.wait():
                    raise RuntimeError('E2E build failed; see build.log')
            migration = spawn('init')
            if await asyncio.to_thread(migration.wait, 45):
                raise RuntimeError('Fixture init failed; inspect init log')
            port_a, port_b = free_port(), free_port()
            while port_b == port_a:
                port_b = free_port()
            url_a, url_b = f'http://127.0.0.1:{port_a}', f'http://127.0.0.1:{port_b}'
            env['TWINTEAMS_CORS_ORIGINS'] = url_a + ',' + url_b
            state.write_text(json.dumps({'env': env, 'root': str(root)}))
            if args.live_model:
                from checks.browser_e2e_live import initialize_budget
                initialize_budget(root, max_attempts=args.live_max_attempts)
            spawn('worker')
            spawn('api', port_a)
            spawn('api', port_b)

            async def control_worker():
                control = root / 'restart-worker.json'
                while True:
                    if control.exists():
                        operation = json.loads(control.read_text())
                        control.unlink()
                        if operation.get('instance_id') != nonce:
                            raise RuntimeError('Control request instance mismatch')
                        worker = workers[-1]
                        if worker.poll() is None:
                            os.killpg(worker.pid, signal.SIGTERM)
                            try:
                                await asyncio.to_thread(worker.wait, 8)
                            except subprocess.TimeoutExpired:
                                os.killpg(worker.pid, signal.SIGKILL)
                                await asyncio.to_thread(worker.wait, 5)
                        replacement = spawn('worker')
                        (root / ('restart-' + operation['id'] + '.json')).write_text(json.dumps({'operation_id': operation['id'], 'pid': replacement.pid, 'restarted': True}))
                    await asyncio.sleep(.2)

            monitor = asyncio.create_task(control_worker())
            async with httpx.AsyncClient(base_url=url_a, trust_env=False) as first, httpx.AsyncClient(base_url=url_b, trust_env=False) as second:
                await asyncio.gather(ready(first), ready(second))
            runner_env = {**env, **{name: value for name, value in os.environ.items() if name in {'TWINTEAMS_E2E_CASE', 'TWINTEAMS_E2E_HEADED', 'TWINTEAMS_BROWSER_CHANNEL', 'PLAYWRIGHT_BROWSERS_PATH', 'HEADED', 'BROWSER_CHANNEL'}}, 'TWINTEAMS_E2E_URL': url_a, 'TWINTEAMS_E2E_SECONDARY_URL': url_b,
                'TWINTEAMS_E2E_ARTIFACT_DIR': str(artifacts), 'TWINTEAMS_E2E_NAMESPACE': namespace,
                'TWINTEAMS_E2E_MODEL_MODE': 'live-gpt-6-luna' if args.live_model else 'deterministic-offline', 'TWINTEAMS_E2E_STATE': str(state),
                'TWINTEAMS_E2E_PYTHON': sys.executable}
            if args.live_model:
                runner_env['TWINTEAMS_E2E_SCENARIO'] = args.scenario
            public = {key: value for key, value in runner_env.items() if key.startswith('TWINTEAMS_E2E_') and key not in {'TWINTEAMS_E2E_ROOT', 'TWINTEAMS_E2E_DIST'}}
            (artifacts / 'instance.json').write_text(json.dumps(public, indent=2))
            print(json.dumps({'status': 'ready', **public}), flush=True)
            if args.serve:
                stop = asyncio.Event()
                loop = asyncio.get_running_loop()
                if args.live_model:
                    await asyncio.wait_for(stop.wait(), max(.01, json.loads((root / 'live-budget.json').read_text())['deadline_at'] - time.time()))
                else:
                    await stop.wait()
            else:
                runner = await asyncio.create_subprocess_exec(node, str(FRONTEND / 'e2e' / 'run.cjs'), cwd=FRONTEND, env=runner_env, start_new_session=True)
                processes.append(runner)
                if args.live_model:
                    code = await asyncio.wait_for(runner.wait(), max(.01, json.loads((root / 'live-budget.json').read_text())['deadline_at'] - time.time()))
                else:
                    code = await runner.wait()
                if code:
                    raise RuntimeError(f'Browser E2E runner failed with exit {code}; see {artifacts}')
        finally:
            if monitor:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            if args.live_model:
                # Preserve terminal allowlisted evidence before its disposable DB is removed.
                snapshot = await asyncio.create_subprocess_exec(sys.executable, str(FIXTURE), 'inspect', '--state', str(state),
                    cwd=BACKEND, env=env, stdin=subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
                processes.append(snapshot)
                try:
                    output, _ = await asyncio.wait_for(snapshot.communicate(), 15)
                    value = json.loads(output) if snapshot.returncode == 0 else {'snapshot_failed': True, 'exit_code': snapshot.returncode}
                except (TimeoutError, ValueError):
                    value = {'snapshot_failed': True}
                (artifacts / 'live-final-inspect.json').write_text(json.dumps(value, indent=2))
            if (root / 'provider-calls.jsonl').exists():
                shutil.copyfile(root / 'provider-calls.jsonl', artifacts / 'provider-calls.jsonl')
            for process in processes:
                running = process.returncode is None if isinstance(process, asyncio.subprocess.Process) else process.poll() is None
                if running:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            for process in processes:
                try:
                    if isinstance(process, asyncio.subprocess.Process):
                        await asyncio.wait_for(process.wait(), 8)
                    else:
                        await asyncio.to_thread(process.wait, 8)
                except (subprocess.TimeoutExpired, TimeoutError):
                    os.killpg(process.pid, signal.SIGKILL)
                    if isinstance(process, asyncio.subprocess.Process):
                        await process.wait()
                    else:
                        await asyncio.to_thread(process.wait, 5)
            for handle in handles:
                handle.close()
            for filename in ('live-budget.json', 'live-smoke.json'):
                if (root / filename).exists():
                    shutil.copyfile(root / filename, artifacts / filename)
            # Logs contain only synthetic fixture data; redact private connection strings.
            for path in artifacts.glob('*.log'):
                content = path.read_text(errors='replace')
                for value in (private['DATABASE_URL'], private['TWINTEAMS_REDIS_URL'], database.url, env['AGENT_SECRETS_KEY'], *live.values()):
                    content = content.replace(value, '<private-test-config>')
                path.write_text(content)
            redis = Redis.from_url(env['TWINTEAMS_REDIS_URL'])
            remaining = None
            try:
                keys = [key async for key in redis.scan_iter(match=namespace + ':*', count=100)]
                if keys:
                    await redis.unlink(*keys)
                remaining = len([key async for key in redis.scan_iter(match=namespace + ':*', count=100)])
            finally:
                await redis.aclose()
                database.close()
            def schema_exists():
                import psycopg
                with psycopg.connect(private['DATABASE_URL']) as connection:
                    return connection.execute('SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=%s)', (database.schema,)).fetchone()[0]
            removed = not await asyncio.to_thread(schema_exists)
            stopped = [{'pid': process.pid, 'returncode': process.returncode} for process in processes]
            temporary.cleanup()
            cleanup = {'status': 'cleaned', 'artifacts': str(artifacts), 'schema': database.schema,
                'schema_removed': removed, 'redis_prefix': namespace, 'redis_keys_remaining': remaining,
                'processes_stopped': all(row['returncode'] is not None for row in stopped), 'processes': stopped,
                'temp_root_removed': not root.exists()}
            (artifacts / 'cleanup.json').write_text(json.dumps(cleanup, indent=2))
            print(json.dumps(cleanup), flush=True)
            if not removed or remaining != 0 or not cleanup['processes_stopped'] or not cleanup['temp_root_removed']:
                raise RuntimeError('Disposable E2E cleanup verification failed')


if __name__ == '__main__':
    asyncio.run(main())
