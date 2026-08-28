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
``writer``
    the ordered per-session writer everything above is driven through.

The re-exports below are a convenience for tests and host apps; the package's
own modules and the controllers import from the submodules directly.
"""

from chainlit.persistence.config import (
    MIGRATIONS_PATH,
    Persistence,
    UnitOfWork,
    isolated,
    sqlalchemy_config,
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
    PageCursor,
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
    InvalidIdError,
    StepService,
    ThreadService,
    UserService,
)
from chainlit.persistence.writer import (
    DeleteElement,
    DeleteStep,
    Op,
    PatchThread,
    SaveElement,
    SaveStep,
    SessionWriter,
    WriterRegistry,
)

__all__ = [
    "MIGRATIONS_PATH",
    "SCHEMA_NAME",
    "Base",
    "DeleteElement",
    "DeleteStep",
    "Element",
    "ElementRecord",
    "ElementRepository",
    "ElementService",
    "Feedback",
    "FeedbackRecord",
    "FeedbackRepository",
    "FeedbackService",
    "ISOTimestamp",
    "InvalidIdError",
    "Op",
    "PageCursor",
    "PageInfoRecord",
    "PatchThread",
    "Persistence",
    "SaveElement",
    "SaveStep",
    "SessionWriter",
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
    "WriterRegistry",
    "isolated",
    "sqlalchemy_config",
]
