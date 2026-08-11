"""Lakebase connection helper using the Databricks secret-backed URL."""

import base64
import os
from contextlib import contextmanager

import psycopg2
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor


def _lakebase_url() -> str:
    """Read the base64-encoded Lakebase URL from the configured secret."""
    direct_url = os.getenv("LAKEBASE_URL")
    if direct_url:
        return direct_url
    from utils.database import LAKEBASE_CONFIG
    return psycopg2.extensions.make_dsn(**LAKEBASE_CONFIG)
    scope = os.getenv("LAKEBASE_SECRET_SCOPE", "database")
    key = os.getenv("LAKEBASE_SECRET_KEY", "lakebase-url")
    secret = WorkspaceClient().secrets.get_secret(scope=scope, key=key)
    return base64.b64decode(secret.value).decode("utf-8")


@contextmanager
def get_connection():
    conn = psycopg2.connect(_lakebase_url(), cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def run_query(sql: str, params=None):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()


def run_write(sql: str, params=None):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            conn.commit()
            return cursor.rowcount
