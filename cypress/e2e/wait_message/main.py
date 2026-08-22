import asyncio

import chainlit as cl

WAIT_TEXTS = ["текст 1", "текст 2", "текст 3"]


@cl.on_message
async def on_message(message: cl.Message):
    if message.content == "next":
        # Scenario: a newer message deactivates wait mode on the loader.
        loader = cl.Message(
            content="",
            wait=WAIT_TEXTS,
            wait_interval=2,
        )
        await loader.send()
        await asyncio.sleep(3)
        await cl.Message(content="follow-up message").send()
    else:
        # Scenario: rotation, then update() ends wait mode with the result.
        loader = cl.Message(
            content="",
            wait=WAIT_TEXTS,
            wait_interval=2,
        )
        await loader.send()
        await asyncio.sleep(10)
        loader.content = "Готово: финальный ответ."
        await loader.update()
