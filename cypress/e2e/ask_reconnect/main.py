import chainlit as cl


@cl.on_chat_start
async def main():
    res = await cl.AskUserMessage(content="What is your name?", timeout=120).send()
    if res is None:
        await cl.Message(content="Name ask timed out").send()
        return

    await cl.Message(content=f"Your name is: {res['output']}").send()

    action_res = await cl.AskActionMessage(
        content="Pick an action!",
        timeout=20,
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
