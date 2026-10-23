"""End-to-end offline test of the self-reflection web dashboard (HTTP + SSE).

Starts the real ThreadingHTTPServer with a graph built from the example, stubs
`llm.complete` (no real Claude call), and asserts the page + stream. The stub
returns REVISE once then GOOD, so the loop runs generate→critique twice.
"""

from __future__ import annotations

import threading
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import quote

import pytest

from adaptive_rag import llm
from examples.self_reflection import build_reflection_graph
from examples.self_reflection_web import Handler


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_RAG_BACKEND", "api")  # deterministic model list
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    calls = {"critiques": 0}

    def fake_complete(prompt, model=None, max_tokens=None):
        if "엄격한" in prompt:  # critique node prompt
            calls["critiques"] += 1
            verdict = "GOOD" if calls["critiques"] >= 2 else "REVISE"
            return f"검토 결과.\nVERDICT: {verdict}"
        return "초안 텍스트"  # generate node

    monkeypatch.setattr(llm, "complete", fake_complete)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.graph = build_reflection_graph()  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read().decode("utf-8")


def test_page_served_with_korean_labels(server):
    html = _get(server + "/")
    assert "자기검토 루프" in html
    assert "<svg" in html
    assert "초안 생성" in html  # 한글 노드 라벨
    assert "자기 비평" in html
    assert "/stream?task=" in html
    assert 'id="critic-model"' in html  # 검토자 모델 드롭다운(팀 구성)
    assert "검토자 모델" in html


def test_stream_runs_the_loop(server):
    body = _get(server + "/stream?task=" + quote("설명해줘"))
    assert "event: node" in body
    assert "event: done" in body
    assert "generate" in body and "critique" in body
    assert "REVISE" in body  # 첫 비평 → 루프
    assert "GOOD" in body  # 두 번째 비평 → 종료


def test_empty_task_streams_error(server):
    body = _get(server + "/stream?task=")
    assert "event: error" in body
