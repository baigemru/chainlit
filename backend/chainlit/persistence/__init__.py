"""Persistence for the Litestar rebuild.

The package is layered bottom-up:

``records``
    msgspec Structs — the only types that cross the package boundary.
``models``
    the SQLAlchemy mapping onto the deployed ``chainlit`` schema.
``statements``
    the Core statements carrying the rules that must run inside the database.
``repositories`` / ``services``
    advanced_alchemy plumbing, and the record↔model conversion on top of it.
``config``
    the engine, the alembic hookup and the unit of work.

Nothing here imports from ``chainlit.data``: the two live side by side until
the rebuild lands.
"""

from chainlit.persistence.config import (
    MIGRATIONS_PATH,
    Persistence,
    UnitOfWork,
    alembic_config,
    sqlalchemy_config,
    upgrade_database,
)
from chainlit.persistence.models import (
    SCHEMA_NAME,
    Base,
    Element,
    Feedback,
    ISOTimestamp,
    Step,
    Thread,
    User,
)
from chainlit.persistence.records import (
    ElementRecord,
    FeedbackRecord,
    PageInfoRecord,
    StepRecord,
    ThreadDetail,
    ThreadPage,
    ThreadPatch,
    ThreadQuery,
    ThreadRecord,
    UserRecord,
)
from chainlit.persistence.repositories import (
    ElementRepository,
    FeedbackRepository,
    StepRepository,
    ThreadRepository,
    UserRepository,
)
from chainlit.persistence.services import (
    ElementService,
    FeedbackService,
    StepService,
    ThreadService,
    UserService,
)

__all__ = [
    "MIGRATIONS_PATH",
    "SCHEMA_NAME",
    "Base",
    "Element",
    "ElementRecord",
    "ElementRepository",
    "ElementService",
    "Feedback",
    "FeedbackRecord",
    "FeedbackRepository",
    "FeedbackService",
    "ISOTimestamp",
    "PageInfoRecord",
    "Persistence",
    "Step",
    "StepRecord",
    "StepRepository",
    "StepService",
    "Thread",
    "ThreadDetail",
    "ThreadPage",
    "ThreadPatch",
    "ThreadQuery",
    "ThreadRecord",
    "ThreadRepository",
    "ThreadService",
    "UnitOfWork",
    "User",
    "UserRecord",
    "UserRepository",
    "UserService",
    "alembic_config",
    "sqlalchemy_config",
    "upgrade_database",
]
