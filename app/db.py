import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


class Connection:
    def __init__(self, raw, postgres):
        self.raw = raw
        self.postgres = postgres

    def execute(self, sql, args=()):
        return self.raw.execute(sql.replace("?", "%s") if self.postgres else sql, args)

    def one(self, sql, args=()):
        row = self.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(row) for row in self.execute(sql, args)]


@contextmanager
def database(write=False):
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn:
        import psycopg
        from psycopg.rows import dict_row

        raw = psycopg.connect(dsn, row_factory=dict_row, client_encoding="UTF8")
    else:
        # Dedicated, single-instance Azure App Service pilot can persist SQLite in
        # /home. Never silently fall back to an ephemeral production database.
        if os.environ.get("APP_ENV") == "production":
            if os.environ.get("APP_DB_ENGINE") != "sqlite":
                raise RuntimeError("PostgreSQL is required in production unless the isolated SQLite pilot is enabled")
            sqlite_path = Path(os.environ.get("LOCAL_DB", ""))
            if not sqlite_path.is_absolute() or not str(sqlite_path).startswith("/home/"):
                raise RuntimeError("Production SQLite must use persistent App Service /home storage")
        raw = sqlite3.connect(os.environ.get("LOCAL_DB", "jobhunter.db"), timeout=30)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        if write:
            raw.execute("BEGIN IMMEDIATE")
    try:
        db = Connection(raw, bool(dsn))
        if write and dsn:
            db.execute("SELECT pg_advisory_xact_lock(29513851)")
        yield db
        raw.commit()
    except BaseException:
        raw.rollback()
        raise
    finally:
        raw.close()


def migrate():
    with database(write=True) as db:
        db.execute("CREATE TABLE IF NOT EXISTS migration (version TEXT PRIMARY KEY)")
        for file in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")):
            if db.one("SELECT version FROM migration WHERE version=?", (file.name,)):
                continue
            for statement in file.read_text().split(";"):
                if statement.strip():
                    db.execute(statement)
            db.execute("INSERT INTO migration VALUES (?)", (file.name,))


def audit(db, actor, action, target):
    db.execute("INSERT INTO audit VALUES (?,?,?,?,?)", (str(uuid4()), now(), actor, action, target))


def checkpoint(db, key, value):
    db.execute(
        "INSERT INTO checkpoint VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )
