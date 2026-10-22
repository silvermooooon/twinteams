"""Bounded real-provider access for the explicit disposable browser fixture only."""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from contextvars import ContextVar
from urllib.parse import urlsplit

MAX_ATTEMPTS = 60
MAX_OUTPUT = 4096
MAX_SECONDS = 900
MODE = 'live-gpt-6-luna'
PHASE = ContextVar('browser_e2e_live_phase', default='scenario')


def configuration():
    from checks.local_stack import RUNTIME, read_env
    saved = read_env(RUNTIME / '.env')
    key, base = saved.get('OPENAI_API_KEY', '').strip(), saved.get('OPENAI_BASE_URL', '').rstrip('/')
    proxy = saved.get('HTTPS_PROXY') or saved.get('https_proxy') or ''
    origin, gateway = urlsplit(base), urlsplit(proxy)
    if not key or origin.scheme != 'https' or not origin.netloc or origin.username or origin.password or origin.query or origin.fragment:
        raise RuntimeError('Live E2E requires the existing configured key and HTTPS base URL')
    if gateway.hostname not in {'127.0.0.1', 'localhost', '::1'} or gateway.port != 7894:
        raise RuntimeError('Live E2E requires the existing loopback proxy on port 7894')
    return {'OPENAI_API_KEY': key, 'OPENAI_BASE_URL': base, 'HTTPS_PROXY': proxy}


def initialize_budget(root, *, max_attempts=MAX_ATTEMPTS):
    if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError('Live E2E attempt ceiling must be between 1 and 60')
    value = {'max_attempts': max_attempts, 'max_output_tokens': MAX_OUTPUT,
             'started_at': time.time(), 'deadline_at': time.time() + MAX_SECONDS, 'attempts': []}
    (root / 'live-budget.json').write_text(json.dumps(value))
    return value


def reserve(root, payload):
    if payload.get('model') != 'gpt-6-luna':
        raise AssertionError('Live E2E permits only gpt-6-luna')
    maximum = payload.get('max_output_tokens')
    if type(maximum) is not int or not 1 <= maximum <= MAX_OUTPUT:
        raise AssertionError('Live E2E requires bounded output tokens')
    with (root / 'live-budget.json').open('r+') as target:
        fcntl.flock(target, fcntl.LOCK_EX)
        value = json.load(target)
        if time.time() >= value['deadline_at'] or len(value['attempts']) >= min(value['max_attempts'], MAX_ATTEMPTS):
            raise RuntimeError('Live E2E model budget exhausted; no further request was sent')
        attempt = len(value['attempts']) + 1
        value['attempts'].append({'attempt': attempt, 'phase': PHASE.get(), 'started_at': time.time(),
                                  'model': 'gpt-6-luna', 'max_output_tokens': maximum})
        target.seek(0)
        json.dump(value, target)
        target.truncate()
        return attempt


def settle(root, attempt, *, status=None, error=None):
    with (root / 'live-budget.json').open('r+') as target:
        fcntl.flock(target, fcntl.LOCK_EX)
        value = json.load(target)
        row = value['attempts'][attempt - 1]
        row.update(response_at=time.time(), http_status=status, error_type=error)
        target.seek(0)
        json.dump(value, target)
        target.truncate()


class RedactedStream:
    def __init__(self, stream, secrets):
        self.stream, self.secrets = stream, tuple(value for value in secrets if value)

    def write(self, text):
        for value in self.secrets:
            text = text.replace(value, '[private-live-config]')
        return self.stream.write(text)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def install(root, role):
    import httpx
    original_async = httpx.AsyncClient.send
    base = os.environ.get('OPENAI_BASE_URL', '').rstrip('/')
    allowed = base + '/responses'
    secrets = [os.environ.get(name, '') for name in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'HTTPS_PROXY')]
    # No real credential, configured gateway URL or proxy value may reach logs.
    sys.stdout, sys.stderr = RedactedStream(sys.stdout, secrets), RedactedStream(sys.stderr, secrets)

    async def bounded_send(client, request, *args, **kwargs):
        if role != 'worker' or request.method != 'POST' or str(request.url) != allowed:
            raise AssertionError('Live E2E denies HTTP outside its configured model endpoint')
        attempt = reserve(root, json.loads(request.content))
        try:
            kwargs['follow_redirects'] = False
            response = await original_async(client, request, *args, **kwargs)
        except BaseException as error:
            settle(root, attempt, error=type(error).__name__)
            raise
        settle(root, attempt, status=response.status_code)
        return response

    def deny_sync(*args, **kwargs):
        raise AssertionError('Live E2E denies synchronous/outside-provider HTTP')

    httpx.AsyncClient.send, httpx.Client.send = bounded_send, deny_sync


async def smoke(root):
    path = root / 'live-smoke.json'
    if path.exists():
        result = json.loads(path.read_text())
        if result.get('passed'):
            return
        raise RuntimeError('The bounded live smoke already failed; no implicit retry')
    from agent_runtime import Models, RuntimeConfig
    config = RuntimeConfig.from_env()
    from agent_runtime.types import AgentContext, user_message
    models = Models(config=config)
    path.write_text(json.dumps({'passed': False, 'attempted': True}))
    marker = PHASE.set('smoke')
    try:
        result = await models.complete_simple(models.get_model(), AgentContext(messages=[user_message('Reply with OK only. This is a bounded connectivity test; do not use tools.')]), {'max_tokens': MAX_OUTPUT})
        usage = result.get('usage', {})
        passed = result.get('stopReason') == 'stop' and any(row.get('type') == 'text' and row.get('text') for row in result.get('content', []))
        safe = {'passed': bool(passed), 'stop_reason': result.get('stopReason'),
                'usage': {name: value for name, value in usage.items() if type(value) in {int, float}}}
        path.write_text(json.dumps(safe))
        if not passed:
            raise RuntimeError('Live E2E smoke did not complete; inspect bounded HTTP status metadata')
    finally:
        PHASE.reset(marker)
