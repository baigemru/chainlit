import os

import chainlit as cl

# Get the directory where the current script is located
current_directory = os.path.dirname(os.path.abspath(__file__))
# Construct the absolute path to the image and pdf files
cat_image_path = os.path.join(current_directory, "cat.jpeg")
pdf_path = os.path.join(current_directory, "dummy.pdf")


# Stable ids, so a refresh of a slot updates an element in place instead of
# unmounting and mounting it. They are the application's names: the engine
# mints the row id from the thread, the slot and the name.
@cl.on_chat_start
async def start():
    # Two slots, so the tab strip and the switch between them are exercised.
    await cl.Sidebar.set_slot(
        "media",
        [
            cl.Image(path=cat_image_path, name="image1", id="image1"),
            cl.Pdf(path=pdf_path, name="pdf1", id="pdf1"),
        ],
        title="Test title",
    )
    await cl.Sidebar.set_slot(
        "notes",
        [
            cl.Text(content="Here is a side text document", name="text1", id="text1"),
            cl.Text(content="Here is a page text document", name="text2", id="text2"),
        ],
        title="Notes",
        activate=False,
    )


@cl.on_message
async def message(msg: cl.Message):
    if msg.content == "replace":
        await cl.Sidebar.set_slot(
            "notes",
            [cl.Text(content="Text changed!", name="text1", id="text1")],
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
