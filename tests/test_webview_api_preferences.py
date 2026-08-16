"""Tests for WebViewAPI preference-persistence methods.

Regression coverage for set_model silently not surviving a restart: it
updated AppCore.preferences in memory but never called save_preferences(),
so picking a model and restarting the app reverted to the default.
"""

from __future__ import annotations

import gc
import json
import os
import platform
import shutil
import time
from unittest.mock import MagicMock
from unittest.mock import Mock

import pytest


@pytest.fixture
def temp_dir(tmp_path):
    """Change cwd to tmp_path so AppCore's relative preferences path lands here."""
    original = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original)
    gc.collect()
    if platform.system() == "Windows":
        for attempt in range(5):
            try:
                shutil.rmtree(tmp_path)
                break
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.1 * (attempt + 1))


@pytest.fixture
def core(temp_dir):
    """Real AppCore (with a real SQLite DB and preferences file in tmp_path)."""
    from core.app_core import AppCore

    return AppCore(api=Mock())


@pytest.fixture
def api(core):
    """WebViewAPI wrapping the real core; no real webview window needed."""
    from webview_api import WebViewAPI

    mock_app = Mock()
    mock_app.core = core
    return WebViewAPI(mock_app)


class TestSetModelPersistence:
    def test_set_model_writes_to_preferences_file(self, api, temp_dir) -> None:
        result = api.set_model("claude-sonnet-4-6")

        assert result["success"] is True
        prefs_file = temp_dir / "preferences.json"
        assert prefs_file.exists()
        saved = json.loads(prefs_file.read_text())
        assert saved["model"] == "claude-sonnet-4-6"

    def test_set_model_survives_a_fresh_load(self, api, core, temp_dir) -> None:
        """Simulates a restart: a new AppCore loading preferences from disk

        must see the model chosen by the previous instance."""
        from core.app_core import AppCore

        api.set_model("claude-opus-4-7")

        restarted_core = AppCore(api=Mock())
        assert restarted_core.preferences.get("model") == "claude-opus-4-7"

    def test_set_model_updates_in_memory_preferences(self, api, core) -> None:
        api.set_model("kimi-k2p7-code")

        assert core.preferences["model"] == "kimi-k2p7-code"

    def test_set_model_routes_to_active_pack_tab(self, api, core) -> None:
        from core.pack_tab import PackTab

        pack_tab = MagicMock(spec=PackTab)
        pack_tab.set_model.return_value = {"success": True}
        core.tabs["pack-1"] = pack_tab
        api._app.get_active_tab_id.return_value = "pack-1"

        result = api.set_model("remote-model")

        assert result == {"success": True}
        pack_tab.set_model.assert_called_once_with("remote-model")
        assert core.preferences.get("model") != "remote-model"
