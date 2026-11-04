"""GitHub -> Devin bridge (stdlib only).

- `/bridge review` on a PR     -> Devin Review in the owner org (has the GitHub App)
- `/bridge <prompt>` on a PR   -> the PR's linked session, else a new session in
                                  the commenter's own Devin org
- review / comment on a PR     -> forwarded to the session linked in the PR body
                                  (through that session's org)
- workflow_dispatch review-all -> Devin Review on every open PR

`/devin` is left to the native Devin GitHub App, which answers every comment
starting with `/devin`.

Teammates each have their own Devin org. Each org's service-user key is the
secret DEVIN_KEY_<ORG_ID> (non-alphanumerics -> `_`, upper case), or an entry in
the legacy DEVIN_ORG_KEYS JSON secret. The caller passes `toJSON(secrets)` as
DEVIN_SECRETS. DEVIN_USER_ORGS (variable) maps GitHub login -> org id.

A PR is linked to a session by `<!-- devin-session: <org-or-personal-id>/devin-... -->` in its body;
the session must also carry the tag `pr:<repo>#<n>` (set by the bridge), so an
edited PR body cannot redirect feedback. Only trusted authors are forwarded.
Comments carrying `<!-- devin-bridge -->` are ours and never forwarded.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

DEVIN_API = "https://api.devin.ai/v3/organizations"
GITHUB_API = "https://api.github.com"
MARKER = "<!-- devin-bridge -->"
SESSION_RE = re.compile(r"<!--\s*devin-session:\s*([\w-]+)/(devin-[\w-]+)\s*-->")
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
TRUSTED_BOTS = {"devin-ai-integration[bot]"}
COMMAND_RE = re.compile(r"(?i)^/bridge\b")
REPO = os.environ.get("GITHUB_REPOSITORY", "")


def call(url, token, body=None, method=None):
    method = method or ("POST" if body is not None else "GET")
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url} -> {e.code}: {e.read().decode()[:500]}")


def gh(path, body=None, method=None):
    return call(f"{GITHUB_API}{path}", os.environ["GH_TOKEN"], body, method)


def reply(pr, text):
    gh(f"/repos/{REPO}/issues/{pr}/comments", {"body": f"{text}\n\n{MARKER}"})


def secret(name):
    if os.environ.get(name):
        return os.environ[name]
    secrets = json.loads(os.environ.get("DEVIN_SECRETS") or "{}")
    return {k.upper(): v for k, v in secrets.items()}.get(name.upper())


def need(*names, required=False):
    missing = [n for n in names if not secret(n)]
    if missing:
        print(f"devin-bridge: {'failing' if required else 'skipping'}, not configured: {', '.join(missing)}")
        sys.exit(1 if required else 0)


def review(pr_url):
    need("OWNER_DEVIN_API_KEY", "OWNER_DEVIN_ORG_ID")
    org = os.environ["OWNER_DEVIN_ORG_ID"]
    return call(f"{DEVIN_API}/{org}/pr-reviews", secret("OWNER_DEVIN_API_KEY"), {"pr_url": pr_url})


def mapping(name):
    return json.loads(os.environ.get(name) or "{}")


def key_name(org):
    return "DEVIN_KEY_" + re.sub(r"\W", "_", org).upper()


def org_key(org):
    return secret(key_name(org)) or json.loads(secret("DEVIN_ORG_KEYS") or "{}").get(org)


def org_call(org, path, body=None):
    key = org_key(org)
    if not key:
        print(f"devin-bridge: no key for {org} (secret {key_name(org)})")
        return None
    return call(f"{DEVIN_API}/{org}{path}", key, body)


def pr_tag(number):
    return f"pr:{REPO}#{number}"


def trusted(association, login):
    return association in TRUSTED or login in TRUSTED_BOTS


def linked_session(pr):
    m = SESSION_RE.search(pr.get("body") or "")
    if not m:
        return None
    org, sid = m.groups()
    session = org_call(org, f"/sessions/{sid}")
    if session is None or pr_tag(pr["number"]) not in session.get("tags", []):
        print(f"ignoring {org}/{sid}: not tagged {pr_tag(pr['number'])}")
        return None
    return org, sid


def open_prs():
    page = 1
    while batch := gh(f"/repos/{REPO}/pulls?state=open&per_page=100&page={page}"):
        yield from batch
        page += 1


def start_session(pr, prompt, author):
    if SESSION_RE.search(pr.get("body") or "") and (linked := linked_session(pr)):
        org, sid = linked
        if mapping("DEVIN_USER_ORGS").get(author) != org:
            return reply(pr["number"], f"@{author}: this PR's Devin session belongs to another Devin org, "
                         "so only members of that org can give it /bridge instructions. Leave a review comment instead.")
        org_call(org, f"/sessions/{sid}/messages",
                 {"message": f"/bridge request on {pr['html_url']} from @{author}:\n\n{prompt}"})
        return reply(pr["number"], f"Sent to the Devin session already on this PR ({sid}).")
    org = mapping("DEVIN_USER_ORGS").get(author)
    if not org or not org_key(org):
        return reply(pr["number"], f"@{author} has no Devin org set up for this bridge "
                     "(DEVIN_USER_ORGS variable / DEVIN_KEY_<org> secret). See https://github.com/StationedAI/devin-bridge#add-a-teammate")
    prompt_text = (
        f"PR: {pr['html_url']} (repo {REPO}, branch {pr['head']['ref']}).\n"
        f"Request from @{author} via a /bridge comment:\n\n{prompt}\n\n"
        "Work on this PR's branch and push to it. Reply on the PR with `gh`, "
        f"and end every comment you post with `{MARKER}`."
    )
    s = org_call(org, "/sessions", {"prompt": prompt_text, "title": f"{REPO}#{pr['number']}: {prompt[:60]}",
                           "tags": ["devin-bridge", pr_tag(pr["number"])]})
    sid = s["session_id"] if s["session_id"].startswith("devin-") else f"devin-{s['session_id']}"
    body = (pr.get("body") or "").rstrip()
    body = SESSION_RE.sub("", body).rstrip() + f"\n\n<!-- devin-session: {org}/{sid} -->"
    gh(f"/repos/{REPO}/pulls/{pr['number']}", {"body": body}, "PATCH")
    reply(pr["number"], f"Devin is on it: {s['url']}")


def forward(pr, kind, author, association, text, extra=""):
    if not text.strip() or MARKER in text or not SESSION_RE.search(pr.get("body") or ""):
        return
    if not trusted(association, author):
        return print(f"not forwarding {kind} from untrusted @{author}")
    linked = linked_session(pr)
    if not linked:
        return
    org, sid = linked
    msg = f"New {kind} on {pr['html_url']} from @{author}{extra}:\n\n{text}"
    org_call(org, f"/sessions/{sid}/messages", {"message": msg})
    print(f"forwarded {kind} to {org}/{sid}")


def notify_review(pr, rv):
    url = secret("WORKER_REVIEW_WEBHOOK")
    if not url or rv["user"]["login"] != "devin-ai-integration[bot]":
        return
    findings = []
    page = 1
    while True:
        comments = gh(f"/repos/{REPO}/pulls/{pr['number']}/reviews/{rv['id']}/comments?per_page=100&page={page}")
        for comment in comments:
            body = comment.get("body") or ""
            match = re.search(r"<!-- devin-review-comment (\{.*?\})\s*-->", body)
            if not match:
                continue
            try:
                meta = json.loads(match[1])
            except ValueError:
                continue
            title = body[match.end():].strip().splitlines()[0] if body[match.end():].strip() else "Finding"
            severity = next((level for icon, level in (("🔴", "high"), ("🟡", "medium"), ("🟢", "low")) if icon in title), "unknown")
            findings.append({"title": title.replace("**", "")[:240],
                             "location": f"{meta.get('file_path', comment.get('path', ''))}:{meta.get('start_line') or comment.get('line') or ''}",
                             "kind": meta.get("kind", "unknown"), "severity": severity})
        if len(comments) < 100:
            break
        page += 1
    counts = {field: {} for field in ("severity", "kind")}
    for finding in findings:
        for field in counts:
            value = finding[field]
            counts[field][value] = counts[field].get(value, 0) + 1
    rank = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    payload = {"repo": REPO, "number": pr["number"], "title": pr["title"],
               "url": pr["html_url"], "author": pr["user"]["login"],
               "sha": rv.get("commit_id") or pr["head"]["sha"], "review_id": rv["id"],
               "counts": counts, "findings": sorted(findings, key=lambda f: rank[f['severity']])[:5]}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except (OSError, ValueError):
        print("devin-bridge: Worker review notification failed")


def main():
    event = os.environ.get("GITHUB_EVENT_NAME")
    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        ev = json.load(f)

    if event == "workflow_dispatch":
        need("OWNER_DEVIN_API_KEY", "OWNER_DEVIN_ORG_ID", required=True)
        for pr in open_prs():
            r = review(pr["html_url"])
            print(f"#{pr['number']}: {r.get('status')}")
        return

    if event == "issue_comment":
        c = ev["comment"]
        text, author = c["body"].strip(), c["user"]["login"]
        pr = gh(f"/repos/{REPO}/pulls/{ev['issue']['number']}")
        if COMMAND_RE.match(text):
            if c.get("author_association") not in TRUSTED:
                return print(f"ignoring /bridge from untrusted @{author}")
            cmd = text[len("/bridge"):].strip()
            if cmd.lower() == "review":
                r = review(pr["html_url"])
                return reply(pr["number"], f"Devin Review queued for `{r.get('commit_sha', '')[:7]}` ({r.get('status')}).")
            if not cmd:
                return reply(pr["number"], "Usage: `/bridge review` or `/bridge <what to do>`.")
            return start_session(pr, cmd, author)
        return forward(pr, "comment", author, c.get("author_association"), text)

    pr = ev["pull_request"]
    if event == "pull_request_review":
        rv = ev["review"]
        notify_review(pr, rv)
        return forward(pr, f"review ({rv['state'].lower()})", rv["user"]["login"],
                       rv.get("author_association"), rv.get("body") or "")
    if event == "pull_request_review_comment":
        c = ev["comment"]
        where = f" on `{c['path']}` line {c.get('line') or c.get('original_line')}"
        return forward(pr, "review comment", c["user"]["login"], c.get("author_association"), c["body"], where)


if __name__ == "__main__":
    main()
