#!/usr/bin/env python3
"""메뉴가 있는 통합 대시보드 — 한 화면에서 모드를 골라 쓴다.

상단 메뉴로 두 모드를 오간다:
  - 🔁 검증 루프 (RAG 없음): 생성 → 자기 비평 → 재생성. 신빙성 있는 답을 루프로 다듬는다.
  - 🔍 RAG + 검증 루프: 문서 검색 → 근거 기반 생성 → 환각·질문적합 검증 → 루프.

`--source` 폴더를 주면 RAG 모드가 켜지고, 안 주면 검증 루프 모드만 쓴다. 기존 두
대시보드(`adaptive_rag.web`, `examples.self_reflection_web`)의 페이지와 그래프를 그대로
재사용하고, 상단에 모드 메뉴만 얹는다. 표준 라이브러리 HTTP + SSE 만 쓴다.

    uv run python examples/menu_web.py                 # 검증 루프만
    uv run python examples/menu_web.py --source docs   # + RAG 모드 활성화
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from adaptive_rag import build_graph, llm
from adaptive_rag import web as ragweb
from adaptive_rag.config import RETRIEVE_K
from examples import self_reflection_web as loopweb
from examples.self_reflection import build_reflection_graph

# 두 페이지에 얹을 모드 메뉴바. 페이지의 CSS 토큰(var(--...))을 그대로 쓴다.
MENU_CSS = """<style>
.menubar{position:sticky;top:0;z-index:20;display:flex;gap:6px;align-items:center;
  padding:9px 16px;background:var(--panel);border-bottom:1px solid var(--border);}
.menubar a{font-size:13px;font-weight:600;color:var(--muted);padding:7px 13px;border-radius:8px;text-decoration:none;}
.menubar a.active{color:var(--accent);background:var(--accent-soft);}
.menubar a:hover{color:var(--accent);} .menubar a.off{opacity:.4;pointer-events:none;}
.menubar .sp{flex:1;} .menubar .hint{font-size:11px;color:var(--faint);}
</style>"""


def _menubar(mode: str, rag_avail: bool) -> str:
    loop_cls = "active" if mode == "loop" else ""
    rag_cls = "active" if mode == "rag" else ("off" if not rag_avail else "")
    hint = (
        ""
        if rag_avail
        else '<span class="hint">RAG 모드는 <code>--source &lt;폴더&gt;</code> 를 주고 실행하면 켜집니다</span>'
    )
    return (
        '<div class="menubar">'
        f'<a class="{loop_cls}" href="/?mode=loop">🔁 검증 루프</a>'
        f'<a class="{rag_cls}" href="/?mode=rag">🔍 RAG + 검증</a>'
        f'<span class="sp"></span>{hint}</div>'
    )


def _inject_menu(html: str, mode: str, rag_avail: bool) -> str:
    """기존 페이지 HTML에 메뉴 CSS(head) + 메뉴바(body 최상단)를 얹는다."""
    return html.replace("</head>", MENU_CSS + "</head>", 1).replace(
        "<body>", "<body>" + _menubar(mode, rag_avail), 1
    )


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        return

    def _rag_avail(self) -> bool:
        return getattr(self.server, "rag_graph", None) is not None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/":
            rag_avail = self._rag_avail()
            want = (params.get("mode") or [""])[0]
            mode = want if want in ("rag", "loop") else ("rag" if rag_avail else "loop")
            if mode == "rag" and not rag_avail:
                mode = "loop"
            base = ragweb.render_page() if mode == "rag" else loopweb.render_page()
            self._send_html(_inject_menu(base, mode, rag_avail))
        elif parsed.path == "/stream":
            # 어떤 모드인지 파라미터로 구분: 검증 루프는 task=, RAG는 question=.
            if "task" in params:
                self._stream_loop(params)
            else:
                self._stream_rag(params)
        else:
            self.send_error(404)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, event: str, data: dict):
        chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        self.wfile.write(chunk.encode("utf-8"))
        self.wfile.flush()

    def _begin_stream(self):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    # --- RAG + 검증 루프 (adaptive_rag 그래프) --- reuses adaptive_rag.web helpers
    def _stream_rag(self, params):
        self._begin_stream()
        graph = getattr(self.server, "rag_graph", None)
        if graph is None:
            self._sse("error", {"message": "RAG 모드는 --source 폴더를 주고 서버를 실행해야 켜집니다."})
            return
        question = (params.get("question") or [""])[0].strip()
        allowed, default_model = ragweb._allowed_models()
        model = (params.get("model") or [default_model])[0]
        if model not in allowed:
            model = default_model
        try:
            k = int((params.get("k") or [str(RETRIEVE_K)])[0])
        except ValueError:
            k = RETRIEVE_K
        k = max(1, min(k, 20))
        if not question:
            self._sse("error", {"message": "질문이 비어 있습니다."})
            return
        state: dict = {}
        prev = "START"
        last = time.monotonic()
        try:
            for update in graph.stream(
                {"question": question, "model": model, "k": k}, stream_mode="updates"
            ):
                for node, partial in update.items():
                    state.update(partial)
                    now = time.monotonic()
                    payload = ragweb._jsonify_update(node, partial)
                    payload["from"] = prev
                    payload["elapsed_ms"] = int((now - last) * 1000)
                    note = ragweb._transition_note(prev, node, len(state.get("documents") or []))
                    if note:
                        payload["decision"] = note
                    self._sse("node", payload)
                    prev = node
                    last = now
            self._sse(
                "done",
                {
                    "generation": state.get("generation", ""),
                    "sources": sorted({c.relative_path for c in state.get("documents", [])}),
                    "rewrites": state.get("rewrites", 0),
                    "generations": state.get("generations", 0),
                    "model": model,
                    "decision": "답변 검증 통과(근거 충실 · 질문 적합) → 완료",
                },
            )
        except BrokenPipeError:
            return
        except Exception as exc:
            self._sse("error", {"message": ragweb._friendly_error(exc)})

    # --- 검증 루프 (self_reflection 그래프) --- reuses self_reflection_web labels
    def _stream_loop(self, params):
        self._begin_stream()
        graph = self.server.loop_graph  # type: ignore[attr-defined]
        task = (params.get("task") or [""])[0].strip()
        allowed, default_model = loopweb._allowed_models()
        model = (params.get("model") or [default_model])[0]
        if model not in allowed:
            model = default_model
        critic = (params.get("critic_model") or [model])[0]
        if critic not in allowed:
            critic = model
        if not task:
            self._sse("error", {"message": "작업(질문)이 비어 있습니다."})
            return
        state: dict = {}
        prev = "START"
        last = time.monotonic()
        try:
            for update in graph.stream(
                {"task": task, "model": model, "critic_model": critic},
                stream_mode="updates",
            ):
                for node, delta in update.items():
                    state.update(delta)
                    now = time.monotonic()
                    label, desc = loopweb.NODE_LABELS.get(node, (node, ""))
                    payload = {
                        "node": node,
                        "label": label,
                        "desc": desc,
                        "from": prev,
                        "elapsed_ms": int((now - last) * 1000),
                    }
                    if node == "generate":
                        payload["draft"] = delta.get("draft", "")
                        payload["drafts"] = delta.get("drafts")
                    elif node == "critique":
                        payload["verdict"] = delta.get("verdict", "")
                        payload["critique"] = delta.get("critique", "")
                    self._sse("node", payload)
                    prev = node
                    last = now
            self._sse(
                "done",
                {
                    "draft": state.get("draft", ""),
                    "drafts": state.get("drafts", 0),
                    "verdict": state.get("verdict", ""),
                    "model": model,
                    "critic_model": critic,
                },
            )
        except BrokenPipeError:
            return
        except Exception as exc:
            self._sse("error", {"message": f"{type(exc).__name__}: {exc}"})


def serve(source: str | None, host: str, port: int, embedding_model: str | None = None) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    server.loop_graph = build_reflection_graph()  # type: ignore[attr-defined]
    server.rag_graph = None  # type: ignore[attr-defined]
    if source:
        from rag import RAGIndex

        kwargs = {"source_dir": source}
        if embedding_model:
            kwargs["embedding_model"] = embedding_model
        index = RAGIndex(**kwargs)
        print(f"인덱싱 {source} ...", file=sys.stderr)
        index.refresh()
        server.rag_graph = build_graph(index)  # type: ignore[attr-defined]

    print(f"백엔드: {llm.backend_description()}", file=sys.stderr)
    print(f"통합 대시보드:  http://127.0.0.1:{port}", file=sys.stderr)
    print(
        "모드: 🔁 검증 루프"
        + (" + 🔍 RAG (활성)" if source else "  (RAG는 --source 폴더를 주면 활성화)"),
        file=sys.stderr,
    )
    if host == "0.0.0.0":
        print(f"다른 기기에서:  http://{_lan_ip()}:{port}", file=sys.stderr)
    print("Ctrl+C 로 종료", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.", file=sys.stderr)
        server.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python examples/menu_web.py",
        description="메뉴가 있는 통합 대시보드 (검증 루프 / RAG+검증).",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="RAG 검색 폴더. 주면 RAG 모드가 켜지고, 없으면 검증 루프 모드만.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="바인딩 주소 (다른 기기: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=8000, help="포트 (기본: 8000).")
    parser.add_argument(
        "--backend",
        choices=("auto", "api", "claude-cli"),
        default="claude-cli",
        help="LLM 백엔드 (기본: claude-cli, API 키 불필요).",
    )
    parser.add_argument("--model", default=None, help="시작 모델 (claude-cli: opus/sonnet/haiku).")
    parser.add_argument("--embedding-model", default=None, help="rag 임베딩 모델 교체 (RAG 모드).")
    args = parser.parse_args(argv)

    if args.backend != "auto":
        os.environ["ADAPTIVE_RAG_BACKEND"] = args.backend
    if args.model:
        os.environ["ADAPTIVE_RAG_WEB_MODEL"] = args.model

    serve(args.source, args.host, args.port, args.embedding_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
