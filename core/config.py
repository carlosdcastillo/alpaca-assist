"""Configuration constants - no UI dependencies."""
from pathlib import Path
from typing import Any

# Preferences defaults.
#
# Keep this list to keys something actually reads — a handful of prior
# entries (default_model, summary_model, window_geometry, background_color,
# max_undo_levels, chat_update_throttle, ui_update_interval) had no reader
# anywhere in the codebase and only added confusion to the preferences
# surface. "theme" previously defaulted to "nord", a value neither
# web/css/themes.css nor the Preferences dialog's Theme dropdown implements
# (only "dark"/"light" are real) — new users got a preferences.json with an
# unrecognized theme, which rendered as a blank Theme dropdown.
DEFAULT_PREFERENCES: dict[str, Any] = {
    "api_url": "http://localhost:11434/api/chat",
    "font_family": "Cascadia Mono",
    "font_size": 12,
    "theme": "dark",
    "agent_skills": {
        "enabled": True,
        "directories": [],
    },
}

# MCP configuration
MCP_SERVERS_FILE: str = "mcp_servers.json"

# Skills directory
_DEFAULT_SKILLS_DIR = str(Path.home() / ".raven" / "skills")
