"""Every field a starter, a category and a profile can carry, in one app.

The smoke test and the Cypress fixture for the welcome screen: if a layout,
a disabled tile, a door profile or a composer hint renders wrong, it renders
wrong here first. Nothing below means anything -- the words are filler; what
is exercised is the combination.
"""

from typing import Optional
from urllib.parse import quote

import chainlit as cl


def icon(glyph: str) -> str:
    """A one-glyph icon as a data URI.

    Drawn here and not fetched: the sample is the smoke test, and an icon
    that comes off a CDN turns "the section icon does not render" into a
    question about somebody's hotlink policy.
    """
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        f'<text x="16" y="24" font-size="24" text-anchor="middle">{glyph}</text>'
        "</svg>"
    )
    return "data:image/svg+xml;utf8," + quote(svg)


@cl.set_chat_profiles
async def chat_profiles(user: Optional[cl.User] = None):
    return [
        # The icon is what makes the welcome screen draw the description at
        # all, and the description is two blocks on purpose: the heading the
        # screen opens with, and the line under it that says what to do next.
        cl.ChatProfile(
            name="Assistant",
            icon=icon("🐼"),
            markdown_description=(
                "### What are we looking for?\n\n"
                "Type it, attach a photo or press a button — I take it from there."
            ),
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
            icon=icon("⚡"),
            starters=[
                # Two tiles of equal standing, one of them the section's
                # preference: the highlight is a border and a colour, not a
                # banner across the row.
                cl.Starter(
                    label="📝 Write a poem",
                    message="Write a poem about nature",
                    caption="2 credits",
                    highlight=True,
                ),
                cl.Starter(
                    label="📖 Tell a story",
                    message="Create a short story about adventure",
                    caption="~1 min",
                ),
            ],
        ),
        cl.StarterCategory(
            label="Runs",
            description="Longer work, with a second line saying what it does.",
            layout="plates",
            icon=icon("🧭"),
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
            icon=icon("🗂"),
            starters=[
                cl.Starter(
                    label="Summarize",
                    description="a topic → the key points of it, in order",
                    message="Summarize the key points of machine learning",
                    caption="1 credit",
                ),
                cl.Starter(
                    label="Create a plan",
                    description="a subject → a week of study, laid out",
                    message="Help me create a weekly study plan",
                    caption="2 credits",
                ),
                # Not a chat at all: the client navigates, the thread is left
                # alone. `href` wins over `profile` and `message`.
                cl.Starter(
                    label="Your account",
                    description="goes to a page instead of saying anything",
                    message="",
                    href="/account?tab=settings",
                    caption="free",
                ),
            ],
        ),
    ]


@cl.on_message
async def on_message(msg: cl.Message):
    await cl.Message(f"You said: {msg.content}").send()
