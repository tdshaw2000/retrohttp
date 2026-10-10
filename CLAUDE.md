# retrohttp

SPEC.md is the spec. Work test-first, with red and green commits so the history shows TDD.
Open PRs as drafts. Once review and CI pass, Claude marks the PR ready and merges it with a
merge commit (never squash or rebase). The owner is told what shipped but does not merge.

Tests use saved pages under fixtures/, never the live web.

## Review loop

Every PR is reviewed by the `reviewer` subagent (.claude/agents/reviewer.md) before it is marked
ready or merged. A hook (.claude/hooks/review_gate.py) enforces this. It blocks opening a PR
that isn't a draft. It blocks marking ready or merging until the pushed HEAD commit has a
passing review. It blocks merges that aren't merge commits of that exact commit, and it blocks
auto-merge, --admin merges, and merges through gh api. It does not stop direct pushes to main.

1. Finish the work, commit, push, and open the PR as a draft.
2. Run the `reviewer` subagent. Give it the PR number and one line on what the change is for.
   Don't tell it what to conclude. Its verdict is recorded automatically when it stops.
3. **Blocking findings**: fix each one test-first, push, and run the reviewer again. The hook
   allows 3 rounds with blocking findings. After the third, stop: leave the PR open and
   unmerged and tell the owner what is still blocking and why. If you think a blocking finding
   is wrong, give the reviewer your evidence on the next round; it still counts as a round.
4. **Once it passes**, post the suggestions as one PR review with event COMMENT and one inline
   comment per suggestion (pull_request_review_write create, add_comment_to_pending_review,
   then submit_pending). Don't act on suggestions unless they are trivial.
5. **Judgment calls** take the reviewer's recommendation (the owner chose this). List each one,
   with the choice made, in the PR review body and in the message to the owner.
6. Wait for CI to be green on the reviewed commit (pull_request_read get_check_runs). Then mark
   the PR ready and merge it: merge_pull_request with merge_method "merge" and
   expectedHeadSha set to the reviewed commit.
7. Tell the owner in the thread, in plain words, what shipped and any judgment calls taken.

Limits, on purpose: the hook only runs inside Claude Code sessions, so a PR pushed by hand is
not reviewed. It checks the branch you are on, so mark ready and merge from the PR's own
branch (expectedHeadSha makes GitHub refuse a merge of any other head). It does not check CI;
step 6 does. It guards against mistakes, not against an agent that edits its state.

Verdicts live in .git/claude-review/, one file per branch, and are never committed. Rounds are
counted per commit reviewed. To start a branch's count again (only when the owner says so):
`rm .git/claude-review/<branch>.json`, with any / in the branch name written as __.
