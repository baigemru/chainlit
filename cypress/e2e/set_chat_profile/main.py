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


@cl.on_message
async def on_message(msg: cl.Message):
    if msg.content.startswith("go search"):
        await cl.context.emitter.set_chat_profile(
            "Search", start_new=True, first_message="searching knife"
        )
    elif msg.content.startswith("go ask"):
        await cl.context.emitter.set_chat_profile(
            "AskSearch", start_new=True, first_message="knife please"
        )
    elif msg.content.startswith("go unknown"):
        await cl.context.emitter.set_chat_profile("Nope", first_message="lost")
    elif msg.content.startswith("go selector"):
        await cl.context.emitter.set_chat_profile("Search", start_new=False)
    else:
        await cl.Message(
            content=f"profile: {cl.user_session.get('chat_profile')}"
        ).send()
