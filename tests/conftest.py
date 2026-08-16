"""
Test configuration and shared fixtures for pywebview_demo.

This module provides pytest fixtures and configuration used across all tests.
"""

import asyncio
import gc
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncGenerator
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from database import ConversationDatabase
    from chat_state import ChatState
    from tool_call_detector import ToolCallDetector
    from agent_skills import SkillManager
    from shell_executor import ShellExecutor

from unittest.mock import MagicMock
from unittest.mock import Mock

import pytest
import pytest_asyncio

# Add the parent directory to sys.path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# Basic Fixtures
# =============================================================================


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test files."""
    tmp_dir = tempfile.mkdtemp()
    try:
        yield Path(tmp_dir)
    finally:
        # Force GC to finalize any open sqlite3.Connection objects.
        # Python's "with sqlite3.connect(...) as conn:" does NOT close the
        # connection — it only commits/rolls back. Connections are closed when
        # garbage-collected. On Windows this matters: shutil.rmtree will get
        # PermissionError if the file handle is still open.
        gc.collect()
        if platform.system() == "Windows":
            for attempt in range(5):
                try:
                    shutil.rmtree(tmp_dir)
                    break
                except PermissionError:
                    if attempt < 4:
                        time.sleep(0.1 * (attempt + 1))
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def temp_file(temp_dir: Path) -> Generator[Path, None, None]:
    """Create a temporary file path."""
    file_path = temp_dir / "test_file.txt"
    yield file_path
    # Cleanup is handled by temp_dir fixture


@pytest.fixture
def mock_event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Provide a mock event loop for testing async code."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Database Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path(temp_dir: Path) -> Path:
    """Create a temporary database path."""
    return temp_dir / "test_conversations.db"


@pytest.fixture
def mock_db(temp_db_path: Path) -> Generator["ConversationDatabase", None, None]:
    """Create a mock database instance with a temporary file."""
    from database import ConversationDatabase

    db = ConversationDatabase(str(temp_db_path))
    yield db
    # Drop the reference so sqlite3.Connection objects can be GC'd before
    # temp_dir tries to delete the file (critical on Windows).
    del db
    gc.collect()


# =============================================================================
# Configuration Fixtures
# =============================================================================


@pytest.fixture
def mock_preferences() -> dict[str, Any]:
    """Provide mock preferences data."""
    return {
        "theme": "dark",
        "font_size": 14,
        "model": "llama3.1",
        "temperature": 0.7,
    }


@pytest.fixture
def mock_mcp_config() -> dict[str, Any]:
    """Provide mock MCP server configuration."""
    return {
        "servers": {
            "test_server": {
                "command": ["python", "-m", "test_server"],
                "args": [],
                "enabled": True,
            },
        },
    }


# =============================================================================
# Chat State Fixtures
# =============================================================================


@pytest.fixture
def empty_chat_state() -> "ChatState":
    """Create an empty chat state."""
    from chat_state import ChatState

    return ChatState(questions=[], answers=[])


@pytest.fixture
def populated_chat_state() -> "ChatState":
    """Create a chat state with some conversation data."""
    from chat_state import ChatState
    from chat_state import FullAnswer

    state = ChatState(
        questions=["What is Python?"],
        answers=[FullAnswer(["Python is a programming language."])],
    )
    return state


# =============================================================================
# MCP Fixtures
# =============================================================================


@pytest.fixture
def mock_mcp_server() -> Mock:
    """Create a mock MCP server."""
    server = Mock()
    server.name = "test_server"
    server.tools = [
        {"name": "test_tool", "description": "A test tool", "parameters": {}},
    ]
    return server


@pytest.fixture
def mock_mcp_manager() -> Mock:
    """Create a mock MCP manager."""
    manager = Mock()
    manager.get_available_tools.return_value = {
        "test_server": [
            {"name": "test_tool", "description": "Test tool", "parameters": {}},
        ],
    }
    manager.call_tool = AsyncMock(return_value={"result": "success"})
    return manager


# =============================================================================
# Tool Call Detector Fixtures
# =============================================================================


@pytest.fixture
def tool_call_detector() -> "ToolCallDetector":
    """Create a ToolCallDetector instance."""
    from tool_call_detector import ToolCallDetector

    return ToolCallDetector()


@pytest.fixture
def sample_tool_call() -> str:
    """Provide a sample tool call JSON string."""
    return '{"tool_call": {"id": "123", "name": "test_tool", "arguments": {"arg1": "value1"}}}'


@pytest.fixture
def sample_tool_result() -> str:
    """Provide a sample tool result JSON string."""
    return '{"tool_result": {"id": "123", "content": "result content"}}'


# =============================================================================
# Skill Fixtures
# =============================================================================


@pytest.fixture
def sample_skill_dir(temp_dir: Path) -> Path:
    """Create a sample skill directory with SKILL.md for testing."""
    skill_content = """---
name: test-skill
description: A test skill for unit testing
---

# Test Skill

This is a test skill for unit testing.

## Usage

Use this skill to test the skill manager.
"""
    skill_dir = temp_dir / "test-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(skill_content)
    return temp_dir


@pytest.fixture
def skill_manager() -> "SkillManager":
    """Create a SkillManager instance."""
    from agent_skills import SkillManager

    return SkillManager()


# =============================================================================
# Shell Executor Fixtures
# =============================================================================


@pytest.fixture
def shell_executor() -> "ShellExecutor":
    """Create a ShellExecutor instance."""
    from shell_executor import ShellExecutor

    return ShellExecutor()


# =============================================================================
# Async Fixtures
# =============================================================================


@pytest_asyncio.fixture
async def async_mcp_manager() -> AsyncGenerator[Mock, None]:
    """Create an async mock MCP manager."""
    manager = Mock()
    manager.add_server = AsyncMock(return_value=True)
    manager.call_tool = AsyncMock(return_value={"result": "success"})
    manager.disconnect_server = AsyncMock(return_value=True)
    manager.shutdown = AsyncMock()
    yield manager


# =============================================================================
# Mock API Fixtures
# =============================================================================


@pytest.fixture
def mock_webview_api() -> Mock:
    """Create a mock WebView API."""
    api = Mock()
    api.on_content_update = Mock()
    api.on_tool_call = Mock()
    api.on_tool_result = Mock()
    api.on_error = Mock()
    return api


# =============================================================================
# Session Fixtures
# =============================================================================


@pytest.fixture
def sample_session_data() -> dict[str, Any]:
    """Provide sample session data."""
    return {
        "version": 1,
        "tabs": [
            {
                "id": "tab-1",
                "title": "Test Tab",
                "chat_data": {
                    "questions": ["Hello"],
                    "answers": [{"components": [{"type": "text", "content": "Hi!"}]}],
                    "streaming_index": None,
                },
            },
        ],
        "selected_tab_id": "tab-1",
    }


# =============================================================================
# Pytest Configuration
# =============================================================================


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line("markers", "unit: marks tests as unit tests")
    config.addinivalue_line("markers", "asyncio: marks async tests")


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Modify test collection to add markers based on test names."""
    for item in items:
        # Auto-mark async tests
        if "async" in item.nodeid or "asyncio" in item.nodeid:
            item.add_marker(pytest.mark.asyncio)
        # Auto-mark integration tests
        if "integration" in item.nodeid:
            item.add_marker(pytest.mark.integration)
        # Auto-mark unit tests
        if "unit" in item.nodeid or "test_" in item.nodeid:
            item.add_marker(pytest.mark.unit)


# =============================================================================
# Test Utilities
# =============================================================================


class TestHelpers:
    """Helper methods for tests."""

    @staticmethod
    def create_temp_json_file(data: dict, path: Path) -> Path:
        """Create a temporary JSON file with the given data."""
        file_path = path / "test_data.json"
        file_path.write_text(json.dumps(data, indent=2))
        return file_path

    @staticmethod
    def assert_json_equal(actual: dict, expected: dict) -> None:
        """Assert that two JSON objects are equal."""
        assert json.dumps(actual, sort_keys=True) == json.dumps(
            expected,
            sort_keys=True,
        )


@pytest.fixture
def helpers() -> TestHelpers:
    """Provide test helpers."""
    return TestHelpers()
