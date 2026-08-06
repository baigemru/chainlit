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
        cl.ChatProfile(
            name="AskSearch",
            markdown_description="Product search that asks first",
        ),
        cl.ChatProfile(
            name="ActionSearch",
            markdown_description="Product search that asks for an action first",
        ),
    ]


@cl.on_chat_start
async def on_chat_start():
    profile = cl.user_session.get("chat_profile")
    if profile == "Search":
        await cl.Message(content="search ready").send()
    elif profile == "AskSearch":
        res = await cl.AskUserMessage(content="What are you looking for?").send()
        if res:
            await cl.Message(content=f"ask answered: {res['output']}").send()
    elif profile == "ActionSearch":
        # A non-text ask cannot be answered with a text step: the first
        # message must wait instead of being sent as the reply.
        res = await cl.AskActionMessage(
            content="Pick a search mode",
            actions=[
                cl.Action(
                    id="first-action",
                    name="by_photo",
                    payload={"value": "by_photo"},
                    label="By photo",
                )
            ],
        ).send()
        if res:
            await cl.Message(content=f"action answered: {res['name']}").send()


@cl.on_message
async def on_message(msg: cl.Message):
    if msg.content.startswith("go search"):
        await cl.context.emitter.set_chat_profile(
            "Search", first_message="searching knife"
        )
    elif msg.content.startswith("go ask"):
        await cl.context.emitter.set_chat_profile(
            "AskSearch", first_message="knife please"
        )
    elif msg.content.startswith("go action"):
        await cl.context.emitter.set_chat_profile(
            "ActionSearch", first_message="knife via action"
        )
    elif msg.content.startswith("go unknown"):
        await cl.context.emitter.set_chat_profile("Nope", first_message="lost")
    elif msg.content.startswith("go echo"):
        # Redelivers the very text that triggered the switch, the case where
        # the trigger must not be shown on both sides of the divider.
        await cl.context.emitter.set_chat_profile(
            "Search", keep_transcript=True, first_message=msg.content
        )
    elif msg.content.startswith("go soft same"):
        await cl.context.emitter.set_chat_profile("Assistant", keep_transcript=True)
    elif msg.content.startswith("go soft"):
        await cl.context.emitter.set_chat_profile(
            "Search", keep_transcript=True, first_message="searching knife"
        )
    else:
        await cl.Message(
            content=f"profile: {cl.user_session.get('chat_profile')}"
        ).send()
