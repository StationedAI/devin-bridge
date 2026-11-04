"""Review summaries carry metadata, never full comment bodies."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_review_summary(monkeypatch):
    spec = importlib.util.spec_from_file_location("review_bridge", Path(__file__).resolve().parents[1] / "bridge/devin_bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("WORKER_REVIEW_WEBHOOK", "https://worker.example/devin-review?token=test")
    pr = {"number": 7, "title": "Fix", "html_url": "https://github.com/a/b/pull/7", "user": {"login": "sam"}, "head": {"sha": "abc"}}
    rv = {"id": 123, "user": {"login": "devin-ai-integration[bot]"}, "commit_id": "def"}
    comment = {"body": '<!-- devin-review-comment {"file_path":"x.py","start_line":2,"kind":"bug"} -->\n🟡 **A bug**\nPRIVATE BODY'}
    with patch.object(mod, "gh", return_value=[comment]), patch.object(mod.urllib.request, "urlopen", return_value=MagicMock()) as http:
        mod.notify_review(pr, rv)
        payload = json.loads(http.call_args.args[0].data)
        assert payload["counts"] == {"severity": {"medium": 1}, "kind": {"bug": 1}}
        assert payload["findings"][0]["location"] == "x.py:2"
        assert "PRIVATE BODY" not in str(payload)
        assert payload["sha"] == "def"
    with patch.object(mod, "gh", return_value=[]), patch.object(mod.urllib.request, "urlopen", side_effect=OSError):
        mod.notify_review(pr, rv)
    monkeypatch.delenv("WORKER_REVIEW_WEBHOOK")
    with patch.object(mod, "gh") as gh:
        mod.notify_review(pr, rv)
        gh.assert_not_called()
