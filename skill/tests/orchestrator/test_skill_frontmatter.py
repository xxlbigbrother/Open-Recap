"""Stage skill identity and invocation metadata."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def _frontmatter(skill_name):
    path = ROOT / "skills" / skill_name / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    assert match, f"{path} must start with YAML frontmatter"

    values = {}
    for line in match.group(1).splitlines():
        if not line or line[0].isspace():
            continue
        key, separator, raw_value = line.partition(":")
        assert separator, (path, line)
        value = raw_value.strip()
        if value in {"true", "false"}:
            values[key] = value == "true"
        else:
            values[key] = value.strip("\"'")
    return values


def test_every_discovered_skill_has_matching_frontmatter_identity():
    skill_dirs = sorted(
        path
        for path in (ROOT / "skills").iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )
    assert skill_dirs
    for skill_dir in skill_dirs:
        frontmatter = _frontmatter(skill_dir.name)
        assert frontmatter.get("name") == skill_dir.name
        if "user-invocable" in frontmatter:
            assert isinstance(frontmatter["user-invocable"], bool)


def test_stage_frontmatter_exposes_only_the_writing_skill():
    expected_invocability = {
        "video-script": True,
        "video-understanding": False,
        "video-cut": False,
        "video-assemble": False,
    }

    for skill_name, expected in expected_invocability.items():
        frontmatter = _frontmatter(skill_name)
        assert frontmatter["name"] == skill_name
        assert frontmatter.get("user-invocable", True) is expected
