# devin-bridge

Devin Review, DeepWiki and PR-linked Devin sessions for every StationedAI repo, while the Devin
GitHub App stays installed in one Devin org (the **owner org**) and every teammate keeps their own
Devin org. No Enterprise plan and no GitHub App move needed.

This repo is three things:

| Part | Path | What it does |
|---|---|---|
| Devin plugin | `.devin-plugin/plugin.json`, `AGENTS.md`, `skills/pr-loop/` | Tells every Devin session to link itself to its PR and work through CI, review threads and Devin Review findings until the PR is clean. |
| GitHub Action | `action.yml`, `bridge/devin_bridge.py` | Forwards PR feedback to the linked session, runs `/bridge` commands, and runs the `devin-feedback` check. |
| Repo setup | `templates/`, `scripts/rollout.sh`, `scripts/add-teammate.sh` | Files every repo gets, and scripts that set secrets and open the setup PRs. |

## How it works

| You do | What happens | Which Devin org |
|---|---|---|
| Open a PR | Devin Review runs through the owner org's auto-review rule | owner |
| Comment `/bridge review` | Devin Review runs on the PR now | owner |
| Comment `/bridge <prompt>` | If a session is linked to the PR, the prompt goes to it. Otherwise a new session starts and is linked | the commenter's own org |
| Review, inline comment, PR comment, Devin Review finding | Copied into the session linked to the PR as a chat message, which wakes it | the session's org |
| Resolve every review thread | `devin-feedback` turns green | none |

- A PR is linked to a session by `<!-- devin-session: <org-id>/devin-... -->` in its body, and the
  session must carry the tag `pr:<owner/repo>#<n>`. The skill (or the bridge, for `/bridge`
  sessions) sets both, so editing the PR body alone can't redirect feedback.
- Only comments from repo members/collaborators and the Devin Review bot are forwarded or obeyed.
- Reviews always run in the owner org, so teammates' orgs never need Review permission.

### `/bridge`, not `/devin`

The Devin GitHub App answers every PR comment that starts with `/devin`. That only works if the
commenter has linked their GitHub account in Devin and is a member of a Devin org that has the repo
([docs](https://docs.devin.ai/integrations/gh#start-devin-from-a-pr-comment)). Here that's only the
owner org, so everyone else gets "Could not match GitHub user … to a Devin account". The bridge uses
`/bridge` so the two never collide.

- **Want native `/devin` too?** Be a member of the owner org and link your GitHub account at
  [Settings → Connections](https://app.devin.ai/account/connections)
  ([docs](https://docs.devin.ai/integrations/gh#user-linking)). Those sessions run in, and are
  billed to, the owner org.

### Native vs bridge

| Feature | Native GitHub App (owner org members) | Bridge (any teammate org) |
|---|---|---|
| Auto Devin Review on PRs | yes (owner org's auto-review rule) | same review |
| On-demand review | `/devin review` | `/bridge review` |
| Start a session from a PR comment | `/devin <prompt>` | `/bridge <prompt>` |
| Session gets PR comments | yes | yes, forwarded by the workflow |
| PR status in the session sidebar | yes | no |
| DeepWiki for private repos | in the owner org | through the Devin MCP connected with an owner-org key |
| Block merges until threads are resolved | branch rules (paid GitHub plan) | `devin-feedback` check; blocking needs a paid GitHub plan too |

## Owner org setup (once)

In the Devin org that owns the GitHub App:

1. Give the Devin GitHub App access to every repo: GitHub org Settings → GitHub Apps → Devin →
   Configure → Repository access.
2. Add an auto-review rule covering the repos and all PR authors (Settings → Review). The bridge
   only covers on-demand reviews.
3. Index the repos in DeepWiki if you want wiki answers.
4. Create a service user with the **Member** role (Settings → Service users) and keep its `cog_`
   key. Member includes Devin Review on Teams; check it with step 1 under "Check it works".

## Add a teammate

Each teammate's sessions run in, and are billed to, their own Devin org.

1. In **your** Devin org: Settings → Service users → create one with the **Member** role and copy
   its `cog_` key.
2. Save the same key as a Devin secret named `DEVIN_BRIDGE_KEY` in your Devin org (the skill uses it
   to tag sessions).
3. Install the plugin in your Devin org: Customize → Plugins → Add plugin → From repository →
   `https://github.com/StationedAI/devin-bridge`
   ([docs](https://docs.devin.ai/cli/extensibility/plugins/quickstart)). Repos rolled out with this
   repo also require it in `.devin/config.json`, which loads it in any session that clones them.
4. Put your key on the repos and map your GitHub login (needs write access to the repos and a
   `gh` login):
   ```sh
   git clone https://github.com/StationedAI/devin-bridge && cd devin-bridge
   scripts/add-teammate.sh StationedAI <your-github-login>
   ```
   It asks for the key, checks it with `GET /v3/self`, and stores it as the secret
   `DEVIN_KEY_<ORG_ID>` on every repo, so nobody else's key is read or overwritten. Run it again
   with `-` instead of a login to add a key without changing the mapping, or with repo names at
   the end to limit it. Several GitHub logins can map to the same org; each login maps to one.

## Add repos

```sh
OWNER_DEVIN_API_KEY=cog_... OWNER_DEVIN_ORG_ID=org-... scripts/rollout.sh StationedAI [repo ...]
```

Per repo it sets `OWNER_DEVIN_API_KEY` (secret) and `OWNER_DEVIN_ORG_ID` (variable), copies any
`DEVIN_KEY_*` environment variables in as secrets, merges `$DEVIN_USER_ORGS` (JSON) into the
variable of the same name, and opens one PR adding `templates/`. It is safe to re-run.

- **New repos:** create them from [StationedAI/repo-template](https://github.com/StationedAI/repo-template),
  which already has the files. GitHub copies template files but not secrets, so run `rollout.sh`
  and `add-teammate.sh` with the new repo's name afterwards.
- **Why per-repo secrets?** On GitHub Free, organization secrets and variables are not available to
  private repos ([docs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)).
  On a paid plan you can set the same names once at the org level instead; the workflow passes
  `toJSON(secrets)`, so org and repo secrets both reach the bridge.
- **Updates:** repos use `StationedAI/devin-bridge@v1`. Moving the `v1` tag updates every repo at once.

## Repo secrets and variables

| Name | Kind | Value |
|---|---|---|
| `OWNER_DEVIN_API_KEY` | secret | owner org service-user key (reviews, review-all) |
| `OWNER_DEVIN_ORG_ID` | variable | owner org id |
| `DEVIN_KEY_<ORG_ID>` | secret, one per Devin org | that org's service-user key; org id upper-cased, non-alphanumerics → `_` (e.g. `org-ab12` → `DEVIN_KEY_ORG_AB12`) |
| `DEVIN_USER_ORGS` | variable | `{"<github-login>": "<devin-org-id>", ...}`: the org a `/bridge <prompt>` session starts in |
| `DEVIN_FALLBACK_ORG` | secret (or `fallback-org` input) | optional: Devin org to use when a session's org is out of credits; it also needs its own `DEVIN_KEY_<ORG_ID>` |
| `DEVIN_ORG_KEYS` | secret, legacy | `{"<org-id>": "cog_..."}`; still read, prefer `DEVIN_KEY_*` |

## Check it works

1. **Owner key can review:** comment `/bridge review` on an open PR. The bridge replies
   "Devin Review queued for `<sha>`".
2. **Teammate key:** `curl -s https://api.devin.ai/v3/self -H "Authorization: Bearer cog_..."`
   returns your org id.
3. **Forwarding:** on a PR your session opened and linked, leave an inline comment. The Actions
   log of `devin-bridge` prints `forwarded review comment to <org>/<session>`, and the session gets
   the message.
4. **Gate:** leave a thread unresolved. `devin-feedback` is red; resolve it and re-run
   `gh workflow run devin-feedback.yml -f pr=<n> --ref <branch>`.

## Credit fallback

Set the `DEVIN_FALLBACK_ORG` secret to another Devin org (for example an Enterprise account with
credits) and add that org's `DEVIN_KEY_<ORG_ID>`. Then:

- **`/bridge <prompt>`:** if the commenter's org refuses the session for credit/quota reasons, or the
  new session is suspended right away with a credit `status_detail`, the session starts in the
  fallback org instead (tagged `fallback-from:<org>`).
- **Feedback on a PR whose session is suspended for credits** (`out_of_credits`, `out_of_quota`,
  `org_usage_limit_exceeded`, ...): sessions can't move between orgs, so the bridge starts a
  continuation session in the fallback org with the PR context and the new feedback, relinks the PR
  to it (tagged `continued-from:<org>/<session>`) and says so on the PR.
- **Without a fallback** the message is still queued on the suspended session and the PR gets a
  notice that nobody will act on it until that org has credits again.
- Sessions asleep for `inactivity` are not credit stops; they get the message and wake as usual.
- **Reviews don't fall back.** Devin Review is billed to the org that has the GitHub App.
- The Devin API doesn't document which HTTP status means "out of credits", so a refused start
  counts as a credit refusal on 402, or on another non-401/404/422 error whose body mentions
  credits, quota, limits, budget, billing or payment. Auth and validation errors never fall back.
- The fallback org's sessions need `DEVIN_BRIDGE_KEY` (its own key) like any other org.

## Limits

- `issue_comment` workflows (PR comments and `/bridge` commands) run from the default branch, so
  they start working only after the setup PR is merged. Review and review-comment events run from
  the PR's branch.
- GitHub has no "review thread resolved" trigger, so `devin-feedback` re-runs on pushes, reviews
  and review comments, or by hand.
- On GitHub Free, private repos can't have branch protection or rulesets, so `devin-feedback`
  can't block a merge. It still shows red.
- Plugin hooks are best effort in Devin cloud sessions
  ([docs](https://docs.devin.ai/cli/extensibility/plugins/overview)), so this plugin uses a rule and a
  skill, not hooks, and the workflow does the enforcement.
- PRs from forks don't get secrets, so the bridge skips them.

## Develop

```sh
python3 -m pytest -q        # bridge tests, GitHub and Devin calls mocked
```

Release: merge to `main`, then `git tag -f v1 && git push -f origin v1`.
