import chainlit as cl


@cl.on_chat_start
async def on_chat_start():
    await cl.Message(content="Hi from copilot!").send()


@cl.on_message
async def on_message(msg: cl.Message):
    if cl.context.session.client_type == "copilot":
        # The host page injects this through `sendChainlitMessage`. The
        # opposite direction -- the widget calling a function *in* the host
        # page -- is gone with the rpc tags: nothing in the consumer embeds
        # the copilot, so the callId machinery had no user.
        if msg.type == "system_message":
            await cl.Message(content=f"System message received: {msg.content}").send()
            return

    await cl.Message(content=f"Echo: {msg.content}").send()
