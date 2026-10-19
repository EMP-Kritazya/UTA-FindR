"""SQLAlchemy engine + session helpers."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ingest.config import get_settings


@lru_cache
def get_engine() -> Engine:
    # pool_pre_ping: check a pooled connection is alive before using it, so a
    # restarted Docker container doesn't cause a confusing error mid-crawl.
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit if the block succeeds, roll back if it raises."""
    session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
