import chainlit as cl


@cl.on_chat_start
async def main():
    res = await cl.AskUserMessage(content="What is your name?", timeout=120).send()
    if res is None:
        await cl.Message(content="Name ask timed out").send()
        return

    await cl.Message(content=f"Your name is: {res['output']}").send()

    # Answering "timeout" requests a short deadline so the timeout scenario
    # doesn't slow down (or flake) the click scenarios, which get a deadline
    # far above any CI hiccup. 20s still leaves room for a slow-CI reload.
    action_timeout = 20 if res["output"] == "timeout" else 120

    action_res = await cl.AskActionMessage(
        content="Pick an action!",
        timeout=action_timeout,
        actions=[
            cl.Action(
                id="continue-action",
                name="continue",
                payload={"value": "continue"},
                label="Continue",
            ),
        ],
    ).send()

    if action_res is not None:
        await cl.Message(
            content=f"Action picked: {action_res['payload']['value']}"
        ).send()
