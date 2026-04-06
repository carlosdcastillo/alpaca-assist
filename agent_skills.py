"""Agent Skills — discovery, validation, and system-prompt injection.

Skills are folders containing a SKILL.md file with YAML frontmatter.
The SkillManager scans configured directories, validates each skill,
and generates the <available_skills> XML block injected into every
LLM request so the model knows which skills are available.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ^[a-z]([a-z0-9-]*[a-z0-9])?$  — single letter is valid; trailing group optional
_NAME_RE = re.compile(r"^[a-z]([a-z0-9-]*[a-z0-9])?$")
_NAME_MAX = 64
_DESC_MAX = 1024


@dataclass
class SkillMetadata:
    name: str
    description: str
    location: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)


class SkillManager:
    def __init__(self) -> None:
        self.skills: list[SkillMetadata] = []

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(
        self,
        directories: list[str],
        enabled: bool = True,
    ) -> list[str]:
        """Scan *directories* for valid skills.  Returns a list of warning strings.

        Clears any previously discovered skills first.  If *enabled* is False,
        no scanning is performed and the skills list is left empty.
        """
        self.skills = []
        if not enabled:
            return []

        warnings: list[str] = []
        seen: dict[str, str] = {}  # name → first location

        for raw_dir in directories:
            dir_path = Path(raw_dir).expanduser()

            if not dir_path.exists():
                try:
                    dir_path.mkdir(parents=True, exist_ok=True)
                    logger.info("Created skills directory: %s", dir_path)
                except Exception as exc:
                    msg = f"Failed to create skills directory {dir_path}: {exc}"
                    logger.warning(msg)
                    warnings.append(msg)
                # Per spec: created this run, do NOT scan this run.
                continue

            if not dir_path.is_dir():
                logger.error("Skills path is not a directory: %s", dir_path)
                continue

            try:
                for entry in sorted(dir_path.iterdir()):
                    if not entry.is_dir():
                        continue
                    skill_file = entry / "SKILL.md"
                    if not skill_file.exists():
                        continue
                    result = _parse_skill(skill_file, entry.name)
                    if isinstance(result, str):
                        msg = f"Skipping {skill_file}: {result}"
                        logger.warning(msg)
                        warnings.append(msg)
                        continue
                    if result.name in seen:
                        msg = (
                            f"Duplicate skill '{result.name}' in {skill_file} "
                            f"(already loaded from {seen[result.name]}); skipping"
                        )
                        logger.warning(msg)
                        warnings.append(msg)
                        continue
                    seen[result.name] = str(skill_file)
                    self.skills.append(result)
            except Exception as exc:
                logger.error("Error scanning directory %s: %s", dir_path, exc)

        self.skills.sort(key=lambda s: s.name)
        return warnings

    # ------------------------------------------------------------------
    # System-prompt XML
    # ------------------------------------------------------------------

    def to_prompt_xml(self) -> str:
        """Return the <available_skills> XML block, or '' if no skills."""
        if not self.skills:
            return ""
        lines = ["<available_skills>"]
        for skill in self.skills:
            lines.append("  <skill>")
            lines.append(f"    <name>{_xml_escape(skill.name)}</name>")
            lines.append(
                f"    <description>{_xml_escape(skill.description)}</description>",
            )
            lines.append(f"    <location>{_xml_escape(skill.location)}</location>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _parse_skill(skill_file: Path, dir_name: str) -> SkillMetadata | str:
    """Parse *skill_file* and return SkillMetadata, or an error string."""
    try:
        content = skill_file.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Cannot read file: {exc}"

    if not content.startswith("---"):
        return "Missing frontmatter (no opening ---)"

    closing = content.find("\n---", 3)
    if closing == -1:
        return "Missing frontmatter closing ---"

    frontmatter_text = content[3:closing].strip()

    try:
        data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        return f"Invalid YAML in frontmatter: {exc}"

    if not isinstance(data, dict):
        return "Frontmatter must be a YAML mapping"

    # Required fields
    raw_name = data.get("name")
    raw_desc = data.get("description")
    if not raw_name:
        return "Missing required field: name"
    if not raw_desc:
        return "Missing required field: description"

    name = str(raw_name)
    description = str(raw_desc)

    # Validate name
    if len(name) > _NAME_MAX:
        return f"Invalid name '{name}': exceeds {_NAME_MAX} characters"
    if not _NAME_RE.match(name):
        return (
            f"Invalid name '{name}': must match ^[a-z]([a-z0-9-]*[a-z0-9])?$ "
            "(lowercase letters, digits, hyphens; must start with a letter)"
        )
    if name != dir_name:
        return f"Name '{name}' does not match directory name '{dir_name}'"

    # Truncate description
    if len(description) > _DESC_MAX:
        description = description[:_DESC_MAX]

    # Optional fields
    raw_meta = data.get("metadata", {})
    metadata: dict[str, str] = (
        {str(k): str(v) for k, v in raw_meta.items()}
        if isinstance(raw_meta, dict)
        else {}
    )
    raw_tools = data.get("allowed-tools", [])
    allowed_tools: list[str] = (
        [str(t) for t in raw_tools] if isinstance(raw_tools, list) else []
    )

    return SkillMetadata(
        name=name,
        description=description,
        location=str(skill_file.resolve()),
        license=str(data["license"]) if "license" in data else None,
        compatibility=(str(data["compatibility"]) if "compatibility" in data else None),
        metadata=metadata,
        allowed_tools=allowed_tools,
    )
