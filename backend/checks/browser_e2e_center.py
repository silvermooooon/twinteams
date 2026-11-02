"""Extend only the disposable Center directory with the fourth browser fixture owner."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def main():
    nonce = os.environ.get('TWINTEAMS_E2E_INSTANCE_ID', '')
    root = Path(os.environ.get('TWINTEAMS_E2E_ROOT', '/nonexistent')).resolve()
    parsed = urlsplit(os.environ.get('DATABASE_URL', ''))
    options = parse_qs(parsed.query).get('options', [])
    port = int(sys.argv[1])
    if (os.environ.get('TWINTEAMS_BROWSER_E2E') != 'true'
            or os.environ.get('TWINTEAMS_E2E_SCENARIO') != 'story-workflow-live'
            or os.environ.get('MESSAGE_CENTER_TEST_FIXTURE') != 'true'
            or not re.fullmatch(r'[0-9a-f]{32}', nonce)
            or not root.name.startswith('twinteams-browser-e2e-')
            or not (root / '.fixture').is_file() or (root / '.fixture').is_symlink()
            or (root / '.fixture').read_text() != nonce
            or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or len(options) != 1 or not re.fullmatch(r'-c search_path=tt_test_[0-9a-f]{32}', options[0])
            or port in {8000, 8001, 8020} or not 1 <= port <= 65535):
        raise RuntimeError('Center fixture requires its isolated browser schema and port')
    sys.path.insert(0, os.getcwd())
    from app import service

    service.PEOPLE.append({'owner_id': 'owner-riley', 'name': 'DDD'})
    import uvicorn

    from app.main import app
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')


if __name__ == '__main__':
    main()
