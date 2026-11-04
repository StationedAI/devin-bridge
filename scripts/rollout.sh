#!/usr/bin/env bash
# Enable devin-bridge on every non-archived repo of a GitHub org (or only the repos named).
#   OWNER_DEVIN_API_KEY=cog_... OWNER_DEVIN_ORG_ID=org-... scripts/rollout.sh StationedAI [repo ...]
# Per repo: sets the owner secret/variable, copies any DEVIN_KEY_* env vars in as secrets,
# merges $DEVIN_USER_ORGS (JSON) into the repo variable, and opens one PR adding templates/.
# Re-runnable: skips files a repo already has and PRs that are already open.
set -euo pipefail
owner=${1:?usage: rollout.sh <github-org> [repo ...]}; shift
: "${OWNER_DEVIN_API_KEY:?}" "${OWNER_DEVIN_ORG_ID:?}"
here=$(cd "$(dirname "$0")/.." && pwd)
branch=devin-bridge/setup
repos=("$@")
[ ${#repos[@]} -gt 0 ] || mapfile -t repos < <(gh repo list "$owner" --limit 1000 --no-archived --json name -q '.[].name')

merge_user_orgs() {  # $1 = repo; merges $DEVIN_USER_ORGS into the repo variable
  [ -n "${DEVIN_USER_ORGS:-}" ] || return 0
  cur=$(gh variable get DEVIN_USER_ORGS -R "$1" 2>/dev/null || echo '{}')
  gh variable set DEVIN_USER_ORGS -R "$1" -b "$(jq -cn --argjson a "$cur" --argjson b "$DEVIN_USER_ORGS" '$a + $b')"
}

for name in "${repos[@]}"; do
  repo="$owner/$name"
  echo "== $repo"
  printf '%s' "$OWNER_DEVIN_API_KEY" | gh secret set OWNER_DEVIN_API_KEY -R "$repo"
  gh variable set OWNER_DEVIN_ORG_ID -R "$repo" -b "$OWNER_DEVIN_ORG_ID"
  for v in $(compgen -e | grep '^DEVIN_KEY_' || true); do
    printf '%s' "${!v}" | gh secret set "$v" -R "$repo"
  done
  merge_user_orgs "$repo"

  base=$(gh api "repos/$repo" -q .default_branch)
  if gh api "repos/$repo/contents/.github/workflows/devin-bridge.yml?ref=$base" >/dev/null 2>&1; then
    echo "   bridge already on $base"; continue
  fi
  if [ -n "$(gh pr list -R "$repo" --head "$branch" --state open --json number -q '.[].number')" ]; then
    echo "   setup PR already open"; continue
  fi
  sha=$(gh api "repos/$repo/git/ref/heads/$base" -q .object.sha 2>/dev/null) || { echo "   empty repo, skipped"; continue; }
  gh api "repos/$repo/git/refs" -f ref="refs/heads/$branch" -f sha="$sha" >/dev/null 2>&1 ||
    gh api -X PATCH "repos/$repo/git/refs/heads/$branch" -f sha="$sha" -F force=true >/dev/null
  (cd "$here/templates" && find . -type f | sed 's|^\./||') | while read -r path; do
    if [ "$path" = .devin/config.json ] && existing=$(gh api "repos/$repo/contents/$path?ref=$base" -q .content 2>/dev/null); then
      content=$(printf '%s' "$existing" | base64 -d | jq --slurpfile t "$here/templates/$path" \
        '.requiredPlugins = ((.requiredPlugins // []) + $t[0].requiredPlugins | unique)')
      fsha=$(gh api "repos/$repo/contents/$path?ref=$base" -q .sha)
    elif gh api "repos/$repo/contents/$path?ref=$base" >/dev/null 2>&1; then
      continue
    else
      content=$(cat "$here/templates/$path"); fsha=
    fi
    gh api -X PUT "repos/$repo/contents/$path" -f message="Add devin-bridge: $path" -f branch="$branch" \
      -f content="$(printf '%s\n' "$content" | base64 -w0)" ${fsha:+-f sha="$fsha"} >/dev/null
  done
  body="Adds the [devin-bridge](https://github.com/StationedAI/devin-bridge) workflows and requires the devin-bridge Devin plugin in \`.devin/config.json\`.

- \`devin-bridge\`: forwards PR reviews and comments to the Devin session linked to the PR; \`/bridge review\` runs Devin Review in the owner org; \`/bridge <prompt>\` starts or messages a session in the commenter's own Devin org.
- \`devin-feedback\`: red while any review thread is unresolved.

Secrets and variables are already set on this repo. Comment commands start working once this is merged."
  gh api "repos/$repo/pulls" -f title="Enable devin-bridge" -f head="$branch" -f base="$base" -f body="$body" -q .html_url
done
