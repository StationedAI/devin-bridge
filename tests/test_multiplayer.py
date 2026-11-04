"""Shared PR rooms: real handlers, mocked GitHub and Devin HTTP boundary."""
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

REPO = "StationedAI/example"
TAG = f"pr:{REPO}#7"
BOT = {"login": "github-actions[bot]", "type": "Bot"}


@pytest.fixture
def room(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GH_TOKEN", "github-test")
    for name in list(os.environ):
        if name.startswith("DEVIN_KEY_") or name in {"OWNER_DEVIN_API_KEY", "DEVIN_ORG_KEYS"}:
            monkeypatch.delenv(name)
    monkeypatch.delenv("OWNER_DEVIN_ORG_ID", raising=False)
    monkeypatch.setenv("DEVIN_USER_ORGS", json.dumps({"alice": "org-a", "bob": "org-b"}))
    monkeypatch.setenv("DEVIN_SECRETS", json.dumps({"DEVIN_KEY_ORG_A": "key-a", "DEVIN_KEY_ORG_B": "key-b",
                                                  "DEVIN_ORG_KEYS": json.dumps({"org-c": "key-c"}),
                                                  "DEVIN_KEY_ORG_D": "key-d"}))
    spec = importlib.util.spec_from_file_location("multiplayer_bridge", Path(__file__).parents[1] / "bridge/devin_bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.time, "time", lambda: 1_800_000_000)
    pr = {"number": 7, "html_url": f"https://github.com/{REPO}/pull/7", "head": {"ref": "feat"},
          "body": "<!-- devin-session: org-a/devin-old -->"}
    calls, comments = [], []
    sessions = {org: [] for org in ("org-a", "org-b", "org-c", "org-d")}
    messages, permission = {}, {"value": "read"}

    def http(url, token, body=None, method=None):
        method = method or ("POST" if body is not None else "GET")
        calls.append((method, url, token, body))
        path, query = urlsplit(url).path, parse_qs(urlsplit(url).query)
        if path == "/v3/self":
            assert token == "key-d"
            return {"org_id": "org-d"}
        if path.startswith("/v3/organizations/"):
            org = path.split("/")[3]
            assert token == "key-" + org[-1]
            if path.endswith("/sessions"):
                if body is not None:
                    result = {"session_id": "devin-created", "url": "https://app.devin.ai/sessions/created",
                              "updated_at": 500, "status": "new", **body}
                    sessions[org].append(result)
                    return result
                assert query["tags"] == [TAG]
                index = int(query.get("after", [0])[0])
                items = sessions[org][index:index + 1]
                more = index + 1 < len(sessions[org])
                return {"items": items, "has_next_page": more, "end_cursor": str(index + 1) if more else None}
            sid = path.split("/")[5]
            if path.endswith("/messages"):
                if body is not None:
                    return {}
                index = int(query.get("after", [0])[0])
                items = messages.get(sid, [])
                more = index + 10 < len(items)
                return {"items": items[index:index + 10], "has_next_page": more, "end_cursor": str(index + 10) if more else None}
            return next(s for s in sessions[org] if s["session_id"] == sid)
        assert token == "github-test"
        if path.endswith("/permission"):
            return {"permission": permission["value"]}
        if path.endswith("/pulls/7"):
            assert method == "GET", "handoff must not change the feedback target"
            return pr
        if path.endswith("/pulls"):
            return [pr] if query.get("page") == ["1"] else []
        if "/issues/comments/" in path:
            comment = next(c for c in comments if c["id"] == int(path.rsplit("/", 1)[1]))
            assert method == "PATCH"
            comment["body"] = body["body"]
            return comment
        if path.endswith("/comments"):
            if method == "POST":
                comment = {"id": max([c["id"] for c in comments] + [0]) + 1, "user": BOT, "body": body["body"]}
                comments.append(comment)
                return comment
            page = int(query["page"][0])
            return comments[(page - 1) * 100:page * 100]
        raise AssertionError(f"Unexpected HTTP route: {method} {path}")

    monkeypatch.setattr(mod, "call", http)

    def run(text=None, login="alice", association="NONE", event="issue_comment"):
        payload = {"comment": {"body": text, "user": {"login": login}, "author_association": association},
                   "issue": {"number": 7}, "pull_request": pr}
        path = tmp_path / "event.json"
        path.write_text(json.dumps(payload))
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        mod.main()

    return mod, pr, sessions, messages, comments, calls, run, permission


def linked(sid, updated, **extra):
    return {"session_id": sid, "updated_at": updated, "title": sid, "status": "running", "tags": [TAG], **extra}


def creates(calls):
    return [c for c in calls if c[0] == "POST" and c[1].endswith("/sessions")]


def test_lookup_all_keys_paginated_exact_tags_and_deterministic_order(room):
    mod, pr, sessions, _, _, calls, *_ = room
    sessions["org-a"] = [linked("devin-old", 10), linked("devin-spoof", 100, tags=[TAG + "0"]), linked("devin-second", 20)]
    sessions["org-b"] = [linked("devin-other", 20)]
    sessions["org-c"] = [linked("devin-legacy", 5)]
    sessions["org-d"] = [linked("devin-unmapped", 30)]
    assert [(org, s["session_id"]) for org, _, s in mod.linked_sessions(pr)] == [
        ("org-d", "devin-unmapped"), ("org-a", "devin-second"), ("org-b", "devin-other"),
        ("org-a", "devin-old"), ("org-c", "devin-legacy")]
    assert any("after=2" in c[1] for c in calls)


def test_handoff_waits_for_target_and_accepts_latest_context(room):
    mod, pr, sessions, messages, comments, calls, run, _ = room
    sessions["org-a"] = [linked("devin-old", 1)]
    sessions["org-c"] = [linked("devin-latest", 90, title="Newest work", status="suspended", structured_output={"summary": "Ready to finish"})]
    messages["devin-latest"] = [{"source": "user" if i % 2 else "devin", "message": f"message-{i}", "created_at": i} for i in range(25)]
    run("/bridge handoff @bob")
    handoff, state = mod.handoff_state(pr)
    assert state == {"from": "alice", "to": "bob", "at": 1_800_000_000, "state": "pending"}
    assert "@bob, @alice wants to hand" in handoff["body"]
    assert not creates(calls)
    run("/bridge accept", login="bob")
    create, = creates(calls)
    assert create[1] == f"{mod.DEVIN_API}/org-b/sessions" and create[2] == "key-b"
    request = create[3]
    assert request["tags"] == ["devin-bridge", TAG, "bridge-user:bob", f"bridge-handoff:{handoff['id']}"]
    for text in [pr["html_url"], "@alice", "@bob", "Newest work", "suspended", '"summary": "Ready to finish"', "message-5", "message-24", "Continue the work"]:
        assert text in request["prompt"]
    assert "message-4\n" not in request["prompt"]
    assert "accepted" in handoff["body"] and "https://app.devin.ai/sessions/created" in handoff["body"]
    run("/bridge accept", login="bob")
    assert len(creates(calls)) == 1
    assert pr["body"] == "<!-- devin-session: org-a/devin-old -->"


@pytest.mark.parametrize("login,command", [("alice", "accept"), ("alice", "decline")])
def test_only_recipient_can_resolve(room, login, command):
    mod, pr, _, _, _, calls, run, _ = room
    run("/bridge handoff @bob")
    run(f"/bridge {command}", login=login)
    assert not creates(calls)
    assert mod.handoff_state(pr)[1]["state"] == "pending"


def test_decline_and_replacement(room, monkeypatch):
    mod, pr, _, _, comments, calls, run, _ = room
    run("/bridge handoff @bob")
    first, _ = mod.handoff_state(pr)
    monkeypatch.setenv("DEVIN_USER_ORGS", json.dumps({"alice": "org-a", "bob": "org-b", "carol": "org-c"}))
    run("/bridge handoff @carol")
    assert mod.handoff_state(pr)[0]["id"] == first["id"]
    assert mod.handoff_state(pr)[1]["to"] == "carol"
    run("/bridge accept", login="bob")
    assert not creates(calls)
    run("/bridge decline", login="carol")
    assert mod.handoff_state(pr)[1]["state"] == "declined"
    assert not creates(calls)
    assert len([c for c in comments if mod.HANDOFF_RE.search(c["body"])]) == 1


@pytest.mark.parametrize("age", [7 * 86400 + 1, -1])
def test_expired_or_future_handoff_cannot_start(room, monkeypatch, age):
    mod, pr, _, _, _, calls, run, _ = room
    run("/bridge handoff @bob")
    monkeypatch.setattr(mod.time, "time", lambda: 1_800_000_000 + age)
    run("/bridge accept", login="bob")
    assert not creates(calls)
    assert mod.handoff_state(pr)[1]["state"] == "pending"


def test_accept_exact_expiry_boundary_empty_context_and_case_insensitive_login(room, monkeypatch):
    mod, _, _, _, _, calls, run, _ = room
    run("/bridge handoff @BoB")
    monkeypatch.setattr(mod.time, "time", lambda: 1_800_000_000 + 7 * 86400)
    run("/bridge accept", login="bob")
    assert "No linked session yet." in creates(calls)[0][3]["prompt"]


def test_unmapped_target_and_unauthorized_caller_create_no_pending_or_session(room):
    mod, pr, _, _, comments, calls, run, permission = room
    run("/bridge handoff @missing")
    assert "@missing has no Devin org/key configured" in comments[-1]["body"]
    run("/bridge handoff @bob", login="outsider")
    assert "only mapped teammates or users with write permission" in comments[-1]["body"]
    assert not creates(calls) and mod.handoff_state(pr) == (None, None)
    permission["value"] = "write"
    run("/bridge handoff @bob", login="outsider")
    assert mod.handoff_state(pr)[1]["from"] == "outsider"
    assert not creates(calls)


def test_spoofed_comment_and_terminal_handoff_are_not_pending(room):
    mod, pr, _, _, comments, calls, run, _ = room
    state = {"from": "alice", "to": "bob", "at": 1_800_000_000, "state": "pending"}
    comments.append({"id": 1, "user": {"login": "evil", "type": "User"}, "body": mod.handoff_text("fake", state)})
    run("/bridge accept", login="bob")
    assert not creates(calls)
    assert mod.handoff_state(pr) == (None, None)


def test_digest_create_edit_all_orgs_summary_fallback_and_no_forwarding_change(room):
    mod, pr, sessions, messages, comments, calls, run, _ = room
    sessions["org-a"] = [linked("devin-old", 1, tags=[TAG, "bridge-user:alice"], structured_output={"summary": "Completed checks"})]
    sessions["org-b"] = [linked("devin-b", 2, user_id="user-b")]
    messages["devin-b"] = [{"source": "devin", "message": "Last reply " + mod.DIGEST, "created_at": 0}] + [{"source": "user", "message": "followup", "created_at": i} for i in range(25)]
    mod.refresh_digest(pr)
    digest, = [c for c in comments if mod.DIGEST in c["body"]]
    assert "org-a" in digest["body"] and "org-b" in digest["body"]
    assert "user-b" in digest["body"] and "alice" in digest["body"]
    assert 'Completed checks' in digest["body"] and "Last reply" in digest["body"]
    assert "https://app.devin.ai/sessions/b" in digest["body"]
    assert digest["body"].count(mod.DIGEST) == 1
    sessions["org-b"][0]["status"] = "exit"
    mod.refresh_digest(pr)
    assert len(comments) == 1 and "`exit`" in digest["body"]
    run("please fix this", association="MEMBER")
    sent = [c for c in calls if c[0] == "POST" and c[1].endswith("/messages")]
    assert len(sent) == 1 and sent[0][1].endswith("/org-a/sessions/devin-old/messages")
    assert not creates(calls)


def test_digest_empty_and_spoofed_marker(room):
    mod, pr, sessions, _, comments, _, _, _ = room
    mod.refresh_digest(pr)
    assert comments == []
    comments.append({"id": 1, "user": {"login": "evil", "type": "User"}, "body": mod.DIGEST})
    sessions["org-a"] = [linked("devin-a", 1)]
    mod.refresh_digest(pr)
    assert len(comments) == 2 and comments[0]["body"] == mod.DIGEST
    assert "No Devin message yet." in comments[1]["body"]
    sessions["org-a"] = []
    mod.refresh_digest(pr)
    assert len(comments) == 2 and "No linked sessions found." in comments[1]["body"]


def test_schedule_refreshes_open_prs_without_reviewing_or_spawning(room):
    _, _, sessions, _, comments, calls, run, _ = room
    sessions["org-a"] = [linked("devin-a", 1, structured_output={"summary": "Scheduled update"})]
    run(event="schedule")
    assert "Scheduled update" in comments[0]["body"]
    assert not creates(calls) and not any("/pr-reviews" in c[1] for c in calls)


def test_context_size_bounded(room):
    _, _, sessions, messages, _, calls, run, _ = room
    sessions["org-a"] = [linked("devin-old", 1, title="t" * 100000, structured_output={"summary": "x" * 100000})]
    messages["devin-old"] = [{"source": "devin", "message": "m" * 100000, "created_at": i} for i in range(21)]
    run("/bridge handoff @bob")
    run("/bridge accept", login="bob")
    assert len(creates(calls)[0][3]["prompt"]) < 25000


def test_accept_failure_leaves_pending_retryable(room, monkeypatch):
    mod, pr, _, _, _, calls, run, _ = room
    run("/bridge handoff @bob")
    http = mod.call

    def failed(url, token, body=None, method=None):
        if url.endswith("/org-b/sessions") and body is not None:
            raise SystemExit("upstream failed")
        return http(url, token, body, method)

    monkeypatch.setattr(mod, "call", failed)
    with pytest.raises(SystemExit, match="upstream failed"):
        run("/bridge accept", login="bob")
    assert mod.handoff_state(pr)[1]["state"] == "pending" and not creates(calls)
    monkeypatch.setattr(mod, "call", http)
    run("/bridge accept", login="bob")
    assert len(creates(calls)) == 1 and mod.handoff_state(pr)[1]["state"] == "accepted"


def test_retry_after_session_created_reuses_receipt(room):
    mod, pr, sessions, _, _, calls, run, _ = room
    run("/bridge handoff @bob")
    comment, _ = mod.handoff_state(pr)
    sessions["org-b"] = [linked("devin-resume", 1, tags=[TAG, f"bridge-handoff:{comment['id']}"], url="https://app.devin.ai/sessions/resume")]
    run("/bridge accept", login="bob")
    assert not creates(calls) and "sessions/resume" in comment["body"]


def test_pagination_rejects_repeating_cursor(room, monkeypatch):
    mod, pr, *_ = room
    http = mod.call

    def repeated(url, token, body=None, method=None):
        if "/sessions?" in url:
            return {"items": [], "has_next_page": True, "end_cursor": "stuck"}
        return http(url, token, body, method)

    monkeypatch.setattr(mod, "call", repeated)
    with pytest.raises(ValueError, match="Invalid Devin pagination"):
        mod.linked_sessions(pr)
