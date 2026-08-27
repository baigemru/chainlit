"""The wiring in persistence/config.py, which nothing else exercises.

Every assertion here corresponds to a defect found by an audit: the public
constructors advertised keyword arguments that raised, and the config was
handed to advanced_alchemy without the metadata it registers against.
"""

import pytest
from advanced_alchemy.base import metadata_registry
from sqlalchemy.ext.asyncio import create_async_engine

from chainlit.persistence import Persistence
from chainlit.persistence.config import sqlalchemy_config
from chainlit.persistence.models import Base

URL = "sqlite+aiosqlite://"


def test_engine_settings_reach_the_engine_config() -> None:
    """`from_url(url, pool_size=...)` used to raise TypeError.

    The kwargs were spread into the SQLAlchemyAsyncConfig constructor, which
    has no engine fields -- so the documented API died on its first caller.
    """
    config = sqlalchemy_config(URL, pool_size=7, pool_pre_ping=True)

    assert config.engine_config.pool_size == 7
    assert config.engine_config.pool_pre_ping is True


def test_from_url_accepts_engine_settings() -> None:
    persistence = Persistence.from_url(URL, pool_size=3)

    assert persistence.config.engine_config.pool_size == 3


def test_engine_settings_are_refused_alongside_a_prebuilt_engine() -> None:
    """Passing both is a caller error, not a silently ignored argument."""
    engine = create_async_engine(URL)

    with pytest.raises(ValueError, match="belong to the engine"):
        sqlalchemy_config(engine=engine, pool_size=3)


def test_url_and_engine_are_mutually_exclusive() -> None:
    engine = create_async_engine(URL)

    with pytest.raises(ValueError, match="exactly one"):
        sqlalchemy_config(url=URL, engine=engine)

    with pytest.raises(ValueError, match="exactly one"):
        sqlalchemy_config()


def test_metadata_is_registered_with_advanced_alchemy() -> None:
    """Without this, the registry keeps its own empty MetaData.

    Anything driving DDL or autogenerate through the config would then see no
    tables at all -- and would report that as "no changes", not as an error.
    """
    config = sqlalchemy_config(URL)

    assert config.metadata is Base.metadata
    assert set(config.metadata.tables) == {
        "chainlit.users",
        "chainlit.threads",
        "chainlit.steps",
        "chainlit.elements",
        "chainlit.feedbacks",
    }
    assert metadata_registry.get(config.bind_key) is Base.metadata


def test_listeners_that_have_nothing_to_do_are_off() -> None:
    """Both listeners look for columns and types this schema does not have.

    `updatedAt` is written explicitly by the thread service, and element blobs
    are plain `objectKey`/`url` text rather than FileObject columns.
    """
    config = sqlalchemy_config(URL)

    assert config.enable_touch_updated_timestamp_listener is False
    assert config.enable_file_object_listener is False


def test_alembic_config_targets_the_chainlit_schema() -> None:
    config = sqlalchemy_config(URL)

    assert config.alembic_config.version_table_name == "alembic_version"
    assert config.alembic_config.version_table_schema == "chainlit"
    assert config.alembic_config.script_location.endswith("migrations")
