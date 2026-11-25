"""Database layer (SQLAlchemy Core).

Production: set DATABASE_URL to a Postgres URL (e.g. Neon) so every web worker and
the ingestion job share one database. Local dev and tests fall back to SQLite.
"""
from __future__ import annotations

import os
import pickle
from contextlib import contextmanager

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Index, Integer, LargeBinary, MetaData, String, Table,
    create_engine, func, insert, select,
)

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
    with connect() as c:
        if seed_if_empty and c.execute(select(func.count()).select_from(fares)).scalar() == 0:
            batch = [
                {"route": r, "observed": o, "depart": d, "price": p, "is_sale": bool(s), "source": "synthetic"}
                for r, o, d, p, s in generate()
            ]
            for i in range(0, len(batch), 5000):
                c.execute(insert(fares), batch[i:i + 5000])


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
