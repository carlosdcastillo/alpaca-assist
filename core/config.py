"""Configuration constants - no UI dependencies."""
from pathlib import Path
from typing import Any

# Preferences defaults
DEFAULT_PREFERENCES: dict[str, Any] = {
    "api_url": "http://localhost:11434/api/chat",
    "default_model": "granite-code:8b",
    "summary_model": "codellama:13b",
    "font_family": "Cascadia Mono",
    "font_size": 12,
    "theme": "nord",  # Default to nord theme for dark background
    "background_color": "black",
    "window_geometry": "600x800+100+100",
    "ui_update_interval": 500,
    "max_undo_levels": -1,
    "chat_update_throttle": 0.1,
    "agent_skills": {
        "enabled": True,
        "directories": [],
    },
}

# MCP configuration
MCP_SERVERS_FILE: str = "mcp_servers.json"

# Skills directory
_DEFAULT_SKILLS_DIR = str(Path.home() / ".raven" / "skills")
