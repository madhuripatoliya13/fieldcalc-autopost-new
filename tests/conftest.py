"""Test bootstrap. Forces DRY_RUN + an isolated temp SQLite DB BEFORE any app
module imports (settings are cached at first import), then gives every test a
freshly-created schema so tests don't bleed into each other."""
import os
import pathlib
import tempfile

# Must be set before importing app.* (get_settings is lru_cached).
os.environ["DRY_RUN"] = "true"
os.environ["IMAGING_FORCE_PILLOW"] = "true"  # keep tests fast + browser-independent
_DB = pathlib.Path(tempfile.gettempdir()) / "autopost_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
_GEN_STATE = pathlib.Path(tempfile.gettempdir()) / "autopost_generation_state_test.json"

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    from app.database import Base, engine, init_db

    Base.metadata.drop_all(engine)
    init_db()
    if _GEN_STATE.exists():
        _GEN_STATE.unlink()
    yield
    if _GEN_STATE.exists():
        _GEN_STATE.unlink()
