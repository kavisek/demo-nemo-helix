"""Work around a NeMo Helix 0.7.3 bug when its database is Postgres.

`nemo services run` keeps one async SQLAlchemy engine per process, and the
entities workspace-cleanup controller runs on its own event loop. With asyncpg
a pooled connection made on one loop is reused on another, the controller fails
with "attached to a different loop", and asyncpg then segfaults the server.
This turns off pooling for asyncpg engines (NullPool), so every connection is
opened on the loop that uses it. SQLite (the default) is not touched.

Loaded automatically because the nemo-helix service sets PYTHONPATH=/app/runtime.
Remove it once Helix creates a pool per event loop.
"""

import os

if os.environ.get("DATABASE_URL", "").startswith("postgresql"):
    import sqlalchemy.ext.asyncio as sa_asyncio
    from sqlalchemy.pool import NullPool

    _create_async_engine = sa_asyncio.create_async_engine
    _POOL_ONLY_ARGS = ("pool_size", "max_overflow", "pool_timeout", "pool_recycle")

    def _create_async_engine_without_pool(url, *args, **kwargs):
        if str(url).startswith("postgresql+asyncpg") and "poolclass" not in kwargs:
            kwargs = {k: v for k, v in kwargs.items() if k not in _POOL_ONLY_ARGS}
            kwargs["poolclass"] = NullPool
        return _create_async_engine(url, *args, **kwargs)

    sa_asyncio.create_async_engine = _create_async_engine_without_pool
