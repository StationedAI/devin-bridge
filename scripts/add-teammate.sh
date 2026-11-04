#!/usr/bin/env bash
# Add a teammate's Devin org to every non-archived repo of a GitHub org (or only the repos named).
#   scripts/add-teammate.sh StationedAI <github-login> [repo ...]
# Prompts for the teammate's Devin service-user key (cog_...), checks it with GET /v3/self, then
# sets the secret DEVIN_KEY_<ORG_ID> and maps <github-login> -> org id in the DEVIN_USER_ORGS
# variable. Pass "-" as the login to add only the key. Nobody's key is ever read back.
set -euo pipefail
owner=${1:?usage: add-teammate.sh <github-org> <github-login|-> [repo ...]}
login=${2:?usage: add-teammate.sh <github-org> <github-login|-> [repo ...]}; shift 2
key=${DEVIN_KEY:-}
[ -n "$key" ] || { read -rsp "Devin service-user key (cog_...): " key; echo; }
org=$(curl -fsS https://api.devin.ai/v3/self -H "Authorization: Bearer $key" | jq -r .org_id)
[ -n "$org" ] && [ "$org" != null ] || { echo "key rejected by /v3/self" >&2; exit 1; }
secret="DEVIN_KEY_$(printf '%s' "$org" | tr -c 'A-Za-z0-9_' '_' | tr '[:lower:]' '[:upper:]')"
echo "key belongs to $org -> secret $secret"
repos=("$@")
[ ${#repos[@]} -gt 0 ] || mapfile -t repos < <(gh repo list "$owner" --limit 1000 --no-archived --json name -q '.[].name')
for name in "${repos[@]}"; do
  repo="$owner/$name"
  printf '%s' "$key" | gh secret set "$secret" -R "$repo"
  if [ "$login" != - ]; then
    cur=$(gh variable get DEVIN_USER_ORGS -R "$repo" 2>/dev/null || echo '{}')
    gh variable set DEVIN_USER_ORGS -R "$repo" -b "$(jq -cn --argjson a "$cur" --arg l "$login" --arg o "$org" '$a + {($l): $o}')"
  fi
  echo "$repo: done"
done
