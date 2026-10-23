"""End-to-end offline test of the web dashboard's HTTP + SSE path.

Starts the real ThreadingHTTPServer with a graph built from a FakeIndex and
stubbed LLM calls (no API key, no embedding download, no network), then hits
`/` and `/stream` over a real socket and asserts the SSE events.
"""

from __future__ import annotations

import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from adaptive_rag import build_graph, llm
from adaptive_rag.web import Handler
from test_graph import FakeIndex, make_chunk


@pytest.fixture
def server(monkeypatch):
    # Pin the API backend so the page renders the API model list deterministically
    # (the stubs below mean no real Anthropic call is ever made).
    monkeypatch.setenv("ADAPTIVE_RAG_BACKEND", "api")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setattr(llm, "grade_document", lambda q, c, m=None: True)
    monkeypatch.setattr(llm, "rewrite_query", lambda q, m=None: f"rewritten: {q}")
    monkeypatch.setattr(llm, "generate_answer", lambda q, chunks, m=None: "an answer")
    monkeypatch.setattr(llm, "answer_is_grounded", lambda chunks, gen, m=None: True)
    monkeypatch.setattr(llm, "answer_addresses_question", lambda q, gen, m=None: True)

    index = FakeIndex({}, default=[make_chunk("relevant text")])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.graph = build_graph(index)  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read().decode("utf-8")


def test_index_page_served(server):
    html = _get(server + "/")
    assert "Adaptive RAG" in html
    assert "/stream?question=" in html  # the SSE wiring is present
    assert 'id="model"' in html  # model dropdown present
    assert "claude-opus-5-5" in html  # a model option was injected
    assert "<svg" in html  # the live graph diagram is rendered


def test_stream_accepts_model_and_k(server):
    body = _get(server + "/stream?question=hi&model=claude-haiku-4-5&k=2")
    assert "event: done" in body
    assert "claude-haiku-4-5" in body  # chosen model echoed back in done event


def test_stream_emits_nodes_and_final_answer(server):
    body = _get(server + "/stream?question=what%20is%20X")
    # SSE frames: several `event: node` then one `event: done`.
    assert "event: node" in body
    assert "event: done" in body
    assert "retrieve" in body
    assert "grade_documents" in body
    assert "an answer" in body  # final generation is delivered


def test_empty_question_streams_error(server):
    body = _get(server + "/stream?question=")
    assert "event: error" in body


def test_start_model_preselected_via_env(monkeypatch):
    # --model sets ADAPTIVE_RAG_WEB_MODEL, which preselects the dropdown option.
    monkeypatch.setenv("ADAPTIVE_RAG_BACKEND", "api")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("ADAPTIVE_RAG_WEB_MODEL", "claude-haiku-4-5")
    from adaptive_rag.web import render_page

    html = render_page()
    assert '<option value="claude-haiku-4-5" selected>' in html
    assert '<option value="claude-opus-5-5" selected>' not in html  # default overridden


def test_invalid_start_model_falls_back(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_RAG_BACKEND", "api")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("ADAPTIVE_RAG_WEB_MODEL", "not-a-real-model")
    from adaptive_rag.web import render_page

    html = render_page()
    assert '<option value="claude-opus-5-5" selected>' in html  # falls back to default
