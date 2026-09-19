import json
import os
from pathlib import Path

import msgspec
import pytest

from chainlit import config as chainlit_config
from chainlit.config import (
    ChainlitConfig,
    ChainlitConfigOverrides,
    FeaturesSettings,
    HeaderLink,
    SpontaneousFileUploadFeature,
    UISettings,
)


@pytest.fixture
def translation_dir(tmp_path: Path) -> Path:
    """Minimal translation directory with a controlled set of locale files."""
    t_dir = tmp_path / "translations"
    t_dir.mkdir()

    files: dict[str, dict] = {
        "en-US.json": {"greeting": "Hello"},
        "es.json": {"greeting": "Hola"},
        "da-DK.json": {"greeting": "Hej"},
        "de-DE.json": {"greeting": "Hallo"},
        "zh-CN.json": {"greeting": "你好 CN"},
        "zh-TW.json": {"greeting": "你好 TW"},
    }
    for filename, content in files.items():
        (t_dir / filename).write_text(json.dumps(content), encoding="utf-8")

    return t_dir


class TestLoadTranslation:
    """Regression tests for the load_translation fallback chain."""

    @pytest.fixture(autouse=True)
    def no_packaged_translations(self, tmp_path: Path, monkeypatch):
        """The chain alone: an empty package directory, so only the app's
        copies answer and the equalities below stay exact."""
        empty = tmp_path / "pkg"
        empty.mkdir()
        monkeypatch.setattr(chainlit_config, "TRANSLATIONS_DIR", str(empty))

    def test_exact_match_regional(
        self,
        test_config: ChainlitConfig,
        translation_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Exact regional locale (da-DK) resolves directly to its file."""
        monkeypatch.setattr(
            chainlit_config, "config_translation_dir", str(translation_dir)
        )
        assert test_config.load_translation("da-DK") == {"greeting": "Hej"}

    def test_exact_match_base(
        self,
        test_config: ChainlitConfig,
        translation_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Exact base locale (es) resolves directly to its file."""
        monkeypatch.setattr(
            chainlit_config, "config_translation_dir", str(translation_dir)
        )
        assert test_config.load_translation("es") == {"greeting": "Hola"}

    def test_parent_fallback(
        self,
        test_config: ChainlitConfig,
        translation_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Regional locale (es-419) falls back to base file (es.json) when no exact match."""
        monkeypatch.setattr(
            chainlit_config, "config_translation_dir", str(translation_dir)
        )
        assert test_config.load_translation("es-419") == {"greeting": "Hola"}

    def test_regional_variant_lookup(
        self,
        test_config: ChainlitConfig,
        translation_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Base locale (da) resolves to regional file (da-DK.json) when no exact match exists."""
        monkeypatch.setattr(
            chainlit_config, "config_translation_dir", str(translation_dir)
        )
        assert test_config.load_translation("da") == {"greeting": "Hej"}

    def test_regional_variant_lookup_de(
        self,
        test_config: ChainlitConfig,
        translation_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Base locale (de) resolves to regional file (de-DE.json) via variant lookup."""
        monkeypatch.setattr(
            chainlit_config, "config_translation_dir", str(translation_dir)
        )
        assert test_config.load_translation("de") == {"greeting": "Hallo"}

    def test_regional_variant_sorted_deterministic(
        self,
        test_config: ChainlitConfig,
        translation_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """When multiple regional variants exist, the first sorted match (zh-CN) is returned."""
        monkeypatch.setattr(
            chainlit_config, "config_translation_dir", str(translation_dir)
        )
        assert test_config.load_translation("zh") == {"greeting": "你好 CN"}

    def test_default_fallback_unknown_locale(
        self,
        test_config: ChainlitConfig,
        translation_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Completely unknown locale (xx) falls back to en-US."""
        monkeypatch.setattr(
            chainlit_config, "config_translation_dir", str(translation_dir)
        )
        assert test_config.load_translation("xx") == {"greeting": "Hello"}

    def test_default_fallback_base_without_regional_variant(
        self,
        test_config: ChainlitConfig,
        translation_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Base locale (fr) with no matching file at all falls back to en-US."""
        monkeypatch.setattr(
            chainlit_config, "config_translation_dir", str(translation_dir)
        )
        assert test_config.load_translation("fr") == {"greeting": "Hello"}


class TestTranslationLayering:
    """The package's file is the base, the app's copy is laid over it.

    ``init_config`` copies the packaged files once; a key the package gained in
    a later release never reached an app initialised before it, and the client
    drew ``...`` for it. The two are merged, section by section, so a release
    can add a string without the app touching its copies.
    """

    @pytest.fixture
    def dirs(self, tmp_path: Path, monkeypatch):
        pkg = tmp_path / "pkg"
        app = tmp_path / "app"
        pkg.mkdir()
        app.mkdir()
        monkeypatch.setattr(chainlit_config, "TRANSLATIONS_DIR", str(pkg))
        monkeypatch.setattr(chainlit_config, "config_translation_dir", str(app))
        return pkg, app

    @staticmethod
    def _write(directory: Path, name: str, content: dict) -> None:
        (directory / name).write_text(json.dumps(content), encoding="utf-8")

    def test_a_key_the_copy_never_saw_comes_from_the_package(
        self, test_config: ChainlitConfig, dirs
    ):
        pkg, app = dirs
        self._write(pkg, "ru.json", {"account": {"title": "Account", "new": "New"}})
        self._write(app, "ru.json", {"account": {"title": "Кабинет"}})

        assert test_config.load_translation("ru") == {
            "account": {"title": "Кабинет", "new": "New"}
        }

    def test_the_copy_wins_where_it_speaks(self, test_config: ChainlitConfig, dirs):
        pkg, app = dirs
        self._write(pkg, "ru.json", {"greeting": "Hello", "nav": {"a": "1", "b": "2"}})
        self._write(app, "ru.json", {"greeting": "Привет", "nav": {"b": "два"}})

        assert test_config.load_translation("ru") == {
            "greeting": "Привет",
            "nav": {"a": "1", "b": "два"},
        }

    def test_a_language_only_the_package_ships_still_resolves(
        self, test_config: ChainlitConfig, dirs
    ):
        # An app initialised before the package gained a language has no copy
        # of it; the wheel's own file answers on its own.
        pkg, _ = dirs
        self._write(pkg, "kk.json", {"greeting": "Сәлем"})

        assert test_config.load_translation("kk") == {"greeting": "Сәлем"}

    def test_the_fallback_chain_picks_one_stem_for_both_directories(
        self, test_config: ChainlitConfig, dirs
    ):
        # `es-419` has no file anywhere; the parent `es` is chosen once, and
        # both halves come from `es.json` rather than one from each language.
        pkg, app = dirs
        self._write(pkg, "es.json", {"greeting": "Hola", "extra": "x"})
        self._write(pkg, "en-US.json", {"greeting": "Hello", "extra": "en"})
        self._write(app, "es.json", {"greeting": "¡Hola!"})

        assert test_config.load_translation("es-419") == {
            "greeting": "¡Hola!",
            "extra": "x",
        }


class TestLoadSettings:
    """The TOML sections decode into the settings types."""

    def test_the_shipped_template_loads(self, tmp_path: Path, monkeypatch):
        """The template ``init_config`` writes is itself a valid config."""
        toml = tmp_path / "config.toml"
        toml.write_text(chainlit_config.DEFAULT_CONFIG_STR, encoding="utf-8")
        monkeypatch.setattr(chainlit_config, "config_file", str(toml))
        settings = chainlit_config.load_settings()
        assert settings["ui"].name == "Assistant"
        assert settings["project"].session_timeout == 3600
        assert settings["features"].spontaneous_file_upload.max_size_mb == 500

    def test_retired_tables_still_load(self, tmp_path: Path, monkeypatch):
        """A ``config.toml`` from an older release keeps loading.

        ``[features.mcp]``, ``[features.slack]`` and ``hot_swap_chat_profile``
        were written by previous releases and are still on disk in every
        deployment; a strict decoder would refuse to start on them.
        """
        toml = tmp_path / "config.toml"
        toml.write_text(
            chainlit_config.DEFAULT_CONFIG_STR.replace(
                "[features]\n", "[features]\nhot_swap_chat_profile = true\n", 1
            )
            + "\n[features.mcp]\nenabled = true\n"
            "[features.mcp.sse]\nenabled = true\n"
            "[features.slack]\nenabled = false\n"
            "[UI.unknown_table]\nx = 1\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(chainlit_config, "config_file", str(toml))
        settings = chainlit_config.load_settings()
        assert settings["ui"].name == "Assistant"
        assert not hasattr(settings["features"], "hot_swap_chat_profile")
        assert not hasattr(settings["features"], "mcp")

    def test_a_wrong_type_is_refused_by_key(self):
        """Validation names the offending key, so the fix is findable."""
        import msgspec

        toml = {
            "UI": {"name": "x", "cot": "loud"},
            "meta": {"generated_by": "9.9.9"},
        }
        with pytest.raises(msgspec.ValidationError, match=r"\$\.cot"):
            chainlit_config.decode_settings(toml)

    def test_lc_cache_path_is_derived_from_the_config_dir(self):
        toml = {"UI": {"name": "x"}, "meta": {"generated_by": "9.9.9"}}
        settings = chainlit_config.decode_settings(toml)
        assert settings["project"].lc_cache_path == str(
            Path(chainlit_config.config_dir) / ".langchain.db"
        )


def test_app_root_comes_from_the_environment(tmp_path: Path):
    """``CHAINLIT_APP_ROOT`` decides where ``.chainlit/`` and ``.files/`` live.

    Read at import, so asserted in a fresh interpreter: the module under
    test is already imported here with this process's value.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c", "import chainlit.config as c; print(c.APP_ROOT)"],
        env={**os.environ, "CHAINLIT_APP_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == str(tmp_path)
    assert (tmp_path / ".files").is_dir()


class TestWithOverrides:
    """A chat profile's ``config_overrides`` reshape the config the UI sees."""

    def test_nested_override_keeps_the_rest_of_the_section(
        self, test_config: ChainlitConfig
    ):
        """The cypress ``config_overrides`` shape: flip one flag, keep the rest.

        ``SpontaneousFileUploadFeature(enabled=False)`` must not blank
        ``accept`` and ``max_files``, or the UI would render an upload with
        no accepted types.
        """
        overrides = ChainlitConfigOverrides(
            ui=UISettings(name="Upload UI"),
            features=FeaturesSettings(
                spontaneous_file_upload=SpontaneousFileUploadFeature(enabled=False)
            ),
        )
        effective = test_config.with_overrides(overrides)

        assert effective.ui.name == "Upload UI"
        assert effective.ui.cot == test_config.ui.cot
        upload = effective.features.spontaneous_file_upload
        assert upload is not None
        assert upload.enabled is False
        base_upload = test_config.features.spontaneous_file_upload
        assert base_upload is not None
        assert upload.accept == base_upload.accept
        assert upload.max_files == base_upload.max_files
        assert effective.features.latex == test_config.features.latex
        # The base is untouched, and the process-wide sections are shared.
        assert base_upload.enabled is True
        assert test_config.ui.name == "Assistant"
        assert effective.code is test_config.code
        assert effective.project is test_config.project

    def test_no_overrides_is_the_same_config(self, test_config: ChainlitConfig):
        assert test_config.with_overrides(None) is test_config

    def test_a_list_field_overrides_whole(self, test_config: ChainlitConfig):
        """Header links replace, not append: a profile owns its header."""
        links = [HeaderLink(name="Balance", url="/billing/balance")]
        effective = test_config.with_overrides(
            ChainlitConfigOverrides(ui=UISettings(name="P", header_links=links))
        )
        assert effective.ui.header_links == links
        assert test_config.ui.header_links is None

    def test_the_settings_are_plain_data(self, test_config: ChainlitConfig):
        """The settings route hands ``ui`` and ``features`` to the UI as JSON."""
        dumped = msgspec.to_builtins(test_config.ui)
        assert dumped["name"] == "Assistant"
        assert dumped["header_links"] is None
        json.dumps(dumped)
