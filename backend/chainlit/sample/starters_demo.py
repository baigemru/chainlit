"""Every field a starter, a category and a profile can carry, in one app.

The smoke test and the Cypress fixture for the welcome screen: if a layout,
a disabled tile, a door profile or a composer hint renders wrong, it renders
wrong here first. Nothing below means anything -- the words are filler; what
is exercised is the combination.
"""

from typing import Optional

import chainlit as cl


@cl.set_chat_profiles
async def chat_profiles(user: Optional[cl.User] = None):
    return [
        cl.ChatProfile(
            name="Assistant",
            markdown_description="The one profile a visitor is offered.",
            default=True,
            composer_hint="Enter sends. **Shift+Enter** starts a new line.",
        ),
        # A door: reached only by the "Open the archive" starter below. The
        # switcher has one offered profile left, so it disappears entirely.
        cl.ChatProfile(
            name="Archive",
            markdown_description="Reached through a starter, never chosen.",
            listed=False,
            composer_hint="Ask for a year, a name or a number.",
        ),
    ]


@cl.set_starter_categories
async def starter_categories(user: Optional[cl.User] = None):
    return [
        cl.StarterCategory(
            label="Quick",
            description="One click, one answer.",
            layout="tiles",
            icon="https://cdn-icons-png.flaticon.com/512/3094/3094837.png",
            starters=[
                cl.Starter(
                    label="Write a poem about nature",
                    message="Write a poem about nature",
                ),
                cl.Starter(
                    label="Create a short story",
                    message="Create a short story about adventure",
                    caption="~1 min",
                ),
            ],
        ),
        cl.StarterCategory(
            label="Runs",
            description="Longer work, with a second line saying what it does.",
            layout="plates",
            icon="https://cdn-icons-png.flaticon.com/512/3976/3976625.png",
            starters=[
                cl.Starter(
                    label="Explain a complex topic",
                    description="Picks the topic apart and rebuilds it in plain words.",
                    message="Explain quantum computing in simple terms",
                    caption="~3 min",
                    highlight=True,
                ),
                # A door-starter: switches the profile on the client, sends
                # nothing. `message=""` is the whole point -- there is no
                # "switch and then say this".
                cl.Starter(
                    label="Open the archive",
                    description="Switches to a profile the switcher does not offer.",
                    message="",
                    profile="Archive",
                ),
                # Visible and inert, which is what an application does with
                # something it is not ready to honour yet.
                cl.Starter(
                    label="Set up a subscription",
                    description="Not wired up in this sample.",
                    message="",
                    caption="soon",
                    disabled=True,
                ),
            ],
        ),
        cl.StarterCategory(
            label="Errands",
            description="A long tail, folded away on a phone.",
            layout="rows",
            collapsible=True,
            icon="https://cdn-icons-png.flaticon.com/512/1055/1055646.png",
            starters=[
                cl.Starter(
                    label="Summarize a topic",
                    message="Summarize the key points of machine learning",
                ),
                cl.Starter(
                    label="Create a plan",
                    description="A week of study, laid out.",
                    message="Help me create a weekly study plan",
                ),
                # Not a chat at all: the client navigates, the thread is left
                # alone. `href` wins over `profile` and `message`.
                cl.Starter(
                    label="Your account",
                    description="Goes to a page instead of saying anything.",
                    message="",
                    href="/account?tab=settings",
                ),
            ],
        ),
    ]


@cl.on_message
async def on_message(msg: cl.Message):
    await cl.Message(f"You said: {msg.content}").send()
