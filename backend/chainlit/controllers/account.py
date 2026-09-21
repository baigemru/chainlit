"""The account page's routes: read it, save it, press a button on it.

``/project/account``, not ``/account``: the second is a client-side route and
a server handler there would take the page away from the SPA.

There is no ``[UI]`` mirror of "is an account registered". The page asks this
controller and a 404 here *is* the not-configured state -- one fact, in one
place, answered by the thing that knows it.

The action route borrows rather than reimplements: the page it may answer with
is the one ``build_page`` produces for the other two, and the conversation it
may open is minted by ``transit_store.mint_handover``, which is the same pair
of steps ``emitter.set_chat_profile`` takes. A second way to hand a message to
a successor session would be a second set of rules about who may claim it.
"""

from __future__ import annotations

from typing import Any, Optional, Type

import msgspec
from litestar import Controller, get, post, put
from litestar.connection import ASGIConnection
from litestar.di import NamedDependency
from litestar.exceptions import (
    ClientException,
    MethodNotAllowedException,
    NotAuthorizedException,
    NotFoundException,
    ValidationException,
)
from litestar.handlers.base import BaseRouteHandler
from litestar.params import FromPath, SkipValidation
from litestar.status_codes import HTTP_428_PRECONDITION_REQUIRED
from litestar.types import Empty

import chainlit.config
from chainlit.account import (
    AccountActionCall,
    AccountActionResponse,
    AccountPage,
    AccountSave,
    OpenThread,
    OpenThreadOutcome,
    PageOutcome,
    Refresh,
    Toast,
    as_account,
    build_page,
    call_hook,
    decode_stored,
    element_type_at,
    merge_account,
    strip_readonly,
)
from chainlit.account_badge import push_account_badge
from chainlit.controllers.caller import caller
from chainlit.controllers.sessions import UserSessions
from chainlit.persistence.services import UserService
from chainlit.security import AuthedRequest, Identity
from chainlit.transit_store import TransitStore, mint_handover

__all__ = ("AccountController", "require_identity")


async def require_identity(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Refuse a caller the scope has no user for.

    A guard rather than a check inside each handler: it runs before the
    handler's dependencies are resolved, so an anonymous request never reaches
    a database session. The scope is read the way ``caller`` reads it -- in a
    deployment with no ``CHAINLIT_AUTH_SECRET`` the ``user`` property *raises*
    instead of answering ``None``, and these values are stored per identifier,
    so an app with no login has nobody to store them for.
    """
    user = connection.scope.get("user", Empty)
    if user is Empty or user is None:
        raise NotAuthorizedException("The account page requires a signed-in user.")


class AccountController(Controller):
    """Read and write the values of the Struct an app registered."""

    path = "/project/account"
    guards = [require_identity]

    @get()
    async def read(
        self,
        request: AuthedRequest,
        sessions: NamedDependency[UserSessions],
        user_service: SkipValidation[NamedDependency[Optional[UserService]]] = None,
    ) -> AccountPage:
        """The form, the values behind it, and whether it can be saved."""
        cls = _registered()
        identity = caller(request)
        account = await _render(cls, identity, user_service)
        page = build_page(account, readonly=_readonly(user_service))
        # After the values, not before: the application marks things seen
        # inside ``on_account_load``, so the count computed ahead of it is
        # the one the user is looking at rather than the one that is left.
        # And the badge hook is *handed* the marked values, because that
        # write belongs to this session and commits in ``before_send`` -- a
        # hook that read the row itself would answer from before the mark.
        # ``strip_readonly`` so it sees what the store holds, which is what
        # the other two call sites read for it.
        await push_account_badge(sessions, identity, strip_readonly(account))
        return page

    @put()
    async def write(
        self,
        request: AuthedRequest,
        data: AccountSave,
        user_service: SkipValidation[NamedDependency[Optional[UserService]]] = None,
    ) -> AccountPage:
        """Validate, hand to the app, merge onto the row, and answer with it.

        ``values`` is typed as a plain object because the Struct is only known
        at runtime -- the application registers it as it imports -- so the
        conversion happens here instead of at the signature. ``msgspec`` names
        the offending field (``$.calculation.margin``) and that message is the
        only field addressing the client has, so it is passed through rather
        than replaced with a generic refusal.

        ``base`` is the page the form was filled from, and it is what makes
        this a save of the user's *edit* rather than of their whole document.
        It is decoded the lenient way the stored row is -- it was that row a
        moment ago, and an application that has since renamed a field must not
        turn every open tab's save into a 400 -- while ``values`` is converted
        strictly, because that half is input and refusing it is the point.

        What is converted is not quite what is stored: ``strip_readonly``
        runs first, so a derived leaf the browser echoed back cannot be
        written over the value the application computes. It runs *before*
        ``on_account_update`` too -- the hook is shown the save, not what was
        posted -- and before the merge, which is deliberate: the hook is the
        application's reading of what *this user* asked for, and handing it
        the merged document would let it rewrite, as if the user had asked,
        whatever another writer had just put there.
        """
        cls = _registered()
        code = chainlit.config.config.code
        readonly = _readonly(user_service)
        if readonly:
            raise MethodNotAllowedException(
                "This application has nowhere to store account values: it "
                "registered no on_account_update and has no data layer."
            )
        if data.base is None:
            # Not a compatibility branch: a save that cannot say what it was
            # editing can only be stored by overwriting the document, and
            # overwriting is exactly what silently dropped the other writer's
            # work. The client is shipped inside this package, so the only
            # caller that can land here is one that skipped the GET.
            raise ClientException(
                status_code=HTTP_428_PRECONDITION_REQUIRED,
                detail=(
                    "A save must carry `base`, the account page it was made "
                    "from: only the difference between the two is stored, so "
                    "a write that arrived while the page was open is kept."
                ),
            )

        try:
            posted = msgspec.convert(data.values, cls)
        except msgspec.ValidationError as error:
            raise ValidationException(detail=str(error)) from error

        base = strip_readonly(decode_stored(data.base, cls))
        account = strip_readonly(posted)
        identity = caller(request)
        message: Optional[str] = None
        if code.on_account_update is not None:
            returned = await call_hook(code.on_account_update, identity, account)
            # Anything but a string was not asking for a toast.
            message = returned if isinstance(returned, str) and returned else None
        stored = await _store_merged(base, account, cls, identity, user_service)

        # Rebuilt the way the GET builds it, not echoed back: the app's own
        # load hook may normalise what it was handed, and the page must show
        # what is now stored rather than what was posted -- which is also how
        # a concurrent arrival the merge kept reaches the form that saved over
        # it. ``baseline=stored`` rather than a second SELECT: it is the same
        # session that has just written it, so the read could only answer with
        # this value.
        return build_page(
            await _render(cls, identity, user_service, baseline=stored),
            readonly=readonly,
            message=message,
        )

    @post("/actions/{name:str}")
    async def act(
        self,
        request: AuthedRequest,
        name: FromPath[str],
        data: AccountActionCall,
        transit: NamedDependency[TransitStore],
        user_service: SkipValidation[NamedDependency[Optional[UserService]]] = None,
    ) -> AccountActionResponse:
        """Run the hook a button on the page names, and say what follows.

        Three refusals before any application code runs, in order of cost:
        an action nobody registered is a 404, a ``path`` the registered
        Struct cannot address is a 400, and an ``item`` that does not fit
        the type the *schema* says sits at that path is a 400 carrying
        msgspec's own message -- which names the offending field and is the
        only field addressing the client has.
        """
        cls = _registered()
        hook = chainlit.config.config.code.account_actions.get(name)
        if hook is None:
            raise NotFoundException(f"No account action named {name!r}.")

        try:
            element_type = element_type_at(cls, data.path)
        except LookupError as error:
            raise ValidationException(detail=str(error)) from error

        item: Any = None
        if data.item is not None:
            # A tab action addresses a Struct too, but its item is null and
            # converting the tab itself would hand the hook a copy of the
            # whole tab the user never edited.
            try:
                item = msgspec.convert(data.item, element_type)
            except msgspec.ValidationError as error:
                raise ValidationException(detail=str(error)) from error

        identity = caller(request)
        outcome = await call_hook(hook, identity, item)

        if isinstance(outcome, Toast):
            return AccountActionResponse(outcome=outcome)
        if isinstance(outcome, Refresh):
            # An ``account`` on the refresh is stored here, with the request's
            # own session, and the page is then drawn from it. The action that
            # dismissed a notice therefore does not need a session of its own
            # -- and cannot show the page a value it has already retired.
            baseline = await _store_action_values(
                outcome.account, cls, identity, user_service
            )
            return AccountActionResponse(
                outcome=PageOutcome(
                    page=build_page(
                        await _render(cls, identity, user_service, baseline=baseline),
                        readonly=_readonly(user_service),
                    ),
                    message=outcome.message,
                )
            )
        if isinstance(outcome, OpenThread):
            # The same handover a profile switch performs, from a page
            # instead of from a session: the engine mints the thread and
            # parks the record, the browser opens it, and the socket that
            # arrives claims it. ``owner`` is the caller -- the route runs
            # behind the guard, so there is always one.
            owner = identity.identifier if identity is not None else None
            thread_id = await mint_handover(
                transit,
                outcome.transit_message,
                owner,
                parent=outcome.parent,
            )
            return AccountActionResponse(
                outcome=OpenThreadOutcome(
                    thread_id=thread_id,
                    chat_profile=outcome.chat_profile,
                    has_transit_message=outcome.transit_message is not None,
                )
            )
        raise TypeError(
            f"@cl.account_action({name!r}) returned {outcome!r}. It must "
            "return cl.AccountToast, cl.AccountRefresh or cl.AccountOpenThread."
        )


def _registered() -> Type[msgspec.Struct]:
    cls = chainlit.config.config.code.account
    if cls is None:
        raise NotFoundException("This application has not declared an account page.")
    return cls


def _readonly(user_service: Optional[UserService]) -> bool:
    """True when a save would have nowhere to go."""
    return (
        chainlit.config.config.code.on_account_update is None and user_service is None
    )


async def _render(
    cls: Type[msgspec.Struct],
    identity: Optional[Identity],
    user_service: Optional[UserService],
    *,
    baseline: Optional[msgspec.Struct] = None,
) -> msgspec.Struct:
    """The values to show, and the write the load hook may have asked for.

    The hook is handed what the engine has just read with *this request's*
    session, so an application no longer reads the row a second time through
    a session of its own -- which, after a ``PUT``, answered with the values
    from before the save, because the request's session commits in
    ``before_send``.

    What the hook returns is the page, and it is also allowed to be a change:
    marking a feed seen is a legitimate write on a ``GET``. So the two are
    compared -- both through ``strip_readonly``, or a derived field the hook
    recomputed would look like a change on every single load -- and the store
    is touched only when they differ. That write goes through the same merge a
    ``PUT`` does: the hook may spend a second on the network before it answers,
    and the arrival that lands in that second must not be undone by a page
    load.

    The page that comes back is the hook's, not the merge's, so a document
    that changed underneath is shown on the next load rather than half-drawn
    from two: what the hook returned is the only version of it that has the
    derived leaves filled in.

    ``baseline`` short-circuits the read for a caller that has just written
    the values itself and so already knows them.
    """
    code = chainlit.config.config.code
    if baseline is None:
        baseline = await _stored(cls, identity, user_service)
    if code.on_account_load is None:
        return baseline

    account = as_account(
        await call_hook(code.on_account_load, identity, baseline),
        cls,
        hook="@cl.on_account_load",
    )
    kept = strip_readonly(baseline)
    keep = strip_readonly(account)
    if keep != kept:
        await _store_merged(kept, keep, cls, identity, user_service)
    return account


async def _store_merged(
    base: msgspec.Struct,
    account: msgspec.Struct,
    cls: Type[msgspec.Struct],
    identity: Optional[Identity],
    user_service: Optional[UserService],
) -> msgspec.Struct:
    """Write what changed between ``base`` and ``account``, and return the row.

    The read and the write are one transaction and the read holds the row,
    which is the whole of the guarantee: between them there is no moment where
    another writer's commit can pass unseen. The lock is released when the
    request's session commits, in ``before_send`` -- so it also spans the load
    hook that draws the answer, which is application code. That is the price
    of showing the document that was actually stored, and the hook that runs
    there is the one an application already runs on every page load.

    With nowhere to store -- no persistence, or no identity to store under --
    there is nothing to merge with and the caller's own values are the answer.
    """
    if user_service is None or identity is None:
        return account
    current = decode_stored(await user_service.locked_account(identity.identifier), cls)
    merged = merge_account(base, account, current)
    await user_service.set_account(identity.identifier, msgspec.to_builtins(merged))
    return merged


async def _stored(
    cls: Type[msgspec.Struct],
    identity: Optional[Identity],
    user_service: Optional[UserService],
) -> msgspec.Struct:
    """What the engine holds for this user: the row, else the defaults."""
    if user_service is not None and identity is not None:
        return decode_stored(await user_service.get_account(identity.identifier), cls)
    return cls()


async def _store_action_values(
    account: Any,
    cls: Type[msgspec.Struct],
    identity: Optional[Identity],
    user_service: Optional[UserService],
) -> Optional[msgspec.Struct]:
    """Store the ``account`` an ``AccountRefresh`` carried, if it carried one.

    Unconditional, unlike the load hook's write: the action said to store
    these, and an action that ran and changed nothing simply leaves
    ``account`` as ``None``. The return is the baseline the page is then drawn
    from -- ``None`` when there was nothing, so the read happens as usual.

    Stored stripped and returned whole: what goes into the row is the same
    thing a ``PUT`` would put there, but the derived leaves the action worked
    out are the freshest there are, so they are what the page is drawn from
    and what the load hook is handed.

    Not merged, unlike the other two writes, and this is the one place where
    the whole document still wins: the hook was handed an item and built its
    answer from a read of its own, so there is no "what the page showed" for a
    difference to be taken against. Holding the row across the hook would buy
    one instead, at the price of a lock held over whatever network the action
    talks to. An action that changes one thing should return the values it was
    handed with that one thing changed.
    """
    if account is None:
        return None
    shown = as_account(account, cls, hook="cl.AccountRefresh(account=...)")
    if user_service is not None and identity is not None:
        await user_service.set_account(
            identity.identifier, msgspec.to_builtins(strip_readonly(shown))
        )
    return shown
