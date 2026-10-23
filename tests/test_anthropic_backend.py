"""Anthropic 백엔드(api)가 실제 anthropic SDK로 요청을 만들고 응답을 읽는지 확인한다.

네트워크나 API 키 없이 돈다. SDK는 진짜를 쓰고, HTTP 전송만 가짜 응답으로 바꾼다.
SDK 메이저 버전이 바뀌어 요청 형식(특히 구조화 출력 output_config)이 달라지면 이 테스트가 깨진다.
"""

import json

import pytest

anthropic = pytest.importorskip("anthropic")
# anthropic 2.x는 httpx2를, 그 전 버전은 httpx를 HTTP 클라이언트로 쓴다 (2026-10-22 확인)
try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover - anthropic < 2
    httpx = pytest.importorskip("httpx")

from adaptive_rag import llm  # noqa: E402


def _client_returning(text, seen):
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": seen[-1]["model"],
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    return anthropic.Anthropic(api_key="test-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_anthropic_complete_round_trip(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "_client", _client_returning("초안입니다", seen))
    assert llm._anthropic_complete("질문", "claude-opus-5-5", 50) == "초안입니다"
    assert seen[0]["model"] == "claude-opus-5-5"
    assert seen[0]["max_tokens"] == 50
    assert seen[0]["messages"][0]["content"] == "질문"


def test_anthropic_bool_sends_json_schema_and_parses(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "_client", _client_returning('{"relevant": true}', seen))
    assert llm._anthropic_bool("관련 있나?", "relevant", "claude-sonnet-5-5") is True
    fmt = seen[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["properties"] == {"relevant": {"type": "boolean"}}
    assert fmt["schema"]["required"] == ["relevant"]
