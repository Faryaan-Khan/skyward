"""Database layer (SQLAlchemy Core).

Production: set DATABASE_URL to a Postgres URL (e.g. Neon) so every web worker and
the ingestion job share one database. Local dev and tests fall back to SQLite.
"""
from __future__ import annotations

import datetime as dt
import os
import pickle
from contextlib import contextmanager

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Index, Integer, LargeBinary, MetaData, String, Table,
    create_engine, func, insert, inspect, or_, select, text, update,
)
from sqlalchemy.exc import IntegrityError

_DEFAULT_SQLITE = os.path.join(os.path.dirname(__file__), "..", "skyward.db")


def _url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        # Neon/Heroku style "postgres://" -> SQLAlchemy psycopg3 driver
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url
    return "sqlite:///" + os.getenv("SKYWARD_DB", _DEFAULT_SQLITE)


metadata = MetaData()

fares = Table(
    "fares", metadata,
    Column("id", Integer, primary_key=True),
    Column("route", String(16), nullable=False),
    Column("observed", Date, nullable=False),        # day the price was seen
    Column("depart", Date, nullable=False),
    Column("price", Float, nullable=False),
    Column("is_sale", Boolean, nullable=False, default=False),
    Column("source", String(32), nullable=False, default="synthetic"),
    Column("carrier", String(8)),
    Column("flight_number", String(16)),
    Column("transfers", Integer),
    Column("link", String(512)),
    Column("fetched_at", DateTime(timezone=True)),   # exact time of the live fetch
    Index("ix_fares_route_depart", "route", "depart"),
    Index("ix_fares_route_observed", "route", "observed", "source"),
)

watches = Table(
    "watches", metadata,
    Column("id", Integer, primary_key=True),
    Column("token", String(64), nullable=False, unique=True),  # private manage token
    Column("route", String(16), nullable=False),
    Column("depart", Date, nullable=False),
    Column("target_price", Float),
    Column("email", String(255)),
    Column("telegram_chat_id", String(64)),
    Column("created", DateTime(timezone=True), server_default=func.now()),
    # v2.1: double opt-in for email + anti-spam bookkeeping
    Column("email_verified", Boolean, nullable=False, server_default="0"),
    Column("last_alert_price", Float),
    Column("last_alert_at", DateTime),                   # naive UTC
    Column("alert_count", Integer, nullable=False, server_default="0"),
)

alerts = Table(
    "alerts", metadata,
    Column("id", Integer, primary_key=True),
    Column("watch_id", Integer, index=True),
    Column("kind", String(32), nullable=False),
    Column("message", String(1000), nullable=False),
    Column("created", DateTime(timezone=True), server_default=func.now()),
)

model_store = Table(
    "model_store", metadata,
    Column("id", Integer, primary_key=True),
    Column("created", DateTime(timezone=True), server_default=func.now()),
    Column("mae", Float),
    Column("rows_trained", Integer),
    Column("blob", LargeBinary, nullable=False),
)

routes = Table(
    "routes", metadata,
    Column("code", String(16), primary_key=True),        # e.g. "KUL-LHR"
    Column("origin_name", String(128)),
    Column("dest_name", String(128)),
    Column("origin_country", String(64)),
    Column("dest_country", String(64)),
    Column("origin_lat", Float), Column("origin_lon", Float),
    Column("dest_lat", Float), Column("dest_lon", Float),
    Column("searches", Integer, nullable=False, default=0),
    Column("last_searched", DateTime),                   # naive UTC
    Column("featured", Boolean, nullable=False, default=False),
)

search_events = Table(  # one row per search, for weekly popularity (pruned after 30 days)
    "search_events", metadata,
    Column("id", Integer, primary_key=True),
    Column("route", String(16), nullable=False),
    Column("at", DateTime, nullable=False, index=True),  # naive UTC
)

popular = Table(  # recomputed weekly: Skyward users' searches + worldwide demand
    "popular", metadata,
    Column("code", String(16), primary_key=True),
    Column("rank", Integer, nullable=False),
    Column("score", Float, nullable=False),
    Column("user_searches", Integer, nullable=False, default=0),
    Column("worldwide", Boolean, nullable=False, default=False),
    Column("updated", DateTime, nullable=False),
)

job_lock = Table(
    "job_lock", metadata,
    Column("name", String(32), primary_key=True),
    Column("started", DateTime),  # naive UTC
)

_engine = None


def engine():
    global _engine
    if _engine is None:
        url = _url()
        kw: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
        else:
            kw.update(pool_size=5, max_overflow=5, pool_recycle=300)
        _engine = create_engine(url, **kw)
    return _engine


def reset_engine() -> None:
    """Forget the cached engine (used by tests that switch databases)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


@contextmanager
def connect():
    with engine().begin() as conn:
        yield conn


def rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result]


def init(seed_if_empty: bool = True) -> None:
    """Create tables. Seed synthetic history when the DB is empty so the model has a baseline."""
    from .synthetic import generate

    metadata.create_all(engine())
    migrate()
    with connect() as c:
        if seed_if_empty and c.execute(select(func.count()).select_from(fares)).scalar() == 0:
            batch = [
                {"route": r, "observed": o, "depart": d, "price": p, "is_sale": bool(s), "source": "synthetic"}
                for r, o, d, p, s in generate()
            ]
            for i in range(0, len(batch), 5000):
                c.execute(insert(fares), batch[i:i + 5000])


def migrate() -> None:
    """Add columns introduced after a table was first created (create_all never alters tables)."""
    insp = inspect(engine())
    with connect() as c:
        for table in metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {col["name"] for col in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine().dialect)}'
                if col.server_default is not None:
                    default = col.server_default.arg
                    if isinstance(col.type, Boolean):  # Postgres wants TRUE/FALSE, SQLite accepts both
                        default = "TRUE" if str(default) in ("1", "true") else "FALSE"
                    ddl += f" DEFAULT {default}"
                    if not col.nullable:
                        ddl += " NOT NULL"
                c.execute(text(ddl))


def try_acquire(name: str, min_interval_s: int) -> bool:
    """Claim a job slot shared by all workers/instances. True if this caller should run the job.

    Works as a cross-process lock: the UPDATE only succeeds for one caller per interval.
    """
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    cutoff = now - dt.timedelta(seconds=min_interval_s)
    with connect() as c:
        if c.execute(select(job_lock.c.name).where(job_lock.c.name == name)).first() is None:
            try:
                with c.begin_nested():
                    c.execute(insert(job_lock), {"name": name, "started": None})
            except IntegrityError:
                pass  # another worker created it first
        res = c.execute(
            update(job_lock)
            .where(job_lock.c.name == name)
            .where(or_(job_lock.c.started.is_(None), job_lock.c.started < cutoff))
            .values(started=now)
        )
        return res.rowcount == 1


def last_job_start(name: str) -> dt.datetime | None:
    with connect() as c:
        return c.execute(select(job_lock.c.started).where(job_lock.c.name == name)).scalar()


def save_model(model, rows_trained: int) -> None:
    with connect() as c:
        c.execute(insert(model_store), {"mae": model.mae, "rows_trained": rows_trained, "blob": pickle.dumps(model)})
        # keep only the 3 most recent models
        keep = [r[0] for r in c.execute(select(model_store.c.id).order_by(model_store.c.id.desc()).limit(3))]
        c.execute(model_store.delete().where(model_store.c.id.not_in(keep)))


def latest_model_id() -> int | None:
    with connect() as c:
        return c.execute(select(func.max(model_store.c.id))).scalar()


def load_model():
    """Return (id, created, model) for the newest stored model, or None."""
    with connect() as c:
        r = c.execute(
            select(model_store.c.id, model_store.c.created, model_store.c.blob)
            .order_by(model_store.c.id.desc()).limit(1)
        ).first()
    if r is None:
        return None
    return r.id, r.created, pickle.loads(r.blob)
