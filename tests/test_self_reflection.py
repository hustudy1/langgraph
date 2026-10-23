"""Offline test of the RAG-free self-reflection example graph.

Stubs `llm.complete` (no real Claude call) and drives the generate<->critique
loop, checking both exit conditions: a GOOD verdict stops immediately, and an
always-REVISE verdict is bounded by MAX_DRAFTS.
"""

from __future__ import annotations

from adaptive_rag import llm
from examples.self_reflection import MAX_DRAFTS, build_reflection_graph


def _run(monkeypatch, verdicts: list[str]) -> dict:
    """Run the graph; critique returns verdicts in order (last one repeats)."""
    seen = {"critiques": 0}

    def fake_complete(prompt, model=None, max_tokens=None):
        # "엄격한" appears only in the critique prompt, not the generate/improve
        # one (the improve prompt embeds the previous critique text, so keying
        # on "VERDICT" would misclassify it).
        if "엄격한" in prompt:  # critique node
            i = min(seen["critiques"], len(verdicts) - 1)
            seen["critiques"] += 1
            return f"검토 결과입니다.\nVERDICT: {verdicts[i]}"
        return "a draft"  # generate node

    monkeypatch.setattr(llm, "complete", fake_complete)
    return build_reflection_graph().invoke({"task": "설명해줘"})


def test_stops_when_verdict_good(monkeypatch):
    result = _run(monkeypatch, ["GOOD"])
    assert result["verdict"] == "GOOD"
    assert result["drafts"] == 1  # first draft accepted, no revision loop


def test_loops_until_cap_when_always_revise(monkeypatch):
    result = _run(monkeypatch, ["REVISE"])  # never satisfied
    assert result["drafts"] == MAX_DRAFTS  # bounded by the retry budget -> terminates


def test_revises_then_accepts(monkeypatch):
    result = _run(monkeypatch, ["REVISE", "GOOD"])  # improve once, then accept
    assert result["verdict"] == "GOOD"
    assert result["drafts"] == 2  # one initial draft + one revision


def test_separate_models_for_writer_and_critic(monkeypatch):
    # A "team": generate uses the writer model, critique uses the critic model.
    used: list[tuple[str, str]] = []

    def fake_complete(prompt, model=None, max_tokens=None):
        is_critic = "엄격한" in prompt
        used.append(("critic" if is_critic else "writer", model))
        return "VERDICT: GOOD" if is_critic else "draft"

    monkeypatch.setattr(llm, "complete", fake_complete)
    build_reflection_graph().invoke(
        {"task": "t", "model": "writer-m", "critic_model": "critic-m"}
    )
    writers = [m for role, m in used if role == "writer"]
    critics = [m for role, m in used if role == "critic"]
    assert writers and all(m == "writer-m" for m in writers)  # generate → writer model
    assert critics and all(m == "critic-m" for m in critics)  # critique → critic model
