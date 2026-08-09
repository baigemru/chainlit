import chainlit as cl


@cl.set_chat_profiles
async def chat_profile(current_user):
    return [
        cl.ChatProfile(
            name="Assistant",
            markdown_description="General assistant",
            default=True,
        ),
        cl.ChatProfile(
            name="Search",
            markdown_description="Product search",
        ),
    ]


@cl.on_chat_start
async def on_chat_start():
    profile = cl.user_session.get("chat_profile")
    if profile == "Search":
        await cl.Message(content="search ready").send()
        # Echoed by the assistant on purpose: the value must never appear as
        # a user reply — the spec asserts on [data-step-type].
        transit = cl.user_session.get("transit_message")
        if transit is not None:
            cl.user_session.set("transit_message", None)
            await cl.Message(content=f"transit: {transit}").send()


@cl.on_message
async def on_message(msg: cl.Message):
    if msg.content.startswith("go search"):
        await cl.context.emitter.set_chat_profile(
            "Search", transit_message="searching knife"
        )
    elif msg.content.startswith("go unknown"):
        await cl.context.emitter.set_chat_profile("Nope", transit_message="lost")
    elif msg.content.startswith("go empty"):
        # No transit parked: the new chat must not inherit one from an
        # earlier switch either.
        await cl.context.emitter.set_chat_profile("Search")
    elif msg.content.startswith("go echo"):
        # Hands over the very text that triggered the switch. The app
        # re-reads it in on_chat_start, but nothing sends it back as a user
        # message, so no second switch can occur.
        await cl.context.emitter.set_chat_profile(
            "Search", keep_transcript=True, transit_message=msg.content
        )
    elif msg.content.startswith("go soft same"):
        await cl.context.emitter.set_chat_profile("Assistant", keep_transcript=True)
    elif msg.content.startswith("go soft"):
        await cl.context.emitter.set_chat_profile(
            "Search", keep_transcript=True, transit_message="searching knife"
        )
    else:
        await cl.Message(
            content=f"profile: {cl.user_session.get('chat_profile')}"
        ).send()
