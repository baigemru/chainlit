"""Where an autoscrolled assistant message lands.

``assistant_message_anchor`` never leaves ``FeaturesSettings``: the section is
dumped whole with ``msgspec.to_builtins`` (``controllers/project.py``) and the
scroll container reads it off ``config.features``. So the decode path is the
whole contract — a TOML table has to produce what the constructor does, and a
value the client cannot act on has to be refused here rather than in a browser.
"""

import msgspec
import pytest

from chainlit.config import FeaturesSettings


class TestAssistantMessageAnchor:
    def test_default_follows_the_stream(self):
        # Existing deployments never wrote the key; they must keep the
        # bottom-follow they were built against.
        assert FeaturesSettings().assistant_message_anchor == "bottom"

    def test_section_without_the_key_still_decodes(self):
        features = msgspec.convert(
            {"assistant_message_autoscroll": True}, type=FeaturesSettings
        )

        assert features.assistant_message_anchor == "bottom"

    def test_toml_can_pin_the_reply_to_the_top(self):
        features = msgspec.convert(
            {"assistant_message_anchor": "top"}, type=FeaturesSettings
        )

        assert features.assistant_message_anchor == "top"

    def test_an_unknown_landing_is_refused(self):
        # The client branches on exactly two strings; anything else would
        # silently fall through to bottom-follow with no error anywhere.
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(
                {"assistant_message_anchor": "middle"}, type=FeaturesSettings
            )

    def test_the_key_reaches_the_client_payload(self):
        payload = msgspec.to_builtins(FeaturesSettings(assistant_message_anchor="top"))

        assert payload["assistant_message_anchor"] == "top"
