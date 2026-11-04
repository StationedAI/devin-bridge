---
name: pr-loop
description: Link this Devin session to its GitHub PR through the devin-bridge workflow and keep handling CI, review threads and Devin Review findings until the PR is clean. Use after opening or taking over a PR in a repo that has .github/workflows/devin-bridge.yml.
---

# devin-bridge PR loop

The repo's Devin GitHub App belongs to another Devin org (the owner org), so native PR linking does
not reach this session. The `devin-bridge` workflow forwards PR feedback to the session linked in
the PR body, and runs Devin Review in the owner org.

## 1. Link the session (right after opening the PR)
- Set concrete values first: `ORG` = your `org_id`, `SID` = your `devin_id` (both are in your
  identity info), `REPO` = `owner/name` as GitHub shows it, `PR` = the PR number.
- In the PR body, delete every existing `<!-- devin-session: ... -->` marker (a previous
  session's), keep the rest of the text, and append `<!-- devin-session: $ORG/$SID -->`:
  `gh pr edit "$PR" -R "$REPO" --body-file <file>`
- Tag yourself so the bridge trusts that marker (the tag must be exactly `pr:$REPO#$PR`):
  ```sh
  curl -fsS -X POST "https://api.devin.ai/v3/organizations/$ORG/sessions/$SID/tags" \
    -H "Authorization: Bearer $DEVIN_BRIDGE_KEY" -H "Content-Type: application/json" \
    -d "{\"tags\":[\"devin-bridge\",\"pr:${REPO}#${PR}\"]}"
  ```
  `DEVIN_BRIDGE_KEY` is your Devin org's service-user `cog_` key, saved as a Devin secret (the
  same key the repo holds as `DEVIN_KEY_<ORG_ID>`). If it is missing, ask the user for it.
  Without the tag, PR feedback is not forwarded to you.
- Sessions started by a `/bridge <prompt>` comment are already linked and tagged; skip this step.

## 2. Review loop (after every push)
1. Wait for CI: `gh api repos/$REPO/commits/<sha>/check-runs` (or your git PR checks tool).
2. Read all feedback, including Devin Review (`devin-ai-integration[bot]`):
   `gh api repos/$REPO/pulls/$PR/reviews` (review bodies), `gh api repos/$REPO/pulls/$PR/comments`
   (inline), and `gh api repos/$REPO/issues/$PR/comments` (PR comments).
3. Fix real issues and push. Reply to each thread with what changed (or why not), then resolve
   it (GraphQL `resolveReviewThread`). The `devin-feedback` check stays red while any thread is
   unresolved. GitHub has no "thread resolved" trigger, so re-run it yourself afterwards:
   `gh workflow run devin-feedback.yml -R "$REPO" --ref <pr-branch> -f pr="$PR"`
4. Repeat until CI and `devin-feedback` are green. Do not end the session before that.
- End every PR comment you post with `<!-- devin-bridge -->` so the bridge does not forward it
  back to you.
- Need a fresh review? Comment `/bridge review` on the PR; it runs in the owner org.

## Never
- Never merge unless the user says so. Never paste keys into PR text, comments or logs.
