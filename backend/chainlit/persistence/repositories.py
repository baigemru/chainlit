"""Repositories — the plain CRUD half of the persistence package.

They exist so the services inherit advanced_alchemy's session handling,
exception wrapping and identity map. Every rule that is specific to chainlit
lives in ``services.py`` or ``statements.py``; nothing belongs here.
"""

from advanced_alchemy.repository import SQLAlchemyAsyncRepository

from chainlit.persistence.models import Element, Feedback, Step, Thread, User


class UserRepository(SQLAlchemyAsyncRepository[User]):
    model_type = User


class ThreadRepository(SQLAlchemyAsyncRepository[Thread]):
    model_type = Thread


class StepRepository(SQLAlchemyAsyncRepository[Step]):
    model_type = Step


class ElementRepository(SQLAlchemyAsyncRepository[Element]):
    model_type = Element


class FeedbackRepository(SQLAlchemyAsyncRepository[Feedback]):
    model_type = Feedback
