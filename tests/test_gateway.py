"""Offline test of the CLI-gateway backend.

Stands up a fake OpenAI-compatible `/v1/chat/completions` server (like
`cli_gateway.py`, but returning canned text), points `LLM_BASE_URL` at it, and
runs the REAL `llm.*` functions and the full graph through it -- no API key, no
`anthropic` SDK, no `claude` CLI. This proves the gateway path (OpenAI request
shape + text yes/no parsing) works end to end.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from adaptive_rag import build_graph, llm
from test_graph import FakeIndex, make_chunk


class FakeGatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length).decode("utf-8"))
        prompt = req["messages"][0]["content"]
        # Grading prompts end with the yes/no instruction -> answer "yes".
        # Everything else is a generation/rewrite -> return canned text.
        if "yes or no" in prompt.lower():
            content = "yes"
        elif "rewrite it into a single" in prompt.lower():
            content = "rewritten query"
        else:
            content = "an answer from the gateway [doc.md]"
        body = json.dumps(
            {
                "id": "cli-gateway",
                "object": "chat.completion",
                "model": req.get("model", "sonnet"),
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}
                ],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def gateway(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeGatewayHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    monkeypatch.setenv("LLM_BASE_URL", base)
    monkeypatch.delenv("ADAPTIVE_RAG_LLM_BASE_URL", raising=False)
    yield base
    httpd.shutdown()


def test_gateway_is_detected(gateway):
    assert llm.using_gateway() is True
    assert "게이트웨이" in llm.backend_description()


def test_parse_bool_lenient():
    assert llm._parse_bool("Yes.") is True
    assert llm._parse_bool("No, it is not relevant.") is False
    assert llm._parse_bool("The answer is: yes, definitely") is True
    assert llm._parse_bool("(unparseable gibberish)") is True  # defaults accept


def test_llm_functions_hit_gateway(gateway):
    chunk = make_chunk("some text")
    assert llm.grade_document("q", chunk) is True
    assert llm.rewrite_query("q") == "rewritten query"
    assert "gateway" in llm.generate_answer("q", [chunk])
    assert llm.answer_is_grounded([chunk], "gen") is True
    assert llm.answer_addresses_question("q", "gen") is True


def test_full_graph_runs_through_gateway(gateway):
    """The whole graph runs against the gateway with no API key or stubs."""
    index = FakeIndex({}, default=[make_chunk("relevant text")])
    graph = build_graph(index)
    result = graph.invoke({"question": "무엇을 확인하나요?"})
    assert "gateway" in result["generation"]
    assert result["documents"]  # docs graded relevant via the gateway "yes"
