"""Claude Code reads these files at session start, so they can't be run here. These tests pin
down the wiring between them: the settings call the hook, and the agent the hook expects exists."""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def hooks():
    return json.loads((ROOT / ".claude/settings.json").read_text())["hooks"]


@pytest.fixture(scope="module")
def reviewer():
    text = (ROOT / ".claude/agents/reviewer.md").read_text()
    _, frontmatter, body = text.split("---", 2)
    return yaml.safe_load(frontmatter), body


def commands_for(hooks, event, tool):
    return [
        handler["command"]
        for group in hooks[event]
        if re.fullmatch(group["matcher"], tool)
        for handler in group["hooks"]
    ]


@pytest.mark.parametrize(
    "tool",
    [
        "mcp__github__update_pull_request",
        "mcp__github__create_pull_request",
        "mcp__plugin_gh_github__update_pull_request",
        "mcp__github__merge_pull_request",
        "mcp__github__enable_pr_auto_merge",
        "Bash",
    ],
)
def test_the_gate_runs_before_every_way_of_opening_or_readying_a_pr(hooks, tool):
    commands = commands_for(hooks, "PreToolUse", tool)

    assert any("review_gate.py" in c and c.endswith(" gate") for c in commands)


def test_the_gate_does_not_run_on_unrelated_tools(hooks):
    assert commands_for(hooks, "PreToolUse", "Read") == []


def test_the_reviewers_verdict_is_recorded_when_it_stops(hooks):
    commands = commands_for(hooks, "SubagentStop", "reviewer")

    assert any("review_gate.py" in c and c.endswith(" record") for c in commands)


def test_hook_commands_use_the_project_dir_so_they_work_from_any_cwd(hooks):
    for event in ("PreToolUse", "SubagentStop"):
        for group in hooks[event]:
            for handler in group["hooks"]:
                assert "$CLAUDE_PROJECT_DIR" in handler["command"]


def test_the_reviewer_is_named_as_the_hook_expects(reviewer):
    frontmatter, _ = reviewer

    assert frontmatter["name"] == "reviewer"


def test_the_reviewer_cannot_edit_files(reviewer):
    frontmatter, _ = reviewer
    tools = [t.strip() for t in frontmatter["tools"].split(",")]

    assert set(tools) <= {"Read", "Grep", "Glob", "Bash"}


def test_the_reviewer_uses_the_sessions_model(reviewer):
    frontmatter, _ = reviewer

    assert frontmatter["model"] == "inherit"


def test_the_reviewer_is_told_the_verdict_format_the_hook_parses(reviewer):
    _, body = reviewer

    for key in ("commit", "blocking", "suggestions", "judgment_calls"):
        assert f'"{key}"' in body


def test_claude_md_describes_the_review_loop():
    text = (ROOT / "CLAUDE.md").read_text()

    assert "## Review loop" in text


def test_claude_md_says_how_to_reset_a_branchs_review_rounds():
    text = (ROOT / "CLAUDE.md").read_text()

    assert "rm .git/claude-review/" in text


def test_claude_md_says_claude_merges_with_a_merge_commit():
    text = (ROOT / "CLAUDE.md").read_text()

    assert "Never merge" not in text
    assert "merge_method" in text


def test_claude_md_says_slashes_in_branch_names_become_double_underscores():
    text = (ROOT / "CLAUDE.md").read_text()

    assert "any / in the branch name written as __" in text
