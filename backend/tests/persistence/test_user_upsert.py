"""The user upsert is one statement, keyed on the identifier."""

from sqlalchemy import func, select

from chainlit.persistence import UnitOfWork
from chainlit.persistence.models import USERS


async def count_users(uow: UnitOfWork) -> int:
    result = await uow.session.execute(select(func.count()).select_from(USERS))
    return int(result.scalar_one())


async def test_first_upsert_creates_the_user(uow: UnitOfWork) -> None:
    user = await uow.users.save("alice", {"role": "admin"})

    assert user.identifier == "alice"
    assert user.metadata == {"role": "admin"}
    assert user.created_at.endswith("Z")
    assert await count_users(uow) == 1


async def test_second_upsert_keeps_the_row_and_refreshes_metadata(
    uow: UnitOfWork,
) -> None:
    """The identifier is the conflict target, so the caller's freshly minted
    id loses to the stored one — which is what the login flow needs: the same
    user must keep the same id across logins."""

    first = await uow.users.save("alice", {"role": "admin"})
    second = await uow.users.save("alice", {"role": "member"})

    assert second.id == first.id
    assert second.metadata == {"role": "member"}
    assert second.created_at == first.created_at
    assert await count_users(uow) == 1


async def test_upsert_is_visible_to_get_by_identifier(uow: UnitOfWork) -> None:
    created = await uow.users.save("bob", {})
    fetched = await uow.users.get_by_identifier("bob")

    assert fetched is not None
    assert fetched.id == created.id


async def test_unknown_identifier_reads_back_as_none(uow: UnitOfWork) -> None:
    assert await uow.users.get_by_identifier("nobody") is None
