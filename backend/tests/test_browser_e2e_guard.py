"""The explicit browser fixture fails closed before app imports or DB access."""
from urllib.parse import urlencode

import pytest

from checks.browser_e2e_fixture import guard


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    root = tmp_path / 'twinteams-browser-e2e-guard'
    root.mkdir()
    nonce = 'a' * 32
    (root / '.fixture').write_text(nonce)
    url = 'postgresql://fixture@127.0.0.1/test?' + urlencode({'options': '-c search_path=tt_test_' + 'b' * 32})
    values = {'TWINTEAMS_BROWSER_E2E': 'true', 'TWINTEAMS_E2E_INSTANCE_ID': nonce,
        'TWINTEAMS_E2E_ROOT': str(root), 'DATABASE_URL': url, 'AGENT_DB_URL': url,
        'TWINTEAMS_REDIS_PREFIX': 'browser-e2e-' + nonce,
        'TWINTEAMS_WORKSPACE_ROOT': str(root / 'workspaces'),
        'AGENT_MEMORY_DIR': str(root / 'memory'), 'AGENT_SECRETS_DIR': str(root / 'secrets')}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return root, values


def test_valid_disposable_marker_is_accepted_without_opening_a_database(isolated):
    root, _ = isolated
    assert guard() == root


@pytest.mark.parametrize('field,value', [
    ('TWINTEAMS_BROWSER_E2E', ''),
    ('TWINTEAMS_E2E_INSTANCE_ID', 'not-a-nonce'),
    ('DATABASE_URL', 'postgresql://fixture@127.0.0.1/test'),
    ('AGENT_DB_URL', 'postgresql://fixture@127.0.0.1/production'),
    ('TWINTEAMS_REDIS_PREFIX', 'twinteams:local'),
    ('TWINTEAMS_WORKSPACE_ROOT', '/outside/real-user-workspace'),
])
def test_production_or_mismatched_configuration_is_rejected(isolated, monkeypatch, field, value):
    monkeypatch.setenv(field, value)
    with pytest.raises(RuntimeError, match='Browser E2E'):
        guard()


def test_duplicate_schema_options_and_replaced_marker_are_rejected(isolated, monkeypatch):
    root, values = isolated
    unsafe = values['DATABASE_URL'] + '&options=-c+search_path%3Dpublic'
    monkeypatch.setenv('DATABASE_URL', unsafe)
    monkeypatch.setenv('AGENT_DB_URL', unsafe)
    with pytest.raises(RuntimeError, match='disposable schema'):
        guard()
    monkeypatch.setenv('DATABASE_URL', values['DATABASE_URL'])
    monkeypatch.setenv('AGENT_DB_URL', values['AGENT_DB_URL'])
    (root / '.fixture').write_text('wrong-instance')
    with pytest.raises(RuntimeError, match='disposable schema'):
        guard()


@pytest.mark.parametrize("maximum", [12, 20, 48, 60])
def test_live_budget_counts_failures_and_survives_restart(tmp_path, maximum):
    import json

    from checks.browser_e2e_live import initialize_budget, reserve, settle
    initialize_budget(tmp_path, max_attempts=maximum)
    payload = {'model': 'gpt-6-luna', 'max_output_tokens': 4096}
    for attempt in range(1, maximum + 1):
        assert reserve(tmp_path, payload) == attempt
        settle(tmp_path, attempt, status=503 if attempt % 2 else 200)
    # Reopening the file, as a replacement worker does, cannot renew allowance.
    assert len(json.loads((tmp_path / 'live-budget.json').read_text())['attempts']) == maximum
    with pytest.raises(RuntimeError, match='budget exhausted'):
        reserve(tmp_path, payload)


def test_live_budget_rejects_wrong_model_unbounded_output_and_expired_window(tmp_path):
    import json

    from checks.browser_e2e_live import initialize_budget, reserve
    initialize_budget(tmp_path)
    for maximum in (0, 61, '20'):
        with pytest.raises(ValueError, match='between 1 and 60'):
            initialize_budget(tmp_path, max_attempts=maximum)
    for payload in ({'model': 'other', 'max_output_tokens': 4096},
                    {'model': 'gpt-6-luna'}, {'model': 'gpt-6-luna', 'max_output_tokens': 4097}):
        with pytest.raises(AssertionError):
            reserve(tmp_path, payload)
    path = tmp_path / 'live-budget.json'
    budget = json.loads(path.read_text())
    budget['deadline_at'] = 0
    path.write_text(json.dumps(budget))
    with pytest.raises(RuntimeError, match='budget exhausted'):
        reserve(tmp_path, {'model': 'gpt-6-luna', 'max_output_tokens': 4096})
    assert json.loads(path.read_text())['attempts'] == []


def test_live_http_fence_allows_only_worker_endpoint_and_records_no_values(tmp_path, monkeypatch):
    import asyncio
    import json
    import sys

    import httpx

    from checks.browser_e2e_live import initialize_budget, install
    initialize_budget(tmp_path)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-unit-secret')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://provider.invalid/v1')
    # Restore globally patched methods/streams after this strictly offline test.
    monkeypatch.setattr(httpx.AsyncClient, 'send', httpx.AsyncClient.send)
    monkeypatch.setattr(httpx.Client, 'send', httpx.Client.send)
    monkeypatch.setattr(sys, 'stdout', sys.stdout)
    monkeypatch.setattr(sys, 'stderr', sys.stderr)
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={'ok': True})

    async def run():
        install(tmp_path, 'worker')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            for url, payload in [('https://other.invalid/v1/responses', {'model': 'gpt-6-luna', 'max_output_tokens': 4096}),
                                 ('https://provider.invalid/v1/responses', {'model': 'other', 'max_output_tokens': 4096})]:
                with pytest.raises(AssertionError):
                    await client.post(url, json=payload)
            response = await client.post('https://provider.invalid/v1/responses',
                json={'model': 'gpt-6-luna', 'max_output_tokens': 4096, 'input': 'synthetic input'},
                headers={'Authorization': 'Bearer synthetic-unit-secret'})
            assert response.status_code == 200
    asyncio.run(run())
    assert calls == ['/v1/responses']
    saved = (tmp_path / 'live-budget.json').read_text()
    assert 'synthetic-unit-secret' not in saved and 'synthetic input' not in saved
    assert json.loads(saved)['attempts'][0]['http_status'] == 200
