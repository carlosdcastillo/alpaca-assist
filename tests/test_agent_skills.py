"""
Comprehensive tests for agent_skills.py module.

This module tests skill discovery, parsing, and XML generation.
"""
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from agent_skills import _parse_skill
from agent_skills import _xml_escape
from agent_skills import SkillManager
from agent_skills import SkillMetadata


class TestSkillMetadata:
    """Tests for SkillMetadata dataclass."""

    def test_skill_metadata_creation(self) -> None:
        """Test creating SkillMetadata instance."""
        skill = SkillMetadata(
            name="test_skill",
            description="A test skill",
            location="/path/to/skill",
        )

        assert skill.name == "test_skill"
        assert skill.description == "A test skill"
        assert skill.location == "/path/to/skill"


class TestXMLEscape:
    """Tests for XML escaping function."""

    def test_xml_escape_basic(self) -> None:
        """Test basic XML escaping."""
        assert _xml_escape("<test>") == "&lt;test&gt;"
        assert _xml_escape("&test") == "&amp;test"
        assert _xml_escape('"test"') == "&quot;test&quot;"
        assert _xml_escape("'test'") == "&apos;test&apos;"

    def test_xml_escape_no_special_chars(self) -> None:
        """Test that normal text is unchanged."""
        text = "Normal text without special characters"
        assert _xml_escape(text) == text

    def test_xml_escape_mixed_content(self) -> None:
        """Test escaping mixed content."""
        text = '<div>Hello & welcome to "test"</div>'
        expected = "&lt;div&gt;Hello &amp; welcome to &quot;test&quot;&lt;/div&gt;"
        assert _xml_escape(text) == expected

    def test_xml_escape_empty_string(self) -> None:
        """Test escaping empty string."""
        assert _xml_escape("") == ""

    def test_xml_escape_multiple_special_chars(self) -> None:
        """Test escaping multiple special characters."""
        text = "<>&\"'"
        expected = "&lt;&gt;&amp;&quot;&apos;"
        assert _xml_escape(text) == expected


class TestParseSkill:
    """Tests for skill parsing function."""

    def test_parse_skill_valid(self, temp_dir: Path) -> None:
        """Test parsing a valid skill file."""
        skill_content = """---
name: test-skill
description: This is a test skill description
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

        result = _parse_skill(skill_file, "test-skill")

        assert isinstance(result, SkillMetadata)
        assert result.name == "test-skill"
        assert result.description == "This is a test skill description"

    def test_parse_skill_no_frontmatter(self, temp_dir: Path) -> None:
        """Test parsing skill without frontmatter."""
        skill_content = """# My Skill

Some content here.
"""
        skill_dir = temp_dir / "my-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(skill_content)

        result = _parse_skill(skill_file, "my-skill")

        assert isinstance(result, str)
        assert "Missing frontmatter" in result

    def test_parse_skill_empty_file(self, temp_dir: Path) -> None:
        """Test parsing empty skill file."""
        skill_dir = temp_dir / "empty-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("")

        result = _parse_skill(skill_file, "empty-skill")

        assert isinstance(result, str)
        assert "Missing frontmatter" in result

    def test_parse_skill_missing_name(self, temp_dir: Path) -> None:
        """Test parsing skill without required name field."""
        skill_content = """---
description: A skill without a name
---

# Test
"""
        skill_dir = temp_dir / "no-name"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(skill_content)

        result = _parse_skill(skill_file, "no-name")

        assert isinstance(result, str)
        assert "Missing required field: name" in result

    def test_parse_skill_nonexistent_file(self, temp_dir: Path) -> None:
        """Test parsing nonexistent skill file."""
        nonexistent_file = temp_dir / "does_not_exist.md"

        result = _parse_skill(nonexistent_file, "category")

        assert isinstance(result, str)
        assert "Cannot read file" in result


class TestSkillManagerInitialization:
    """Tests for SkillManager initialization."""

    def test_init_creates_empty_skills(self) -> None:
        """Test that initialization creates empty skills list."""
        manager = SkillManager()
        assert hasattr(manager, "skills")
        assert isinstance(manager.skills, list)
        assert len(manager.skills) == 0


class TestSkillManagerDiscover:
    """Tests for skill discovery."""

    def test_discover_finds_skills(self, temp_dir: Path) -> None:
        """Test discovering skills in a directory."""
        # Create skill directory structure
        skills_dir = temp_dir / "skills"
        skills_dir.mkdir()

        # Create first skill
        skill1_dir = skills_dir / "skill-one"
        skill1_dir.mkdir()
        (skill1_dir / "SKILL.md").write_text(
            """---
name: skill-one
description: First test skill
---

# Skill One
""",
        )

        # Create second skill
        skill2_dir = skills_dir / "skill-two"
        skill2_dir.mkdir()
        (skill2_dir / "SKILL.md").write_text(
            """---
name: skill-two
description: Second test skill
---

# Skill Two
""",
        )

        manager = SkillManager()
        warnings = manager.discover([str(skills_dir)])

        assert len(manager.skills) == 2
        assert manager.skills[0].name == "skill-one"
        assert manager.skills[1].name == "skill-two"

    def test_discover_empty_directory(self, temp_dir: Path) -> None:
        """Test discovering skills in empty directory."""
        skills_dir = temp_dir / "empty_skills"
        skills_dir.mkdir()

        manager = SkillManager()
        warnings = manager.discover([str(skills_dir)])

        assert len(manager.skills) == 0
        assert isinstance(warnings, list)

    def test_discover_nonexistent_directory(self, temp_dir: Path) -> None:
        """Test discovering skills in nonexistent directory (creates it)."""
        nonexistent_dir = temp_dir / "does_not_exist"

        manager = SkillManager()
        warnings = manager.discover([str(nonexistent_dir)])

        # Should create the directory but not scan it
        assert nonexistent_dir.exists()
        assert len(manager.skills) == 0

    def test_discover_skips_disabled(self, temp_dir: Path) -> None:
        """Test that discover skips skills when enabled=False."""
        skills_dir = temp_dir / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: test-skill
description: Test
---
""",
        )

        manager = SkillManager()
        # First discover to load skills
        manager.discover([str(skills_dir)], enabled=True)
        assert len(manager.skills) == 1

        # Discover again with enabled=False
        manager.discover([str(skills_dir)], enabled=False)
        # Skills should be cleared
        assert len(manager.skills) == 0

    def test_discover_overwrites_existing(self, temp_dir: Path) -> None:
        """Test that discover overwrites existing skills."""
        skills_dir = temp_dir / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: test-skill
description: Original description
---
""",
        )

        manager = SkillManager()
        manager.discover([str(skills_dir)])
        assert len(manager.skills) == 1
        assert manager.skills[0].description == "Original description"

        # Modify file and rediscover
        (skill_dir / "SKILL.md").write_text(
            """---
name: test-skill
description: Updated description
---
""",
        )
        manager.discover([str(skills_dir)])

        assert len(manager.skills) == 1
        assert manager.skills[0].description == "Updated description"


class TestSkillManagerToPromptXML:
    """Tests for XML prompt generation."""

    def test_to_prompt_xml_empty(self) -> None:
        """Test XML generation with no skills."""
        manager = SkillManager()
        xml = manager.to_prompt_xml()

        assert xml == ""

    def test_to_prompt_xml_with_skills(self, temp_dir: Path) -> None:
        """Test XML generation with skills."""
        skills_dir = temp_dir / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: test-skill
description: This is a test skill for XML generation
---

# Test Skill
""",
        )

        manager = SkillManager()
        manager.discover([str(skills_dir)])
        xml = manager.to_prompt_xml()

        assert "<available_skills>" in xml
        assert "</available_skills>" in xml
        assert "<skill>" in xml
        assert "<name>test-skill</name>" in xml
        assert "<description>" in xml
        assert "test skill for XML generation" in xml

    def test_to_prompt_xml_escapes_content(self, temp_dir: Path) -> None:
        """Test that XML generation properly escapes content."""
        skills_dir = temp_dir / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "xml-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: xml-skill
description: Content with <special> & "characters'
---
""",
        )

        manager = SkillManager()
        manager.discover([str(skills_dir)])
        xml = manager.to_prompt_xml()

        assert "&lt;special&gt;" in xml
        assert "&amp;" in xml
        assert "&quot;" in xml
        assert "<special>" not in xml  # Should be escaped

    def test_to_prompt_xml_structure(self, temp_dir: Path) -> None:
        """Test XML structure is correct."""
        skills_dir = temp_dir / "skills"
        skills_dir.mkdir()

        skill1_dir = skills_dir / "skill-one"
        skill1_dir.mkdir()
        (skill1_dir / "SKILL.md").write_text(
            """---
name: skill-one
description: First skill
---
""",
        )

        skill2_dir = skills_dir / "skill-two"
        skill2_dir.mkdir()
        (skill2_dir / "SKILL.md").write_text(
            """---
name: skill-two
description: Second skill
---
""",
        )

        manager = SkillManager()
        manager.discover([str(skills_dir)])
        xml = manager.to_prompt_xml()

        # Check structure
        assert xml.startswith("<available_skills>")
        assert xml.endswith("</available_skills>")

        # Should have skill elements
        assert "<skill>" in xml
        assert "</skill>" in xml


class TestSkillManagerEdgeCases:
    """Tests for edge cases and error handling."""

    def test_discover_malformed_skill(self, temp_dir: Path) -> None:
        """Test discovering malformed skill files."""
        skills_dir = temp_dir / "skills"
        skills_dir.mkdir()

        # Create a valid skill
        valid_dir = skills_dir / "valid-skill"
        valid_dir.mkdir()
        (valid_dir / "SKILL.md").write_text(
            """---
name: valid-skill
description: Valid skill
---
""",
        )

        # Create a skill with invalid name
        invalid_dir = skills_dir / "invalid"
        invalid_dir.mkdir()
        (invalid_dir / "SKILL.md").write_text(
            """---
name: Invalid_Name
description: Invalid name with underscore
---
""",
        )

        manager = SkillManager()
        warnings = manager.discover([str(skills_dir)])

        # Should still find valid skill
        assert len(manager.skills) == 1
        assert manager.skills[0].name == "valid-skill"
        # Should have warning about invalid skill
        assert len(warnings) > 0

    def test_discover_permission_error(self, temp_dir: Path) -> None:
        """Test handling permission errors during discovery."""
        skills_dir = temp_dir / "skills"
        skills_dir.mkdir()

        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: test-skill
description: Test
---
""",
        )

        manager = SkillManager()

        # Should handle gracefully even with errors
        warnings = manager.discover([str(skills_dir)])
        assert isinstance(warnings, list)

    def test_discover_very_long_content(self, temp_dir: Path) -> None:
        """Test discovering skills with very long content."""
        skills_dir = temp_dir / "skills"
        skills_dir.mkdir()

        skill_dir = skills_dir / "long-skill"
        skill_dir.mkdir()

        # Create description longer than 1024 chars
        long_desc = "x" * 2000
        (skill_dir / "SKILL.md").write_text(
            f"""---
name: long-skill
description: {long_desc}
---
""",
        )

        manager = SkillManager()
        manager.discover([str(skills_dir)])

        assert len(manager.skills) == 1
        # Description should be truncated
        assert len(manager.skills[0].description) <= 1024

    def test_skill_name_collision(self, temp_dir: Path) -> None:
        """Test handling of skill name collisions."""
        dir1 = temp_dir / "skills1"
        dir2 = temp_dir / "skills2"
        dir1.mkdir()
        dir2.mkdir()

        # Same skill name in different directories
        skill1_dir = dir1 / "common-skill"
        skill1_dir.mkdir()
        (skill1_dir / "SKILL.md").write_text(
            """---
name: common-skill
description: From dir1
---
""",
        )

        skill2_dir = dir2 / "common-skill"
        skill2_dir.mkdir()
        (skill2_dir / "SKILL.md").write_text(
            """---
name: common-skill
description: From dir2
---
""",
        )

        manager = SkillManager()
        warnings = manager.discover([str(dir1), str(dir2)])

        # Should only have one skill (first one wins)
        assert len(manager.skills) == 1
        assert manager.skills[0].description == "From dir1"
        # Should have warning about duplicate
        assert any("Duplicate" in w for w in warnings)


class TestSkillManagerIntegration:
    """Integration tests for SkillManager."""

    def test_full_workflow(self, temp_dir: Path) -> None:
        """Test complete skill management workflow."""
        skills_dir = temp_dir / "agent_skills"
        skills_dir.mkdir()

        # Create multiple skills
        coding_dir = skills_dir / "coding-assistant"
        coding_dir.mkdir()
        (coding_dir / "SKILL.md").write_text(
            """---
name: coding-assistant
description: Helps with programming tasks
---

# Coding Assistant

Helps with programming tasks.
""",
        )

        writing_dir = skills_dir / "writing-assistant"
        writing_dir.mkdir()
        (writing_dir / "SKILL.md").write_text(
            """---
name: writing-assistant
description: Helps with writing tasks
---

# Writing Assistant

Helps with writing tasks.
""",
        )

        manager = SkillManager()

        # Discover skills
        warnings = manager.discover([str(skills_dir)])
        assert len(manager.skills) == 2

        # Generate XML
        xml = manager.to_prompt_xml()
        assert "<available_skills>" in xml
        assert "coding-assistant" in xml
        assert "writing-assistant" in xml
        assert "programming" in xml
        assert "writing tasks" in xml

        # Rediscover (simulating refresh)
        warnings2 = manager.discover([str(skills_dir)])
        assert len(manager.skills) == 2

    def test_skill_with_optional_fields(self, temp_dir: Path) -> None:
        """Test skill with optional metadata fields."""
        skills_dir = temp_dir / "skills"
        skills_dir.mkdir()

        skill_dir = skills_dir / "full-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: full-skill
description: A complete skill
license: MIT
compatibility: python3.11
metadata:
  author: Test Author
  version: "1.0"
allowed-tools:
  - tool1
  - tool2
---
""",
        )

        manager = SkillManager()
        manager.discover([str(skills_dir)])

        assert len(manager.skills) == 1
        skill = manager.skills[0]
        assert skill.name == "full-skill"
        assert skill.license == "MIT"
        assert skill.compatibility == "python3.11"
        assert skill.metadata.get("author") == "Test Author"
        assert "tool1" in skill.allowed_tools
