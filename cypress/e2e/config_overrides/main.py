import os
from typing import Optional

import chainlit as cl
from chainlit.config import (
    ChainlitConfigOverrides,
    FeaturesSettings,
    SpontaneousFileUploadFeature,
    UISettings,
)

os.environ["CHAINLIT_AUTH_SECRET"] = "SUPER_SECRET"  # nosec B105

starters = [
    cl.Starter(
        label="Default Chat",
        message="Start a conversation with default settings",
        icon="https://picsum.photos/300",
    ),
    cl.Starter(
        label="Upload Test",
        message="Test upload functionality",
        icon="https://picsum.photos/350",
    ),
]


@cl.set_chat_profiles
async def chat_profile(current_user: cl.User):
    if current_user.metadata["role"] != "ADMIN":
        return None

    return [
        cl.ChatProfile(
            name="Default Profile",
            icon="https://picsum.photos/250",
            markdown_description="Standard profile with default features. This profile uses **default settings** without any special configurations.",
            starters=starters,
        ),
        cl.ChatProfile(
            name="Upload Enabled",
            markdown_description="Profile with **file upload enabled**. This profile has *spontaneous file upload* activated. [Learn more](https://example.com/upload)",
            icon="https://picsum.photos/250",
            starters=starters,
            config_overrides=ChainlitConfigOverrides(
                ui=UISettings(name="Upload UI"),
                features=FeaturesSettings(
                    spontaneous_file_upload=SpontaneousFileUploadFeature(enabled=True)
                ),
            ),
        ),
        cl.ChatProfile(
            name="Upload Disabled",
            markdown_description="Profile with **file upload explicitly disabled**. This ensures no upload button is available.",
            icon="https://picsum.photos/200",
            starters=starters,
            config_overrides=ChainlitConfigOverrides(
                features=FeaturesSettings(
                    spontaneous_file_upload=SpontaneousFileUploadFeature(enabled=False)
                )
            ),
        ),
    ]


@cl.password_auth_callback
def auth_callback(username: str, password: str) -> Optional[cl.User]:
    if (username, password) == ("admin", "admin"):
        return cl.User(identifier="admin", metadata={"role": "ADMIN"})
    else:
        return None


@cl.on_message
async def on_message():
    user = cl.user_session.get("user")
    chat_profile = cl.user_session.get("chat_profile")
    await cl.Message(
        content=f"starting chat with {user.identifier} using the {chat_profile} chat profile"
    ).send()
