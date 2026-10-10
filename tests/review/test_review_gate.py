"""The review gate hook (.claude/hooks/review_gate.py).

It has two jobs:
- `record` runs when the reviewer subagent stops, and saves its verdict against the commit it
  reviewed.
- `gate` runs before a pull request is opened or marked ready for review, and blocks that until
  the current commit has passed review.

Each test drives the real script the way Claude Code does: JSON on stdin, a blocking message on
stderr with exit code 2.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude/hooks/review_gate.py"
BLOCKED = 2


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A feature branch with one commit, pushed to a remote."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    git(tmp_path, "init", "-b", "main", str(work))
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "test@example.com")
    git(work, "remote", "add", "origin", str(remote))
    (work / "a.py").write_text("x = 1\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "first")
    git(work, "push", "-u", "origin", "main")
    git(work, "checkout", "-b", "feature")
    new_commit(work)
    return work


def new_commit(repo, push=True):
    path = repo / "a.py"
    path.write_text(path.read_text() + "y = 2\n")
    git(repo, "commit", "-am", "change")
    if push:
        git(repo, "push", "-u", "origin", "HEAD")
    return head(repo)


def head(repo):
    return git(repo, "rev-parse", "HEAD")


def run(mode, payload):
    return subprocess.run(
        [sys.executable, str(SCRIPT), mode],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def verdict(commit, blocking=0, suggestions=0, judgment_calls=0):
    finding = {"file": "a.py", "line": 1, "summary": "s", "detail": "d"}
    body = {
        "commit": commit,
        "blocking": [finding] * blocking,
        "suggestions": [finding] * suggestions,
        "judgment_calls": [{"question": "q", "options": ["a", "b"]}] * judgment_calls,
    }
    return f"Review notes.\n\n```json\n{json.dumps(body, indent=2)}\n```\n"


def record(repo, message, agent_type="reviewer", stop_hook_active=False):
    return run(
        "record",
        {
            "hook_event_name": "SubagentStop",
            "cwd": str(repo),
            "agent_type": agent_type,
            "agent_id": "a1",
            "stop_hook_active": stop_hook_active,
            "last_assistant_message": message,
        },
    )


def review(repo, blocking=0, **counts):
    result = record(repo, verdict(head(repo), blocking=blocking, **counts))
    assert result.returncode == 0, result.stderr


def write_transcript(repo, *contents):
    """contents is a list of message "content" lists (Claude Code transcript JSONL shape).
    Written as a sibling of the repo, not inside it, since it isn't part of the checkout."""
    path = repo.parent / "transcript.jsonl"
    lines = [json.dumps({"message": {"content": content}}) for content in contents]
    path.write_text("\n".join(lines) + "\n")
    return path


def record_via_transcript(
    repo, transcript_path, agent_type="reviewer", last_assistant_message=None
):
    return run(
        "record",
        {
            "hook_event_name": "SubagentStop",
            "cwd": str(repo),
            "agent_type": agent_type,
            "agent_id": "a1",
            "stop_hook_active": False,
            "last_assistant_message": last_assistant_message,
            "agent_transcript_path": str(transcript_path),
        },
    )


def mark_ready(repo):
    return run(
        "gate",
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(repo),
            "tool_name": "mcp__github__update_pull_request",
            "tool_input": {"owner": "o", "repo": "r", "pullNumber": 1, "draft": False},
        },
    )


def bash(repo, command):
    return run(
        "gate",
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": command},
        },
    )


# --- The gate: marking a PR ready for review ---


def test_marking_ready_is_blocked_until_the_commit_has_been_reviewed(repo):
    result = mark_ready(repo)

    assert result.returncode == BLOCKED
    assert "reviewer" in result.stderr


def test_marking_ready_is_allowed_once_the_commit_passes_review(repo):
    review(repo)

    assert mark_ready(repo).returncode == 0


def test_suggestions_and_judgment_calls_do_not_block(repo):
    review(repo, suggestions=2, judgment_calls=1)

    assert mark_ready(repo).returncode == 0


def test_blocking_findings_block_and_say_which_round_this_is(repo):
    review(repo, blocking=2)

    result = mark_ready(repo)

    assert result.returncode == BLOCKED
    assert "round 1 of 3" in result.stderr


def test_a_new_commit_needs_a_new_review(repo):
    review(repo)
    new_commit(repo)

    result = mark_ready(repo)

    assert result.returncode == BLOCKED
    assert "reviewer" in result.stderr


def test_a_fix_that_passes_re_review_is_allowed(repo):
    review(repo, blocking=1)
    new_commit(repo)
    review(repo)

    assert mark_ready(repo).returncode == 0


def test_after_three_rounds_with_blocking_findings_it_stops_and_hands_over(repo):
    for _ in range(3):
        review(repo, blocking=1)
        new_commit(repo)

    result = mark_ready(repo)

    assert result.returncode == BLOCKED
    assert "Stop" in result.stderr
    assert "draft" in result.stderr


def test_a_third_round_that_passes_is_still_allowed(repo):
    for _ in range(2):
        review(repo, blocking=1)
        new_commit(repo)
    review(repo)

    assert mark_ready(repo).returncode == 0


def test_rounds_are_counted_per_branch(repo):
    for _ in range(3):
        review(repo, blocking=1)
        new_commit(repo)
    git(repo, "checkout", "-b", "other-feature")
    new_commit(repo)
    review(repo)

    assert mark_ready(repo).returncode == 0


def test_uncommitted_changes_block_marking_ready(repo):
    review(repo)
    (repo / "a.py").write_text("changed but not committed\n")

    result = mark_ready(repo)

    assert result.returncode == BLOCKED
    assert "uncommitted" in result.stderr


def test_untracked_files_block_marking_ready(repo):
    review(repo)
    (repo / "new.py").write_text("unreviewed\n")

    assert mark_ready(repo).returncode == BLOCKED


def test_an_unpushed_commit_blocks_marking_ready(repo):
    new_commit(repo, push=False)
    review(repo)

    result = mark_ready(repo)

    assert result.returncode == BLOCKED
    assert "push" in result.stderr


def test_gh_pr_ready_is_gated_too(repo):
    assert bash(repo, "gh pr ready 12").returncode == BLOCKED
    review(repo)
    assert bash(repo, "gh pr ready 12").returncode == 0


def test_gh_pr_ready_undo_is_not_gated(repo):
    assert bash(repo, "gh pr ready 12 --undo").returncode == 0


def test_gh_pr_ready_undo_on_a_continuation_line_is_not_gated(repo):
    assert bash(repo, "gh pr ready 12 \\\n  --undo").returncode == 0


# --- The gate: opening a PR ---


def test_opening_a_pr_that_is_not_a_draft_is_blocked(repo):
    result = run(
        "gate",
        {
            "cwd": str(repo),
            "tool_name": "mcp__github__create_pull_request",
            "tool_input": {"title": "t", "head": "feature", "base": "main"},
        },
    )

    assert result.returncode == BLOCKED
    assert "draft" in result.stderr


def test_opening_a_draft_pr_is_allowed_without_a_review(repo):
    result = run(
        "gate",
        {
            "cwd": str(repo),
            "tool_name": "mcp__github__create_pull_request",
            "tool_input": {"title": "t", "head": "feature", "base": "main", "draft": True},
        },
    )

    assert result.returncode == 0


def test_gh_pr_create_needs_the_draft_flag(repo):
    assert bash(repo, "gh pr create --title t --body b").returncode == BLOCKED
    assert bash(repo, "gh pr create --draft --title t --body b").returncode == 0


# --- The gate leaves everything else alone ---


@pytest.mark.parametrize(
    "tool_name, tool_input",
    [
        ("Bash", {"command": "uv run pytest"}),
        ("mcp__github__update_pull_request", {"title": "new title"}),
        ("mcp__github__update_pull_request", {"draft": True}),
        ("Read", {"file_path": "/x"}),
    ],
)
def test_other_tool_calls_pass_through(repo, tool_name, tool_input):
    result = run("gate", {"cwd": str(repo), "tool_name": tool_name, "tool_input": tool_input})

    assert result.returncode == 0, result.stderr


# --- Recording the reviewer's verdict from its transcript (cloud subagents) ---
#
# In a cloud session, the reviewer subagent runs through a generic "Agent" tool instead of
# Claude Code's own native subagent mechanism. SubagentStop still fires, but
# last_assistant_message is empty; the reviewer hands its report back through a
# SubagentHandback tool call in its own transcript instead of a closing text message.


def test_a_verdict_handed_back_via_subagenthandback_in_the_transcript_is_recorded(repo):
    transcript = write_transcript(
        repo,
        [{"type": "text", "text": "Reviewing..."}],
        [
            {
                "type": "tool_use",
                "name": "SubagentHandback",
                "input": {"message": verdict(head(repo))},
            }
        ],
    )

    result = record_via_transcript(repo, transcript)

    assert result.returncode == 0, result.stderr
    assert mark_ready(repo).returncode == 0


def test_the_transcript_is_only_read_when_last_assistant_message_is_empty(repo):
    transcript = write_transcript(
        repo,
        [{"type": "tool_use", "name": "SubagentHandback", "input": {"message": "Looks good!"}}],
    )

    result = record_via_transcript(repo, transcript, last_assistant_message=verdict(head(repo)))

    assert result.returncode == 0, result.stderr
    assert mark_ready(repo).returncode == 0


def test_the_latest_message_in_the_transcript_wins(repo):
    transcript = write_transcript(
        repo,
        [{"type": "tool_use", "name": "SubagentHandback", "input": {"message": "Looks good!"}}],
        [
            {
                "type": "tool_use",
                "name": "SubagentHandback",
                "input": {"message": verdict(head(repo))},
            }
        ],
    )

    result = record_via_transcript(repo, transcript)

    assert result.returncode == 0, result.stderr
    assert mark_ready(repo).returncode == 0


def test_a_plain_closing_text_message_in_the_transcript_is_read_too(repo):
    transcript = write_transcript(repo, [{"type": "text", "text": verdict(head(repo))}])

    result = record_via_transcript(repo, transcript)

    assert result.returncode == 0, result.stderr
    assert mark_ready(repo).returncode == 0


def test_a_missing_transcript_file_sends_the_reviewer_back_like_any_bad_verdict(repo):
    result = record_via_transcript(repo, repo / "no-such-file.jsonl")

    assert result.returncode == BLOCKED
    assert "```json" in result.stderr


def test_other_subagents_are_not_recorded_from_the_transcript_either(repo):
    transcript = write_transcript(
        repo,
        [
            {
                "type": "tool_use",
                "name": "SubagentHandback",
                "input": {"message": verdict(head(repo))},
            }
        ],
    )

    result = record_via_transcript(repo, transcript, agent_type="Explore")

    assert result.returncode == 0
    assert mark_ready(repo).returncode == BLOCKED


# --- Recording the reviewer's verdict ---


def test_a_verdict_without_the_json_block_sends_the_reviewer_back(repo):
    result = record(repo, "Looks good to me!")

    assert result.returncode == BLOCKED
    assert "```json" in result.stderr


def test_a_verdict_for_an_older_commit_is_kept_for_that_commit_only(repo):
    reviewed = head(repo)
    new_commit(repo)

    assert record(repo, verdict(reviewed)).returncode == 0
    result = mark_ready(repo)

    assert result.returncode == BLOCKED
    assert "reviewer" in result.stderr


def test_a_verdict_for_a_commit_that_does_not_exist_sends_the_reviewer_back(repo):
    result = record(repo, verdict("0" * 40))

    assert result.returncode == BLOCKED
    assert "0" * 40 in result.stderr


def test_a_second_bad_verdict_is_let_go_but_not_recorded(repo):
    result = record(repo, "still no block", stop_hook_active=True)

    assert result.returncode == 0
    assert mark_ready(repo).returncode == BLOCKED


def test_other_subagents_are_not_recorded(repo):
    result = record(repo, verdict(head(repo)), agent_type="Explore")

    assert result.returncode == 0
    assert mark_ready(repo).returncode == BLOCKED


def test_the_last_json_block_is_the_verdict(repo):
    message = 'Example:\n```json\n{"blocking": []}\n```\n' + verdict(head(repo))

    assert record(repo, message).returncode == 0
    assert mark_ready(repo).returncode == 0


def test_review_state_lives_inside_git_so_it_is_never_committed(repo):
    review(repo)

    assert git(repo, "status", "--porcelain") == ""


def test_marking_ready_outside_a_git_repo_is_blocked_not_crashed(tmp_path):
    result = run(
        "gate",
        {
            "cwd": str(tmp_path),
            "tool_name": "mcp__github__update_pull_request",
            "tool_input": {"draft": False},
        },
    )

    assert result.returncode == BLOCKED
    assert "git" in result.stderr


# --- Round 1 review fixes ---


def test_a_pass_after_three_failed_rounds_is_still_blocked(repo):
    for _ in range(3):
        review(repo, blocking=1)
        new_commit(repo)
    review(repo)

    result = mark_ready(repo)

    assert result.returncode == BLOCKED
    assert "Stop" in result.stderr


def test_re_reviewing_the_same_commit_counts_as_one_round(repo):
    review(repo, blocking=1)
    review(repo, blocking=1)
    review(repo, blocking=1)
    new_commit(repo)
    review(repo)

    assert mark_ready(repo).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "(gh pr ready 12)",
        "echo 1; $(gh pr ready 12)",
        "GH_REPO=o/r gh pr ready 12",
        "command gh pr ready 12",
        'bash -c "gh pr ready 12"',
        "gh api graphql -f query='mutation { markPullRequestReadyForReview(input: {}) { x } }'",
        "echo 'gh pr ready' is how you mark it",
    ],
)
def test_every_shell_form_of_marking_ready_is_gated(repo, command):
    assert bash(repo, command).returncode == BLOCKED


@pytest.mark.parametrize(
    "command",
    [
        'gh pr create --title "fix; tidy" --draft --body b',
        "gh pr create --title t --body \"$(cat <<'EOF'\nSummary\n\nMore | detail\nEOF\n)\" --draft",
        "gh pr create -d --title t",
        "gh pr create \\\n  --draft \\\n  --fill",
    ],
)
def test_draft_creates_are_allowed_however_the_body_is_written(repo, command):
    result = bash(repo, command)

    assert result.returncode == 0, result.stderr


def test_the_github_server_may_have_another_name(repo):
    result = run(
        "gate",
        {
            "cwd": str(repo),
            "tool_name": "mcp__plugin_gh_github__update_pull_request",
            "tool_input": {"draft": False},
        },
    )

    assert result.returncode == BLOCKED


def test_a_corrupt_state_file_blocks_instead_of_failing_open(repo):
    review(repo)
    state = next((repo / ".git/claude-review").iterdir())
    state.write_text("{bad")

    result = mark_ready(repo)

    assert result.returncode == BLOCKED
    assert "review gate" in result.stderr


def test_a_pushed_branch_without_an_upstream_is_told_to_set_one(repo):
    git(repo, "checkout", "-b", "no-upstream")
    new_commit(repo, push=False)
    git(repo, "push", "origin", "HEAD")
    review(repo)

    result = mark_ready(repo)

    assert result.returncode == BLOCKED
    assert "push -u" in result.stderr


# --- Round 2 review fixes ---


@pytest.mark.parametrize(
    "command",
    [
        'gh pr create --title t --body "Run docker compose up -d first"',
        "git branch -d old && gh pr create --title t --body b",
        "gh pr create --draft=false --title t --body b",
        "gh pr create --title t --body b; gh pr create --draft --title u",
        "gh pr create --title t\necho --draft",
        'gh pr create --title "unbalanced',
        'bash -c "gh pr create --title t --draft"',
    ],
)
def test_a_draft_flag_only_counts_on_its_own_gh_pr_create(repo, command):
    result = bash(repo, command)

    assert result.returncode == BLOCKED
    assert "draft" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "gh pr ready 13 && gh pr ready 12 --undo",
        "gh pr ready 13  # no --undo",
        "gh pr \\\n  ready 13",
        "gh \\\n pr ready 13",
    ],
)
def test_undo_and_line_continuations_do_not_hide_marking_ready(repo, command):
    assert bash(repo, command).returncode == BLOCKED


def test_a_null_command_is_not_a_crash(repo):
    result = run("gate", {"cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": None}})

    assert result.returncode == 0, result.stderr


def test_a_verdict_commit_that_is_not_a_sha_is_never_passed_to_git(repo):
    result = record(repo, verdict("--output=/tmp/x"))

    assert result.returncode == BLOCKED
    assert "full sha" in result.stderr


# --- Merging: Claude merges once review passes (owner's decision, 2026-09-25) ---


def merge(repo, **overrides):
    tool_input = {
        "owner": "o",
        "repo": "r",
        "pullNumber": 1,
        "merge_method": "merge",
        "expectedHeadSha": head(repo),
    }
    tool_input.update(overrides)
    return run(
        "gate",
        {
            "cwd": str(repo),
            "tool_name": "mcp__github__merge_pull_request",
            "tool_input": tool_input,
        },
    )


def test_merging_is_blocked_until_the_commit_passes_review(repo):
    result = merge(repo)

    assert result.returncode == BLOCKED
    assert "reviewer" in result.stderr


def test_merging_is_allowed_once_the_commit_passes_review(repo):
    review(repo)

    assert merge(repo).returncode == 0


def test_merging_after_three_failed_rounds_is_blocked(repo):
    for _ in range(3):
        review(repo, blocking=1)
        new_commit(repo)
    review(repo)

    result = merge(repo)

    assert result.returncode == BLOCKED
    assert "Stop" in result.stderr


@pytest.mark.parametrize("method", ["squash", "rebase", None])
def test_merging_must_use_a_merge_commit(repo, method):
    review(repo)

    result = merge(repo, merge_method=method)

    assert result.returncode == BLOCKED
    assert "merge commit" in result.stderr


@pytest.mark.parametrize("sha", ["0" * 40, None])
def test_merging_must_name_the_reviewed_head(repo, sha):
    review(repo)

    result = merge(repo, expectedHeadSha=sha)

    assert result.returncode == BLOCKED
    assert "expectedHeadSha" in result.stderr


def test_gh_pr_merge_is_gated_and_needs_a_merge_commit_and_the_head(repo):
    ok = f"gh pr merge 13 --merge --match-head-commit {head(repo)}"
    assert bash(repo, ok).returncode == BLOCKED
    review(repo)
    assert bash(repo, ok).returncode == 0
    assert bash(repo, f"gh pr merge 13 --squash --match-head-commit {head(repo)}").returncode == 2
    assert bash(repo, "gh pr merge 13 --merge").returncode == BLOCKED


@pytest.mark.parametrize(
    "tool_name, tool_input",
    [
        ("mcp__github__enable_pr_auto_merge", {"pullNumber": 1}),
        ("Bash", {"command": "gh pr merge 13 --auto --merge"}),
    ],
)
def test_auto_merge_is_blocked(repo, tool_name, tool_input):
    review(repo)
    if tool_name == "Bash":
        tool_input["command"] += f" --match-head-commit {head(repo)}"

    result = run("gate", {"cwd": str(repo), "tool_name": tool_name, "tool_input": tool_input})

    assert result.returncode == BLOCKED
    assert "auto-merge" in result.stderr


# --- Merge gating, review round 1 fixes ---


@pytest.mark.parametrize(
    "command",
    [
        "gh api -X PUT repos/o/r/pulls/13/merge -f merge_method=merge",
        "gh api graphql -f query='mutation{mergePullRequest(input:{pullRequestId:\"X\"}){x}}'",
    ],
)
def test_merging_through_gh_api_is_blocked_even_after_review(repo, command):
    review(repo)

    result = bash(repo, command)

    assert result.returncode == BLOCKED
    assert "gh pr merge --merge" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "gh api graphql -f query='mutation{enablePullRequestAutoMerge(input:{}){x}}'",
        "gh pr merge 13 --merge --auto=true --match-head-commit HEADSHA",
    ],
)
def test_every_form_of_auto_merge_is_blocked(repo, command):
    review(repo)

    result = bash(repo, command.replace("HEADSHA", head(repo)))

    assert result.returncode == BLOCKED
    assert "auto-merge" in result.stderr


def test_admin_merges_are_blocked(repo):
    review(repo)

    result = bash(repo, f"gh pr merge 13 --merge --admin --match-head-commit {head(repo)}")

    assert result.returncode == BLOCKED
    assert "--admin" in result.stderr


@pytest.mark.parametrize(
    "form",
    [
        '--match-head-commit "SHA"',
        "--match-head-commit 'SHA'",
        '--match-head-commit="SHA"',
        "--match-head-commit=SHA",
    ],
)
def test_a_quoted_head_sha_is_accepted(repo, form):
    review(repo)

    result = bash(repo, "gh pr merge 13 --merge " + form.replace("SHA", head(repo)))

    assert result.returncode == 0, result.stderr


# --- Merge gating, review round 2 fixes ---


@pytest.mark.parametrize(
    "command",
    [
        "gh pr -R o/r merge 13 --squash",
        "gh pr --repo=o/r merge 13 --squash --admin",
        "gh pr --repo o/r ready 13",
        "gh -R o/r pr merge 13 --squash",
    ],
)
def test_repo_flags_before_the_subcommand_are_still_gated(repo, command):
    assert bash(repo, command).returncode == BLOCKED


@pytest.mark.parametrize(
    "command",
    [
        "gh api repos/o/r/merges -f base=main -f head=feat",
        "gh api graphql -f query='mutation{mergeBranch(input:{}){x}}'",
    ],
)
def test_branch_merges_through_gh_api_are_blocked(repo, command):
    review(repo)

    assert bash(repo, command).returncode == BLOCKED


@pytest.mark.parametrize(
    "command",
    [
        "git push -u origin review-loop",
        "git push origin main-fix",
        "git push",
        "git pull origin main",
    ],
)
def test_ordinary_pushes_are_not_gated(repo, command):
    assert bash(repo, command).returncode == 0


# --- The gate: dispatching the reviewer under the wrong name ---
#
# record() only saves a verdict when the SubagentStop event's agent_type is "reviewer". A
# dispatch that does the reviewer's job (even word for word) but is launched with some other
# subagent_type - e.g. "general-purpose", with "reviewer" only in its free-text description or
# prompt - is silently never recorded, and the gate stays shut with no clue why. Caught this
# recorded from a real PR driven from a cloud session: every reviewer dispatch used
# subagent_type "general-purpose", so .git/claude-review/ was never created at all.


def dispatch(repo, tool_name="Agent", subagent_type=None, description="", prompt=""):
    tool_input = {"description": description, "prompt": prompt}
    if subagent_type is not None:
        tool_input["subagent_type"] = subagent_type
    return run(
        "gate",
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(repo),
            "tool_name": tool_name,
            "tool_input": tool_input,
        },
    )


def test_a_review_dispatch_under_the_wrong_subagent_type_is_blocked(repo):
    result = dispatch(
        repo,
        subagent_type="general-purpose",
        description="Data/deploy reviewer re-review PR 25",
        prompt="Re-review the branch and end with a verdict json block.",
    )

    assert result.returncode == BLOCKED
    assert "reviewer" in result.stderr


def test_a_review_dispatch_is_caught_from_the_prompt_too(repo):
    result = dispatch(
        repo,
        subagent_type="general-purpose",
        description="Follow-up work",
        prompt="Act as the reviewer subagent and review this branch against main.",
    )

    assert result.returncode == BLOCKED


def test_dispatching_the_real_reviewer_subagent_is_allowed(repo):
    result = dispatch(
        repo,
        subagent_type="reviewer",
        description="Review PR 25",
        prompt="Review the branch.",
    )

    assert result.returncode == 0, result.stderr


def test_an_unrelated_general_purpose_dispatch_is_not_gated(repo):
    result = dispatch(
        repo,
        subagent_type="general-purpose",
        description="Investigate the network gap",
        prompt="Find out why the container keeps dropping off the shared network.",
    )

    assert result.returncode == 0, result.stderr


def test_a_dispatch_with_no_subagent_type_at_all_is_still_checked(repo):
    result = dispatch(
        repo,
        subagent_type=None,
        description="reviewer",
        prompt="Review this.",
    )

    assert result.returncode == BLOCKED
