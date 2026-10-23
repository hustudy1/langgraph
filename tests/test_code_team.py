"""Offline test of the multi-agent coding team (real pytest runner, stubbed LLMs).

Stubs `llm.complete` to play the tester / coder / reviewer (keyed on unique words
in each prompt), but the `run_tests` node runs pytest for real in a temp dir — so
these exercise the true generate → run → (fix loop) → review pipeline.
"""

from __future__ import annotations

from adaptive_rag import llm
from examples.code_team import _extract_code, build_code_team_graph


def test_extract_code_strips_fences():
    assert _extract_code("```python\nx = 1\n```") == "x = 1"
    assert _extract_code("no fence here") == "no fence here"


def test_team_reaches_passing_solution(monkeypatch):
    def fake(prompt, model=None, max_tokens=None):
        if "테스터" in prompt:  # test writer
            return "```python\nfrom solution import add\n\ndef test_add():\n    assert add(2, 3) == 5\n    assert add(-1, 1) == 0\n```"
        if "리뷰어" in prompt:  # reviewer
            return "간결하고 정확합니다.\nVERDICT: APPROVE"
        return "```python\ndef add(a, b):\n    return a + b\n```"  # coder

    monkeypatch.setattr(llm, "complete", fake)
    result = build_code_team_graph().invoke({"task": "두 수를 더하는 add"})
    assert result["passed"] is True  # real pytest passed
    assert result["verdict"] == "APPROVE"
    assert "def add" in result["code"]


def test_team_fixes_after_failing_tests(monkeypatch):
    coder_calls = {"n": 0}

    def fake(prompt, model=None, max_tokens=None):
        if "테스터" in prompt:
            return "```python\nfrom solution import mul\n\ndef test_mul():\n    assert mul(2, 3) == 6\n```"
        if "리뷰어" in prompt:
            return "VERDICT: APPROVE"
        coder_calls["n"] += 1
        if coder_calls["n"] == 1:
            return "```python\ndef mul(a, b):\n    return a + b  # 버그: 곱셈이 아님\n```"
        return "```python\ndef mul(a, b):\n    return a * b\n```"

    monkeypatch.setattr(llm, "complete", fake)
    result = build_code_team_graph().invoke({"task": "곱셈 mul"})
    assert result["passed"] is True  # eventually passed after a fix
    assert coder_calls["n"] >= 2  # 실패 → 실행기가 잡아냄 → 수정 루프
