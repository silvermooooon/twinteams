"""Disposable PostgreSQL schema for tests; never reset application tables."""

from __future__ import annotations

import atexit
import os
from importlib.resources import files
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql


class TestDatabase:
    __test__ = False

    def __init__(self):
        self.base_url = os.getenv("TWINTEAMS_TEST_DB_URL") or os.getenv("DATABASE_URL")
        if not self.base_url:
            raise RuntimeError("Set TWINTEAMS_TEST_DB_URL to run PostgreSQL checks")
        self.base_url = self.base_url.replace("postgresql+psycopg://", "postgresql://")
        self.schema = "tt_test_" + uuid4().hex
        with psycopg.connect(self.base_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        parsed = urlsplit(self.base_url)
        query = dict(parse_qsl(parsed.query))
        query["options"] = f"-c search_path={self.schema}"
        self.url = urlunsplit(parsed._replace(query=urlencode(query, quote_via=quote)))
        self.closed = False
        atexit.register(self.close)
        # Provision the SDK using its canonical packaged DDL, not a copied schema.
        with psycopg.connect(self.url) as connection:
            for name in (
                "001_sessions.sql",
                "002_checkpoints.sql",
                "003_session_identity.sql",
                "004_coordination.sql",
                "005_execution_dispatch.sql",
            ):
                connection.execute(files("agent_runtime.storage").joinpath("sql", name).read_text())

    def configure(self):
        os.environ["DATABASE_URL"] = self.url
        os.environ["AGENT_DB_URL"] = self.url
        return self

    def close(self):
        if not self.closed:
            with psycopg.connect(self.base_url, autocommit=True) as connection:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
                )
            self.closed = True
