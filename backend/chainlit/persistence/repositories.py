"""Repositories — the plain CRUD half of the persistence package.

They exist so the services inherit advanced_alchemy's session handling,
exception wrapping and identity map. Every rule that is specific to chainlit
lives in ``services.py`` or ``statements.py``; nothing belongs here.
"""

from advanced_alchemy.extensions.litestar import repository

from chainlit.persistence.models import Element, Feedback, Step, Thread, User


class UserRepository(repository.SQLAlchemyAsyncRepository[User]):
    model_type = User


class ThreadRepository(repository.SQLAlchemyAsyncRepository[Thread]):
    model_type = Thread


class StepRepository(repository.SQLAlchemyAsyncRepository[Step]):
    model_type = Step


class ElementRepository(repository.SQLAlchemyAsyncRepository[Element]):
    model_type = Element


class FeedbackRepository(repository.SQLAlchemyAsyncRepository[Feedback]):
    model_type = Feedback
