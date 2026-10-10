"""Claude Code hook: no pull request is marked ready for review until the reviewer passes it.

    review_gate.py record   SubagentStop, for the reviewer subagent. Saves its verdict
                            against the commit it reviewed.
    review_gate.py gate     PreToolUse. Blocks opening a PR that isn't a draft, and blocks
                            marking a PR ready or merging it until the current commit has
                            passed review. Merges must be merge commits of that exact commit.

Claude Code sends the event as JSON on stdin. Exit code 2 blocks, and stderr tells Claude why.
Any other failure would let the tool call through, so the gate turns its own errors into blocks.
Verdicts are kept in .git/claude-review/, so they are never committed. Standard library only,
so the hook runs with any python3.
"""

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

MAX_ROUNDS = 3
BLOCK = 2
REVIEWER = "reviewer"
LOOP = "See 'Review loop' in CLAUDE.md."

# Fail closed: anything that looks like marking ready is gated, even a quoted mention.
# A false block only costs a retry; a missed one skips the review.
# Each "pr ready" is exempt only if its own part of the command (up to ; & | or #) says --undo.
# gh accepts -R/--repo between "pr" and the subcommand.
PR = r"\bpr\s+(?:(?:-R|--repo)(?:=|\s+)\S+\s+)*"
GH_READY = re.compile(PR + r"ready\b([^;&|#\n]*)")
GRAPHQL_READY = "markPullRequestReadyForReview"
GH_MERGE = re.compile(PR + r"merge\b([^;&|#\n]*)")
# Merges the gate can't check for method and pinned head, so they are always blocked.
API_MERGE = re.compile(
    r"mergePullRequest|mergeBranch|pulls/[^/\s]+/merge\b|repos/[^/\s]+/[^/\s]+/merges\b"
)
API_AUTO_MERGE = "enablePullRequestAutoMerge"
GH_CREATE = re.compile(r"\bgh\b.*\bpr\s+create\b", re.DOTALL)
SEPARATORS = {";", "&", "&&", "|", "||", "\n", "(", ")"}
DRAFT_FLAGS = {"--draft", "-d", "--draft=true"}
VERDICT = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
# A dispatch that does the reviewer's job under some other subagent_type (typically
# "general-purpose", with "reviewer" only in the free-text description or prompt) is never
# recorded: record() only saves a verdict when agent_type is exactly "reviewer". Fail closed
# on the word appearing anywhere in the dispatch, same tradeoff as everywhere else in this
# file - a false block only costs a retry; a missed one skips the review silently.
DISPATCH_TOOLS = ("Agent", "Task")
REVIEW_WORD = re.compile(r"review", re.IGNORECASE)


def git(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def block(message):
    print(message, file=sys.stderr)
    sys.exit(BLOCK)


def state_file(cwd):
    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    folder = Path(git(cwd, "rev-parse", "--absolute-git-dir")) / "claude-review"
    return folder / (branch.replace("/", "__") + ".json")


def load_rounds(cwd):
    path = state_file(cwd)
    return json.loads(path.read_text())["rounds"] if path.exists() else []


def save_round(cwd, round_):
    path = state_file(cwd)
    rounds = load_rounds(cwd) + [round_]
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"rounds": rounds}, indent=2))


# --- record ---


def parse_verdict(message):
    blocks = VERDICT.findall(message or "")
    if not blocks:
        return None
    try:
        verdict = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None
    keys = ("blocking", "suggestions", "judgment_calls")
    if not isinstance(verdict, dict) or not all(isinstance(verdict.get(k), list) for k in keys):
        return None
    return verdict


def transcript_report(path):
    """The reviewer's final report, read from its own transcript. In a cloud session, the
    reviewer runs through a generic Agent tool rather than Claude Code's own subagent
    mechanism: SubagentStop still fires, but last_assistant_message is empty, and the
    reviewer hands its report back through a SubagentHandback tool call instead of a closing
    text message. That is read here as the fallback, in transcript order so the latest one
    found (a plain closing text message or a SubagentHandback) wins."""
    if not path:
        return None
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return None
    message = None
    for line in lines:
        try:
            content = json.loads(line).get("message", {}).get("content")
        except json.JSONDecodeError:
            continue
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                message = item["text"]
            elif item.get("type") == "tool_use" and item.get("name") == "SubagentHandback":
                text = (item.get("input") or {}).get("message")
                if isinstance(text, str):
                    message = text
    return message


def record(event):
    if event.get("agent_type") != REVIEWER:
        return
    cwd = event.get("cwd")
    message = event.get("last_assistant_message") or transcript_report(
        event.get("agent_transcript_path")
    )
    verdict = parse_verdict(message)
    problem = None
    if verdict is None:
        problem = (
            "Your review must end with a ```json block holding commit, blocking, "
            "suggestions and judgment_calls (see your instructions)."
        )
    else:
        # The verdict counts for the commit it names. If HEAD has moved on since, the gate
        # simply won't find a review for HEAD; the reviewer must never relabel its verdict.
        named = verdict.get("commit")
        commit = None
        if isinstance(named, str) and re.fullmatch(r"[0-9a-f]{40}", named):
            commit = git(cwd, "rev-parse", "--verify", "--quiet", f"{named}^{{commit}}")
        if commit is None:
            problem = (
                f"The verdict names commit {verdict.get('commit')}, which isn't a full sha of a "
                f"commit here. Name the commit you actually reviewed (HEAD is "
                f"{git(cwd, 'rev-parse', 'HEAD')} now; if that isn't what you reviewed, say so "
                "and review it from scratch)."
            )
    if problem:
        if event.get("stop_hook_active"):
            return  # Already sent back once; don't loop. Nothing is recorded, so the gate holds.
        block(problem)
    save_round(
        cwd,
        {
            "commit": commit,
            "blocking": len(verdict["blocking"]),
            "suggestions": len(verdict["suggestions"]),
            "judgment_calls": len(verdict["judgment_calls"]),
        },
    )


# --- gate ---


def marks_ready(command):
    if GRAPHQL_READY in command:
        return True
    return any(
        "--undo" not in match.group(1).split()
        for line in command.split("\n")
        if re.search(r"\bgh\b", line)
        for match in GH_READY.finditer(line)
    )


def creates_without_draft(command):
    """True if any gh pr create in the command lacks its own --draft. Unsure means True."""
    if not GH_CREATE.search(command):
        return False
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return True  # unbalanced quotes
    segments, current = [], []
    for word in words + [";"]:
        if word in SEPARATORS or set(word) <= set(";&|()\n"):
            segments.append(current)
            current = []
        else:
            current.append(word)
    creates = [
        seg
        for seg in segments
        if any(seg[i : i + 2] == ["pr", "create"] and "gh" in seg[:i] for i in range(len(seg)))
    ]
    if not creates:
        return True  # gh pr create is in there somewhere we can't read, such as bash -c "..."
    return any(not DRAFT_FLAGS & set(seg) for seg in creates)


def gh_merges(command):
    """(method, head sha) for each gh pr merge in the command, or 'auto' or 'admin' if any
    uses those flags."""
    merges = []
    for line in command.split("\n"):
        if not re.search(r"\bgh\b", line):
            continue
        for match in GH_MERGE.finditer(line):
            flags = match.group(1).split()
            if any(f == "--auto" or f.startswith("--auto=") for f in flags):
                return "auto"
            if any(f == "--admin" or f.startswith("--admin=") for f in flags):
                return "admin"
            method = next(
                (
                    m
                    for m in ("merge", "squash", "rebase")
                    if f"--{m}" in flags or f"-{m[0]}" in flags
                ),
                None,
            )
            sha = None
            for i, flag in enumerate(flags):
                if flag == "--match-head-commit" and i + 1 < len(flags):
                    sha = flags[i + 1].strip("'\"")
                elif flag.startswith("--match-head-commit="):
                    sha = flag.split("=", 1)[1].strip("'\"")
            merges.append((method, sha))
    return merges


def wants(event):
    """What the tool call does, as (action, detail). action is 'ready', 'merge', 'auto-merge',
    'admin-merge', 'api-merge', 'create-not-draft', 'misnamed-reviewer', or None for anything
    else. A merge's detail is [(method, head sha)]."""
    tool, args = event.get("tool_name") or "", event.get("tool_input") or {}
    if tool in DISPATCH_TOOLS and args.get("subagent_type") != REVIEWER:
        text = " ".join(str(args.get(key) or "") for key in ("description", "prompt"))
        if REVIEW_WORD.search(text):
            return "misnamed-reviewer", None
    if tool.startswith("mcp__") and tool.endswith("__update_pull_request"):
        return ("ready" if args.get("draft") is False else None), None
    if tool.startswith("mcp__") and tool.endswith("__create_pull_request"):
        return (None if args.get("draft") is True else "create-not-draft"), None
    if tool.startswith("mcp__") and tool.endswith("__merge_pull_request"):
        return "merge", [(args.get("merge_method"), args.get("expectedHeadSha"))]
    if tool.startswith("mcp__") and tool.endswith("__enable_pr_auto_merge"):
        return "auto-merge", None
    if tool == "Bash":
        command = str(args.get("command") or "").replace("\\\n", " ")  # join continuations
        if API_AUTO_MERGE in command:
            return "auto-merge", None
        if API_MERGE.search(command):
            return "api-merge", None
        merges = gh_merges(command)
        if merges in ("auto", "admin"):
            return f"{merges}-merge", None
        if merges:
            return "merge", merges
        if marks_ready(command):
            return "ready", None
        if creates_without_draft(command):
            return "create-not-draft", None
    return None, None


def check_merge(cwd, merges):
    head = git(cwd, "rev-parse", "HEAD")
    for method, sha in merges:
        if method != "merge":
            block(
                "Merge with a merge commit (merge_method: merge, or gh pr merge --merge) so the "
                "red/green history survives. " + LOOP
            )
        if sha != head:
            block(
                f"Pin the merge to the reviewed commit: expectedHeadSha (or gh pr merge "
                f"--match-head-commit) must be HEAD, {head}. " + LOOP
            )


def check_ready(cwd):
    if git(cwd, "rev-parse", "--abbrev-ref", "HEAD") in (None, "HEAD"):
        block("Not on a git branch here, so the review state can't be checked. " + LOOP)
    if git(cwd, "status", "--porcelain"):
        block("There are uncommitted changes. Commit and push them, then review. " + LOOP)
    commit = git(cwd, "rev-parse", "HEAD")
    upstream = git(cwd, "rev-parse", "@{upstream}")
    if upstream is None:
        block("This branch has no upstream. git push -u origin HEAD, then try again. " + LOOP)
    if upstream != commit:
        block("HEAD isn't pushed. git push, then review that commit. " + LOOP)

    rounds = load_rounds(cwd)
    failed = len({r["commit"] for r in rounds if r["blocking"] > 0})
    if failed >= MAX_ROUNDS:
        block(
            f"Stop: {failed} review rounds found blocking issues. Leave the PR as a draft and "
            "tell the user what is still blocking. " + LOOP
        )
    this = [r for r in rounds if r["commit"] == commit]
    if this and this[-1]["blocking"] == 0:
        return
    if this:
        block(
            f"Review round {failed} of {MAX_ROUNDS} found {this[-1]['blocking']} blocking "
            "issue(s). Fix them (red/green), push, and run the reviewer subagent again. " + LOOP
        )
    block(f"Commit {commit[:7]} hasn't been reviewed. Run the reviewer subagent first. " + LOOP)


def gate(event):
    action, detail = wants(event)
    if action is None:
        return
    if action == "misnamed-reviewer":
        block(
            "This dispatch does the reviewer's job but subagent_type isn't \"reviewer\" - set "
            'subagent_type: "reviewer" exactly (not a free-text description), or its verdict '
            "will never be recorded. " + LOOP
        )
    if action == "create-not-draft":
        block("Open the pull request as a draft. It is marked ready only after review. " + LOOP)
    if action == "auto-merge":
        block("No auto-merge: merge directly once review and CI have passed. " + LOOP)
    if action == "admin-merge":
        block("No --admin merges: they skip GitHub's own checks. " + LOOP)
    if action == "api-merge":
        block(
            "Merge with gh pr merge --merge --match-head-commit <HEAD sha>, or the MCP "
            "merge_pull_request tool, so the review gate can check it. " + LOOP
        )
    try:
        if action == "merge":
            check_merge(event.get("cwd"), detail)
        check_ready(event.get("cwd"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        block(f"The review gate failed ({error!r}), so it is blocking to be safe. " + LOOP)


def main():
    event = json.load(sys.stdin)
    {"record": record, "gate": gate}[sys.argv[1]](event)


if __name__ == "__main__":
    main()
