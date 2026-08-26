import os
from uuid import uuid4

from fastapi import Request, Response

import chainlit as cl
from chainlit.auth import create_jwt
from chainlit.server import _authenticate_user, app
from chainlit.user import User

os.environ["CHAINLIT_AUTH_SECRET"] = "SUPER_SECRET"  # nosec B105
os.environ["CHAINLIT_CUSTOM_AUTH"] = "true"


@app.get("/auth/custom")
async def custom_auth(request: Request) -> Response:
    user_id = str(uuid4())

    user = User(identifier=user_id, metadata={"role": "user"})
    response = await _authenticate_user(request, user)

    return response


@app.get("/auth/token")
async def custom_token_auth() -> Response:
    user_id = str(uuid4())

    user = User(identifier=user_id, metadata={"role": "admin"})
    response = create_jwt(user)

    return response


def _holds_catch_all(route) -> bool:
    """The SPA catch-all itself, or — from FastAPI 0.137 on, where an included
    router stays a single object instead of being copied out — whatever holds
    it."""
    from fastapi.routing import iter_route_contexts

    return any(
        context.path == "/{full_path:path}" for context in iter_route_contexts([route])
    )


# Push it behind the routes declared above so they win over the UI.
catch_all_route = next((route for route in app.routes if _holds_catch_all(route)), None)

if catch_all_route:
    app.routes.remove(catch_all_route)
    app.routes.append(catch_all_route)


@cl.on_chat_start
async def on_chat_start():
    user = cl.user_session.get("user")
    await cl.Message(f"Hello {user.identifier}").send()


@cl.on_message
async def on_message(msg: cl.Message):
    await cl.Message(content=f"Echo: {msg.content}").send()
