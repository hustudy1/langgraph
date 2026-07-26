"""Offline test of the direct `claude` CLI backend.

Forces the claude-cli backend, fakes the `claude` binary lookup, and stubs
`subprocess.run` so the real CLI is never spawned. Verifies that the graph runs
through the CLI backend and that the per-request model is passed to
`claude --model <model>` -- i.e. selecting a model takes effect per call.
"""

from __future__ import annotations

import types

import pytest

from adaptive_rag import build_graph, llm
from test_graph import FakeIndex, make_chunk


@pytest.fixture
def claude_cli(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_RAG_BACKEND", "claude-cli")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ADAPTIVE_RAG_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(llm, "_find_claude", lambda: "/fake/claude")

    cmds: list[list[str]] = []

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        cmds.append(cmd)
        prompt = input or ""
        if "yes or no" in prompt.lower():
            out = "yes"
        elif "rewrite it into a single" in prompt.lower():
            out = "rewritten"
        else:
            out = "cli answer [doc.md]"
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    return cmds


def _model_of(cmd: list[str]) -> str:
    return cmd[cmd.index("--model") + 1]


def test_backend_detected(claude_cli):
    assert llm.active_backend() == "claude-cli"
    assert "claude CLI" in llm.backend_description()


def test_per_request_model_is_passed_to_cli(claude_cli):
    chunk = make_chunk("t")
    assert llm.grade_document("q", chunk, "opus") is True
    assert _model_of(claude_cli[-1]) == "opus"  # grading used --model opus

    assert "cli answer" in llm.generate_answer("q", [chunk], "haiku")
    assert _model_of(claude_cli[-1]) == "haiku"  # generation used --model haiku


def test_full_graph_through_cli_with_chosen_model(claude_cli):
    index = FakeIndex({}, default=[make_chunk("relevant")])
    graph = build_graph(index)
    result = graph.invoke({"question": "q", "model": "sonnet", "k": 1})

    assert "cli answer" in result["generation"]
    # Every CLI call in the whole run used the model chosen at invoke time.
    assert claude_cli, "no CLI calls were made"
    assert all(_model_of(cmd) == "sonnet" for cmd in claude_cli)


def test_missing_cli_raises_clear_error(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_RAG_BACKEND", "claude-cli")
    monkeypatch.setattr(llm, "_find_claude", lambda: None)
    with pytest.raises(RuntimeError, match="claude CLI"):
        llm.generate_answer("q", [make_chunk("t")], "sonnet")
