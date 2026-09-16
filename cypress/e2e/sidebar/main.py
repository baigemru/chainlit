import os
import uuid

import chainlit as cl

# Get the directory where the current script is located
current_directory = os.path.dirname(os.path.abspath(__file__))
# Construct the absolute path to the image and pdf files
cat_image_path = os.path.join(current_directory, "cat.jpeg")
pdf_path = os.path.join(current_directory, "dummy.pdf")


# Stable *and* uuids: a slot's elements are rows, ``elements.id`` is a uuid
# column, and ``set_slot`` refuses anything else. ``uuid5`` is how an
# application mints an id that is the same on every call, which is what
# replacement by identity needs -- without it every refresh would be
# unmount-and-mount.
def element_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"chainlit/cypress/sidebar/{name}"))


@cl.on_chat_start
async def start():
    # Two slots, so the tab strip and the switch between them are exercised.
    await cl.Sidebar.set_slot(
        "media",
        [
            cl.Image(path=cat_image_path, name="image1", id=element_id("image1")),
            cl.Pdf(path=pdf_path, name="pdf1", id=element_id("pdf1")),
        ],
        title="Test title",
    )
    await cl.Sidebar.set_slot(
        "notes",
        [
            cl.Text(
                content="Here is a side text document",
                name="text1",
                id=element_id("text1"),
            ),
            cl.Text(
                content="Here is a page text document",
                name="text2",
                id=element_id("text2"),
            ),
        ],
        title="Notes",
        activate=False,
    )


@cl.on_message
async def message(msg: cl.Message):
    if msg.content == "replace":
        await cl.Sidebar.set_slot(
            "notes",
            [cl.Text(content="Text changed!", name="text1", id=element_id("text1"))],
            title="Title changed!",
        )
        return
    if msg.content == "close":
        await cl.Sidebar.close_slot("notes")
        return
    if msg.content == "hide":
        await cl.Sidebar.hide()
        return
    await cl.Sidebar.clear()
