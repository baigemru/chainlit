"""`users.account`: the column revision 0004 adds, and the two statements
that read and write it.

The interesting one is the last: `metadata` is rewritten whole at every
sign-in, which is the reason the account values are not a key inside it.
"""

import uuid
from typing import Any, Dict

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from chainlit.persistence import UnitOfWork
from chainlit.persistence.models import SCHEMA_NAME, USERS
from tests.persistence.conftest import at, drop_schema, iso, migrate, new_id

# Written while the database is at 0003, where `account` does not exist yet.
USERS_0003 = sa.table(
    "users",
    sa.column("id", sa.Uuid()),
    sa.column("identifier", sa.Text()),
    sa.column("metadata", sa.JSON()),
    sa.column("createdAt", sa.Text()),
    schema=SCHEMA_NAME,
)


async def test_0004_adds_the_column_with_an_empty_object_for_existing_rows(
    engine: AsyncEngine,
) -> None:
    """No backfill: `NOT NULL DEFAULT '{}'` makes every row that predates the
    revision valid the moment the column appears."""
    await drop_schema(engine)
    await migrate(engine, "0003_one_feedback_per_step")

    async with engine.begin() as connection:
        await connection.execute(
            USERS_0003.insert(),
            [
                {
                    "id": uuid.UUID(new_id()),
                    "identifier": "ada",
                    "metadata": {"role": "admin"},
                    "createdAt": iso(at()),
                }
            ],
        )

    await migrate(engine, "head")

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                sa.select(USERS.c["account"], USERS.c["metadata"]).where(
                    USERS.c["identifier"] == "ada"
                )
            )
        ).one()
        nullable = (
            await connection.execute(
                sa.text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'users' "
                    "AND column_name = 'account'"
                ),
                {"schema": SCHEMA_NAME},
            )
        ).scalar_one()

    assert row.account == {}
    # The row's own metadata is left alone.
    assert row.metadata == {"role": "admin"}
    assert nullable == "NO"


async def test_an_unknown_user_reads_back_as_an_empty_object(uow: UnitOfWork) -> None:
    assert await uow.users.get_account("nobody") == {}


async def test_set_account_creates_the_row_when_no_login_has(
    uow: UnitOfWork,
) -> None:
    """The user signed in through a token this deployment minted and never
    called `/user`; the save must not be a duplicate-key error."""
    await uow.users.set_account("ada", {"notify": False})

    assert await uow.users.get_account("ada") == {"notify": False}
    record = await uow.users.get_by_identifier("ada")
    assert record is not None
    assert record.metadata == {}


async def test_set_account_leaves_the_login_metadata_alone(uow: UnitOfWork) -> None:
    """The account upsert names `account` in its conflict clause and nothing
    else. Adding `metadata` there would write the empty object its INSERT half
    carries over whatever the last sign-in stored."""
    await uow.users.save("ada", {"role": "admin"})

    await uow.users.set_account("ada", {"notify": False})

    record = await uow.users.get_by_identifier("ada")
    assert record is not None
    assert record.metadata == {"role": "admin"}


async def test_set_account_overwrites_the_stored_values(uow: UnitOfWork) -> None:
    await uow.users.set_account("ada", {"notify": False, "plan": "pro"})
    await uow.users.set_account("ada", {"notify": True})

    # A whole-object write, not a merge: the Struct is the document, and a
    # field the app retired has to be able to leave.
    assert await uow.users.get_account("ada") == {"notify": True}


async def test_a_login_after_a_save_leaves_the_account_intact(
    uow: UnitOfWork,
) -> None:
    """`upsert_user` sets `metadata` from `excluded` and nothing else. This is
    the whole reason the account values are a column instead of a key in it."""
    created = await uow.users.save("ada", {"role": "admin"})
    await uow.users.set_account("ada", {"notify": False, "plan": "pro"})

    again = await uow.users.save("ada", {"role": "member"})

    assert again.id == created.id
    assert again.metadata == {"role": "member"}
    assert await uow.users.get_account("ada") == {"notify": False, "plan": "pro"}


async def test_a_save_after_a_set_account_keeps_the_row_id(uow: UnitOfWork) -> None:
    """`set_account` mints an id for a row that may not exist; the login that
    follows must find that row rather than collide with it."""
    await uow.users.set_account("ada", {"plan": "pro"})
    minted = await uow.users.get_by_identifier("ada")
    assert minted is not None

    logged_in = await uow.users.save("ada", {"role": "admin"})

    assert logged_in.id == minted.id
    assert await uow.users.get_account("ada") == {"plan": "pro"}


async def test_the_login_upsert_gives_a_new_row_an_empty_account(
    uow: UnitOfWork,
) -> None:
    await uow.users.save("ada", {})

    stored: Dict[str, Any] = await uow.users.get_account("ada")
    assert stored == {}


async def test_two_users_do_not_share_an_account(uow: UnitOfWork) -> None:
    await uow.users.set_account("ada", {"plan": "pro"})
    await uow.users.set_account("bob", {"plan": "free"})

    assert await uow.users.get_account("ada") == {"plan": "pro"}
    assert await uow.users.get_account("bob") == {"plan": "free"}
