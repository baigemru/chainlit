from typing import Optional

import chainlit as cl


@cl.set_starter_categories
async def starter_categories(user: Optional[cl.User] = None):
    """The three densities, plus the two states a starter can be offered in.

    One fixture per density would have hidden the thing the spec is really
    about: every category is a *section* on screen at once, and the order is
    the server's.
    """
    return [
        cl.StarterCategory(
            label="Creative",
            description="the same as typing, but with a button",
            layout="tiles",
            starters=[
                cl.Starter(label="poem", message="Write a poem"),
                cl.Starter(label="story", message="Write a story"),
            ],
        ),
        cl.StarterCategory(
            label="Educational",
            description="longer runs, with what they cost",
            layout="plates",
            starters=[
                cl.Starter(
                    label="explain",
                    message="Explain something",
                    description="a walk through it, step by step",
                    caption="~2 min",
                    highlight=True,
                ),
                cl.Starter(
                    label="subscribe",
                    message="",
                    description="not available yet",
                    caption="soon",
                    disabled=True,
                ),
            ],
        ),
        cl.StarterCategory(
            label="Errands",
            layout="rows",
            collapsible=True,
            starters=[
                cl.Starter(
                    label="summarise",
                    message="Summarise something",
                    description="a paragraph out of a page",
                ),
            ],
        ),
    ]


@cl.on_message
async def on_message(msg: cl.Message):
    await cl.Message(msg.content).send()
