"""End-to-end offline test of the unified menu dashboard (both modes + menu bar).

Starts the real server with both graphs (loop + RAG from a FakeIndex), stubs all
LLM calls, and checks: the menu bar renders on each mode's page, and both stream
endpoints (task= → loop, question= → RAG) produce a done event.
"""

from __future__ import annotations

import threading
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import quote

import pytest

from adaptive_rag import build_graph, llm
from examples.menu_web import Handler
from examples.self_reflection import build_reflection_graph
from test_graph import FakeIndex, make_chunk


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_RAG_BACKEND", "api")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    # RAG-path stubs
    monkeypatch.setattr(llm, "grade_document", lambda q, c, m=None: True)
    monkeypatch.setattr(llm, "rewrite_query", lambda q, m=None: f"rw:{q}")
    monkeypatch.setattr(llm, "generate_answer", lambda q, ch, m=None: "an answer")
    monkeypatch.setattr(llm, "answer_is_grounded", lambda ch, g, m=None: True)
    monkeypatch.setattr(llm, "answer_addresses_question", lambda q, g, m=None: True)
    # loop-path stub: REVISE once then GOOD
    calls = {"c": 0}

    def fake_complete(prompt, model=None, max_tokens=None):
        if "엄격한" in prompt:
            calls["c"] += 1
            return "VERDICT: " + ("GOOD" if calls["c"] >= 2 else "REVISE")
        return "초안"

    monkeypatch.setattr(llm, "complete", fake_complete)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.loop_graph = build_reflection_graph()  # type: ignore[attr-defined]
    httpd.rag_graph = build_graph(FakeIndex({}, default=[make_chunk("relevant")]))  # type: ignore[attr-defined]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read().decode("utf-8")


def test_rag_page_has_menu_and_graph(server):
    html = _get(server + "/?mode=rag")
    assert "menubar" in html
    assert "검증 루프" in html and "RAG + 검증" in html
    assert "<svg" in html


def test_loop_page_has_menu_and_graph(server):
    html = _get(server + "/?mode=loop")
    assert "menubar" in html
    assert "<svg" in html


def test_rag_stream(server):
    body = _get(server + "/stream?question=hi&k=2")  # no task= → RAG mode
    assert "event: done" in body
    assert "an answer" in body


def test_loop_stream(server):
    body = _get(server + "/stream?task=" + quote("설명해줘"))  # task= → loop mode
    assert "event: done" in body
    assert "REVISE" in body and "GOOD" in body
