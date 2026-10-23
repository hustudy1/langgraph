"""The root `dashboard.py` launcher forwards defaults to the menu dashboard.

`dashboard.py` now launches `examples/menu_web.py` (the menu dashboard). We stub
`menu_web.main` and assert the argv the launcher builds.
"""

from __future__ import annotations

import dashboard
import examples.menu_web as menu_web


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(menu_web, "main", fake_main)
    return seen


def _val(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_defaults_and_model_forwarded(monkeypatch):
    seen = _capture(monkeypatch)
    rc = dashboard.main(["--model", "opus", "--port", "9001"])
    assert rc == 0
    argv = seen["argv"]
    assert _val(argv, "--source") == "llm_wiki"  # default source → RAG mode available
    assert _val(argv, "--host") == "0.0.0.0"  # default: other devices allowed
    assert _val(argv, "--backend") == "claude-cli"
    assert _val(argv, "--model") == "opus"
    assert _val(argv, "--port") == "9001"


def test_local_flag_and_model_omitted(monkeypatch):
    seen = _capture(monkeypatch)
    dashboard.main(["--local"])
    argv = seen["argv"]
    assert _val(argv, "--host") == "127.0.0.1"  # --local binds locally
    assert "--model" not in argv  # no --model → not forwarded


def test_loop_flag_omits_source(monkeypatch):
    seen = _capture(monkeypatch)
    dashboard.main(["--loop", "--port", "8022"])
    argv = seen["argv"]
    assert "--source" not in argv  # loop-only → no RAG source (RAG menu disabled)
    assert _val(argv, "--port") == "8022"
