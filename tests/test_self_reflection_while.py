"""Offline test of the plain-`while` self-reflection version (no LangGraph).

Same exit conditions as the LangGraph version: GOOD stops immediately; an
always-REVISE verdict is bounded by MAX_DRAFTS.
"""

from __future__ import annotations

from adaptive_rag import llm
from examples.self_reflection_while import MAX_DRAFTS, reflect


def test_stops_on_good(monkeypatch):
    # "엄격한" appears only in the critique prompt (not the generate/improve one).
    def fake(prompt, model=None, max_tokens=None):
        return "VERDICT: GOOD" if "엄격한" in prompt else "the draft"

    monkeypatch.setattr(llm, "complete", fake)
    assert reflect("t") == "the draft"  # first draft accepted


def test_bounded_when_always_revise(monkeypatch):
    seen = {"generates": 0}

    def fake(prompt, model=None, max_tokens=None):
        if "엄격한" in prompt:  # critique prompt
            return "VERDICT: REVISE"  # never satisfied
        seen["generates"] += 1
        return f"draft {seen['generates']}"

    monkeypatch.setattr(llm, "complete", fake)
    reflect("t")
    assert seen["generates"] == MAX_DRAFTS  # bounded -> always terminates
