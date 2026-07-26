"""A dependency-free local web dashboard for the Adaptive RAG graph.

Runs on Python's standard-library HTTP server only -- no FastAPI, no external
service, no account, no cost. Start it on the machine that has the index and
API key; open the printed URL from any browser on the same network (iPhone
Safari, Windows Chrome, ...). The dashboard shows the LangGraph process as a
live diagram, lets you pick the Claude model and retrieval depth, asks a
question, and streams each node's execution via Server-Sent Events so you can
watch the corrective loops (document filtering, query rewrites, answer
regeneration) light up on the graph as they happen. Conditional-edge routing
decisions (relevant? grounded? on-topic?) are surfaced as their own timeline
steps, mirroring how LangGraph Studio distinguishes direct vs conditional edges.

    uv run python -m adaptive_rag.web --source <folder> [--host 0.0.0.0] [--port 8000]

Requires ANTHROPIC_API_KEY (the graph's grading/generation nodes call Claude).
Binding --host 0.0.0.0 exposes the dashboard to every device on the local
network with no authentication -- only do that on a trusted network.
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

from rag import Chunk, RAGIndex

from adaptive_rag import build_graph, llm
from adaptive_rag.config import (
    ANSWER_MODEL,
    AVAILABLE_MODELS,
    CLI_MODEL_IDS,
    CLI_MODELS,
    DEFAULT_CLI_MODEL,
    MODEL_IDS,
    RETRIEVE_K,
)


def _model_ui():
    """(options, default_model, banner_html) for the active backend."""
    backend = llm.active_backend()
    if backend == "claude-cli":
        banner = (
            '<div class="banner">로컬 claude CLI 직접 · API 키 불필요<br>'
            "<small>모델을 고르면 요청마다 바로 반영됩니다(게이트웨이 재시작 불필요).</small></div>"
        )
        return CLI_MODELS, DEFAULT_CLI_MODEL, banner
    if backend == "gateway":
        banner = (
            '<div class="banner">CLI 게이트웨이 모드 · API 키 불필요<br>'
            "<small>모델은 게이트웨이 실행 옵션(--model)을 따릅니다. 아래 선택은 무시됩니다.</small></div>"
        )
        return AVAILABLE_MODELS, ANSWER_MODEL, banner
    return AVAILABLE_MODELS, ANSWER_MODEL, ""


def _allowed_models():
    """(valid model ids, default) for validating a /stream request."""
    if llm.active_backend() == "claude-cli":
        return CLI_MODEL_IDS, DEFAULT_CLI_MODEL
    return MODEL_IDS, ANSWER_MODEL


# Friendly labels + one-line descriptions for each node, shown in the timeline.
NODE_LABELS = {
    "retrieve": ("검색 (retrieve)", "rag 인덱스에서 청크 검색"),
    "grade_documents": ("문서 평가 (grade_documents)", "관련 없는 청크 제거"),
    "transform_query": ("질의 재작성 (transform_query)", "질문을 더 나은 검색어로"),
    "generate": ("답변 생성 (generate)", "근거 기반 답변 생성"),
}


def _transition_note(prev: str, cur: str, doc_count: int) -> str:
    """Explain the conditional-edge decision behind a `prev -> cur` transition.

    The graph's routers (decide_to_generate, grade_generation) are edges, not
    nodes, so their choice never shows up in `stream_mode="updates"`. But the
    choice IS the observed transition, so we can reconstruct exactly why the
    graph moved where it did and show it as a timeline "판정" (decision) row.
    """
    if prev == "grade_documents" and cur == "generate":
        if doc_count:
            return f"관련 문서 {doc_count}개 확보 → 답변 생성"
        return "관련 문서 없음(재작성 예산 소진) → 그대로 답변 생성"
    if prev == "grade_documents" and cur == "transform_query":
        return "관련 문서 없음 → 질의 재작성 후 재검색"
    if prev == "generate" and cur == "generate":
        return "환각 의심(근거 부족) → 같은 문서로 답변 재생성"
    if prev == "generate" and cur == "transform_query":
        return "답변이 질문에 미부합 → 질의 재작성 후 재검색"
    if prev == "transform_query" and cur == "retrieve":
        return "재작성한 질의로 재검색"
    return ""


def _chunk_summary(chunk: Chunk) -> dict:
    """JSON-serialisable summary of a retrieved chunk for the UI."""
    text = chunk.text.strip().replace("\n", " ")
    return {
        "path": chunk.relative_path,
        "score": round(float(chunk.score), 3),
        "preview": text[:180] + ("…" if len(text) > 180 else ""),
    }


def _jsonify_update(node: str, update: dict) -> dict:
    """Turn one node's raw state update into a UI-friendly event payload."""
    label, description = NODE_LABELS.get(node, (node, ""))
    payload: dict = {"node": node, "label": label, "description": description}
    if "documents" in update:
        docs = update["documents"]
        payload["documents"] = [_chunk_summary(c) for c in docs]
        payload["doc_count"] = len(docs)
    if "query" in update:
        payload["query"] = update["query"]
    if "rewrites" in update:
        payload["rewrites"] = update["rewrites"]
    if "generation" in update:
        payload["generation"] = update["generation"]
    if "generations" in update:
        payload["generations"] = update["generations"]
    return payload


def _friendly_error(exc: Exception) -> str:
    """Turn an exception into a message a first-time user can act on."""
    msg = str(exc)
    low = msg.lower()
    if llm.using_gateway() and any(
        k in low for k in ("refused", "urlopen", "connection", "timed out", "게이트웨이")
    ):
        return (
            "CLI 게이트웨이에 연결할 수 없습니다. `LLM_BASE_URL`이 가리키는 게이트웨이가 "
            "실행 중인지 확인하세요(예: 다른 터미널에서 cli_gateway.py 를 --backend claude "
            "--port 8765 로 실행). 별도 게이트웨이 없이 로컬 claude CLI 를 바로 쓰려면 "
            "`LLM_BASE_URL`을 해제하고 `--backend claude-cli`로 실행하세요. "
            f"(원본 오류: {msg})"
        )
    if any(k in low for k in ("auth", "api_key", "credential", "x-api-key")):
        return (
            "ANTHROPIC_API_KEY가 설정되지 않았거나 올바르지 않습니다. "
            "서버를 켠 터미널에서 `export ANTHROPIC_API_KEY=sk-ant-...` 를 실행한 뒤 "
            "서버를 다시 켜세요. (검색 단계까지는 키 없이도 동작합니다.) "
            "또는 API 키 대신 CLI 게이트웨이를 쓰려면 `LLM_BASE_URL`을 설정하세요."
        )
    return f"{type(exc).__name__}: {msg}"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the console clean
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html()
        elif parsed.path == "/stream":
            params = parse_qs(parsed.query)
            question = (params.get("question") or [""])[0].strip()
            allowed, default_model = _allowed_models()
            model = (params.get("model") or [default_model])[0]
            if model not in allowed:
                model = default_model
            try:
                k = int((params.get("k") or [str(RETRIEVE_K)])[0])
            except ValueError:
                k = RETRIEVE_K
            k = max(1, min(k, 20))
            self._stream_answer(question, model, k)
        else:
            self.send_error(404)

    def _send_html(self):
        body = render_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, event: str, data: dict):
        chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        self.wfile.write(chunk.encode("utf-8"))
        self.wfile.flush()

    def _stream_answer(self, question: str, model: str, k: int):
        # No Content-Length is possible on a stream, so signal the end of the
        # response by closing the connection when the stream finishes -- that
        # is how the client (EventSource, or a plain reader) detects EOF.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        if not question:
            self._sse("error", {"message": "질문이 비어 있습니다."})
            return

        graph = self.server.graph  # type: ignore[attr-defined]
        state: dict = {}
        prev = "START"
        last_ts = time.monotonic()
        try:
            for update in graph.stream(
                {"question": question, "model": model, "k": k},
                stream_mode="updates",
            ):
                for node, partial in update.items():
                    state.update(partial)
                    now = time.monotonic()
                    payload = _jsonify_update(node, partial)
                    payload["from"] = prev
                    payload["elapsed_ms"] = int((now - last_ts) * 1000)
                    note = _transition_note(prev, node, len(state.get("documents") or []))
                    if note:
                        payload["decision"] = note
                    self._sse("node", payload)
                    prev = node
                    last_ts = now
            self._sse(
                "done",
                {
                    "generation": state.get("generation", ""),
                    "sources": sorted(
                        {c.relative_path for c in state.get("documents", [])}
                    ),
                    "rewrites": state.get("rewrites", 0),
                    "generations": state.get("generations", 0),
                    "model": model,
                    "decision": "답변 검증 통과(근거 충실 · 질문 적합) → 완료",
                },
            )
        except BrokenPipeError:
            return  # client navigated away mid-stream
        except Exception as exc:  # surface errors (e.g. missing API key) to the UI
            self._sse("error", {"message": _friendly_error(exc)})


def _lan_ip() -> str:
    """Best-effort local network IP for the 'open from your phone' hint."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def render_page() -> str:
    """Render the dashboard HTML with the model dropdown + backend banner."""
    models, default_model, banner = _model_ui()
    options = "".join(
        f'<option value="{mid}"{" selected" if mid == default_model else ""}>'
        f"{label}</option>"
        for mid, label in models
    )
    return (
        PAGE.replace("<!--MODEL_OPTIONS-->", options)
        .replace("<!--DEFAULT_K-->", str(RETRIEVE_K))
        .replace("<!--BACKEND_BANNER-->", banner)
    )


def serve(index: RAGIndex, host: str, port: int) -> None:
    graph = build_graph(index)
    server = ThreadingHTTPServer((host, port), Handler)
    server.graph = graph  # type: ignore[attr-defined]

    print(f"백엔드: {llm.backend_description()}", file=sys.stderr)
    print(f"대시보드 실행 중:  http://127.0.0.1:{port}", file=sys.stderr)
    if host == "0.0.0.0":
        print(
            f"다른 기기(아이폰/윈도우)에서:  http://{_lan_ip()}:{port}"
            "  (같은 네트워크, 인증 없음)",
            file=sys.stderr,
        )
    print("Ctrl+C 로 종료", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.", file=sys.stderr)
        server.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m adaptive_rag.web",
        description="Adaptive RAG 그래프용 로컬 웹 대시보드.",
    )
    parser.add_argument("--source", required=True, help="인덱싱·검색할 폴더 경로.")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="바인딩 주소. 다른 기기에서 접속하려면 0.0.0.0 (기본: 127.0.0.1).",
    )
    parser.add_argument("--port", type=int, default=8000, help="포트 (기본: 8000).")
    parser.add_argument(
        "--backend",
        choices=("auto", "api", "claude-cli"),
        default="auto",
        help="LLM 백엔드. auto=자동 감지, claude-cli=로컬 claude CLI 직접(키 불필요, "
        "모델 요청별 선택), api=Anthropic API. 게이트웨이는 LLM_BASE_URL 로 자동 감지.",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="rag 임베딩 모델 교체 (예: 다국어 모델).",
    )
    args = parser.parse_args(argv)

    if args.backend != "auto":
        os.environ["ADAPTIVE_RAG_BACKEND"] = args.backend

    kwargs = {"source_dir": args.source}
    if args.embedding_model:
        kwargs["embedding_model"] = args.embedding_model
    index = RAGIndex(**kwargs)

    print(f"인덱싱 {args.source} ...", file=sys.stderr)
    stats = index.refresh()
    print(
        f"  added={stats.added} updated={stats.updated} deleted={stats.deleted} "
        f"unchanged={stats.unchanged} total_chunks={stats.total_chunks}",
        file=sys.stderr,
    )

    serve(index, args.host, args.port)
    return 0


# Self-contained page: no external CSS/JS/fonts, works offline and on mobile.
# Light/dark aware via prefers-color-scheme. Injected server-side:
# <!--MODEL_OPTIONS--> (dropdown), <!--DEFAULT_K--> (k), <!--BACKEND_BANNER-->.
PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Adaptive RAG 대시보드</title>
<style>
  :root {
    color-scheme: light dark;
    --bg:#f4f6f9; --panel:#ffffff; --panel-2:#f1f4f8; --border:#e2e7ef;
    --text:#182031; --muted:#5c6675; --faint:#8a93a2;
    --accent:#2563eb; --accent-soft:#e0e9ff; --accent-line:#2563eb;
    --node-bg:#ffffff; --active:#2563eb; --active-bg:#e0e9ff; --visited:#93b4f7;
    --edge:#c3ccd9; --edge-cond:#b48bd8; --edge-active:#2563eb;
    --good:#0f9d58; --good-soft:#e6f6ec; --good-border:#b6e0c6;
    --warn:#c2620a; --danger:#dc2626; --danger-soft:#fdeaea;
    --shadow:0 1px 2px rgba(20,30,60,.06), 0 6px 20px rgba(20,30,60,.05);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0d0f14; --panel:#12151c; --panel-2:#171b23; --border:#242a35;
      --text:#e6e9ef; --muted:#9aa4b2; --faint:#6b7482;
      --accent:#3b82f6; --accent-soft:#16233b; --accent-line:#60a5fa;
      --node-bg:#171b23; --active:#60a5fa; --active-bg:#16233b; --visited:#2b4a86;
      --edge:#39414f; --edge-cond:#8b6fb0; --edge-active:#60a5fa;
      --good:#34d399; --good-soft:#12261a; --good-border:#1f5a38;
      --warn:#fbbf24; --danger:#f87171; --danger-soft:#2a1414;
      --shadow:0 1px 2px rgba(0,0,0,.3), 0 6px 20px rgba(0,0,0,.25);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans KR",sans-serif;
    background:var(--bg); color:var(--text); line-height:1.55;
  }
  a { color:inherit; text-decoration:none; }

  .app { display:grid; grid-template-columns:264px 1fr; min-height:100vh; }
  @media (max-width:860px) { .app { grid-template-columns:1fr; } }

  /* ---- Sidebar ---- */
  aside {
    background:var(--panel); border-right:1px solid var(--border); padding:20px 18px;
    position:sticky; top:0; align-self:start; height:100vh; overflow-y:auto;
  }
  @media (max-width:860px) { aside { position:static; height:auto; } }
  .brand { font-size:17px; font-weight:700; letter-spacing:-.01em; }
  .brand .tag { display:inline-block; margin-left:6px; font-size:10px; font-weight:600;
    color:var(--accent); background:var(--accent-soft); border-radius:6px; padding:1px 6px; vertical-align:middle; }
  .brand small { display:block; font-weight:400; font-size:11.5px; color:var(--muted); margin-top:4px; }
  .section-title { font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--faint); margin:22px 0 9px; }
  .setting { margin-bottom:13px; }
  .setting label { display:block; font-size:12px; color:var(--muted); margin-bottom:5px; }
  select, input[type=number], input[type=text] {
    width:100%; padding:9px 11px; font-size:14px; border-radius:9px;
    border:1px solid var(--border); background:var(--panel-2); color:var(--text);
  }
  select:focus, input:focus { outline:2px solid var(--accent-soft); border-color:var(--accent); }
  .banner { background:var(--good-soft); border:1px solid var(--good-border); color:var(--good);
    border-radius:9px; padding:9px 11px; font-size:12px; margin-bottom:13px; }
  .banner small { color:var(--good); opacity:.85; }
  .legend { font-size:12px; color:var(--muted); display:flex; flex-direction:column; gap:7px; }
  .legend .dot { display:inline-block; width:11px; height:11px; border-radius:3px; margin-right:7px; vertical-align:middle; }
  .dot.active { background:var(--active); }
  .dot.visited { background:var(--visited); }
  .dot.idle { background:var(--panel-2); border:1px solid var(--border); }

  /* ---- Main ---- */
  main { padding:26px 28px 60px; max-width:1040px; }
  @media (max-width:860px) { main { padding:20px 16px 48px; } }
  header.top h1 { margin:0; font-size:22px; letter-spacing:-.02em; }
  header.top p { margin:6px 0 0; font-size:13.5px; color:var(--muted); max-width:70ch; }
  section { margin-top:28px; }
  section > h2 { font-size:13px; text-transform:uppercase; letter-spacing:.05em; color:var(--faint);
    margin:0 0 12px; display:flex; align-items:center; gap:8px; }

  .graphwrap { overflow-x:auto; background:var(--panel); border:1px solid var(--border);
    border-radius:14px; padding:16px; box-shadow:var(--shadow); }
  svg .gnode rect { fill:var(--node-bg); stroke:var(--border); stroke-width:1.5; transition:fill .25s, stroke .25s; }
  svg .gnode text { fill:var(--muted); font-size:12.5px; font-weight:600; transition:fill .25s; }
  svg .gnode.visited rect { stroke:var(--visited); }
  svg .gnode.visited text { fill:var(--text); }
  svg .gnode.active rect { fill:var(--active-bg); stroke:var(--active); stroke-width:2.5; }
  svg .gnode.active text { fill:var(--active); }
  svg .term rect { fill:var(--panel-2); }
  svg .edge { stroke:var(--edge); stroke-width:1.7; fill:none; transition:stroke .25s, stroke-width .25s; }
  svg .edge.cond { stroke:var(--edge-cond); stroke-dasharray:5 4; }
  svg .edge.edge-active { stroke:var(--edge-active); stroke-width:3.2; stroke-dasharray:none; }
  svg .elabel { fill:var(--faint); font-size:10.5px; }

  form.ask { display:flex; gap:9px; }
  form.ask input[type=text] { flex:1; padding:13px 14px; font-size:16px; }
  form.ask button {
    padding:13px 20px; font-size:15px; border:0; border-radius:11px;
    background:var(--accent); color:#fff; font-weight:600; cursor:pointer; white-space:nowrap;
  }
  form.ask button:disabled { opacity:.5; cursor:default; }
  .examples { margin-top:10px; display:flex; flex-wrap:wrap; gap:7px; }
  .chip { font-size:12px; color:var(--muted); background:var(--panel); border:1px solid var(--border);
    border-radius:999px; padding:5px 12px; cursor:pointer; }
  .chip:hover { border-color:var(--accent); color:var(--accent); }

  .status { margin-top:14px; font-size:13px; color:var(--muted); display:none; align-items:center; gap:8px; }
  .status.run, .status.ok, .status.err { display:flex; }
  .status .spin { width:13px; height:13px; border-radius:50%; border:2px solid var(--border);
    border-top-color:var(--accent); animation:spin .7s linear infinite; }
  .status.ok { color:var(--good); } .status.ok .spin { display:none; }
  .status.err { color:var(--danger); } .status.err .spin { display:none; }
  @keyframes spin { to { transform:rotate(360deg); } }

  #timeline { display:flex; flex-direction:column; gap:9px; }
  #timeline:empty::after { content:"질문하면 각 노드 실행과 라우팅 판정이 여기에 표시됩니다.";
    color:var(--faint); font-size:13px; }
  .router { font-size:12.5px; color:var(--muted); padding:2px 2px 2px 12px; border-left:2px dashed var(--edge-cond);
    margin-left:8px; }
  .router .rtag { font-size:10px; font-weight:700; color:var(--edge-cond); text-transform:uppercase;
    letter-spacing:.04em; margin-right:6px; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:12px;
    padding:12px 14px; box-shadow:var(--shadow); }
  .card .chead { display:flex; align-items:center; gap:8px; }
  .card .cicon { width:22px; height:22px; display:inline-flex; align-items:center; justify-content:center;
    background:var(--accent-soft); border-radius:6px; font-size:12px; }
  .card h3 { margin:0; font-size:14px; flex:1; }
  .card .ms { font-size:11px; color:var(--faint); font-variant-numeric:tabular-nums; }
  .card .desc { font-size:12px; color:var(--muted); margin:4px 0 0; }
  .badges { margin-top:7px; display:flex; flex-wrap:wrap; gap:5px; }
  .badge { display:inline-block; font-size:11px; color:var(--muted); background:var(--panel-2);
    border:1px solid var(--border); border-radius:6px; padding:2px 8px; }
  .doc { font-size:12px; color:var(--text); padding:7px 0 0; margin-top:7px; border-top:1px solid var(--border); }
  .doc .path { color:var(--accent); font-weight:600; word-break:break-all; }
  .doc .prev { color:var(--muted); margin-top:3px; }

  #answer { white-space:pre-wrap; background:var(--panel); border:1px solid var(--border);
    border-radius:12px; padding:16px; box-shadow:var(--shadow); }
  #answer.empty { color:var(--faint); }
  #answer.err { color:var(--danger); background:var(--danger-soft); border-color:var(--danger); }
  .stats { font-size:12px; color:var(--muted); margin-top:10px; }
  .stats .srcs { margin-top:7px; display:flex; flex-wrap:wrap; gap:5px; align-items:center; }
</style>
</head>
<body>
<div class="app">
  <aside>
    <div class="brand">Adaptive RAG<span class="tag">LangGraph</span><small>Corrective / Adaptive RAG 대시보드</small></div>
    <nav></nav>
    <div class="section-title">설정</div>
    <!--BACKEND_BANNER-->
    <div class="setting">
      <label for="model">Claude 모델</label>
      <select id="model"><!--MODEL_OPTIONS--></select>
    </div>
    <div class="setting">
      <label for="k">검색 문서 수 (k)</label>
      <input id="k" type="number" min="1" max="20" value="<!--DEFAULT_K-->">
    </div>
    <div class="section-title">범례</div>
    <div class="legend">
      <span><span class="dot active"></span>실행 중인 노드</span>
      <span><span class="dot visited"></span>지나온 노드</span>
      <span><span class="dot idle"></span>미실행</span>
    </div>
  </aside>

  <main>
    <header class="top">
      <h1>Adaptive RAG 대시보드</h1>
      <p>로컬 rag 라이브러리로 검색하고, Claude로 평가·재작성·생성·검증하는 LangGraph를 실시간으로 관찰합니다. 점선은 조건부 엣지(라우터)이며, 실행되면 경로와 판정 이유가 함께 표시됩니다.</p>
    </header>

    <section id="graph">
      <h2>LangGraph 프로세스</h2>
      <div class="graphwrap">
        <svg viewBox="0 0 880 320" width="100%" style="min-width:660px" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <marker id="arrow" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto">
              <path d="M0,0 L9,4.5 L0,9 z" fill="var(--edge)"/>
            </marker>
          </defs>
          <!-- edges (solid = direct, dashed .cond = conditional/router) -->
          <path class="edge" id="edge-START-retrieve" marker-end="url(#arrow)" d="M105,58 L105,88"/>
          <path class="edge" id="edge-retrieve-grade_documents" marker-end="url(#arrow)" d="M172,111 L232,111"/>
          <path class="edge cond" id="edge-grade_documents-generate" marker-end="url(#arrow)" d="M400,111 L470,111"/>
          <text class="elabel" x="405" y="103">관련 있음</text>
          <path class="edge cond" id="edge-grade_documents-transform_query" marker-end="url(#arrow)" d="M316,136 L316,216"/>
          <text class="elabel" x="322" y="182">관련 없음</text>
          <path class="edge" id="edge-transform_query-retrieve" marker-end="url(#arrow)" d="M232,241 L105,241 L105,136"/>
          <text class="elabel" x="118" y="258">재검색</text>
          <path class="edge cond" id="edge-generate-END" marker-end="url(#arrow)" d="M610,111 L700,111"/>
          <text class="elabel" x="616" y="103">검증 통과</text>
          <path class="edge cond" id="edge-generate-generate" marker-end="url(#arrow)" d="M514,88 C514,46 566,46 566,86"/>
          <text class="elabel" x="486" y="40">환각 → 재생성</text>
          <path class="edge cond" id="edge-generate-transform_query" marker-end="url(#arrow)" d="M540,136 C540,241 470,241 400,241"/>
          <text class="elabel" x="430" y="260">미부합 → 재작성</text>
          <!-- nodes -->
          <g class="gnode term" id="g-START"><rect x="40" y="28" width="130" height="30" rx="15"/><text x="105" y="47" text-anchor="middle">START</text></g>
          <g class="gnode" id="g-retrieve"><rect x="40" y="88" width="132" height="48" rx="11"/><text x="106" y="116" text-anchor="middle">retrieve</text></g>
          <g class="gnode" id="g-grade_documents"><rect x="232" y="88" width="168" height="48" rx="11"/><text x="316" y="116" text-anchor="middle">grade_documents</text></g>
          <g class="gnode" id="g-generate"><rect x="470" y="88" width="140" height="48" rx="11"/><text x="540" y="116" text-anchor="middle">generate</text></g>
          <g class="gnode term" id="g-END"><rect x="700" y="94" width="96" height="36" rx="18"/><text x="748" y="116" text-anchor="middle">END</text></g>
          <g class="gnode" id="g-transform_query"><rect x="232" y="216" width="168" height="48" rx="11"/><text x="316" y="244" text-anchor="middle">transform_query</text></g>
        </svg>
      </div>
    </section>

    <section id="run">
      <h2>질문</h2>
      <form class="ask" id="f">
        <input id="q" type="text" placeholder="예: 커밋 전에 무엇을 확인해야 하나요?" autocomplete="off">
        <button id="go" type="submit">질문</button>
      </form>
      <div class="examples" id="examples"></div>
      <div class="status" id="status"><span class="spin"></span><span id="status-text"></span></div>
    </section>

    <section id="flow">
      <h2>실행 흐름 · 라우팅 판정</h2>
      <div id="timeline"></div>
    </section>

    <section id="answer-sec">
      <h2>답변</h2>
      <div id="answer" class="empty">아직 질문하지 않았습니다.</div>
      <div id="stats" class="stats"></div>
    </section>
  </main>
</div>
<script>
const f=document.getElementById('f'), q=document.getElementById('q'), go=document.getElementById('go');
const modelSel=document.getElementById('model'), kInput=document.getElementById('k');
const timeline=document.getElementById('timeline'), answer=document.getElementById('answer');
const stats=document.getElementById('stats');
const statusEl=document.getElementById('status'), statusText=document.getElementById('status-text');
let es=null, t0=0, lastNode='START';

const ICON={retrieve:'🔍',grade_documents:'✓',transform_query:'✏️',generate:'✨'};
const EXAMPLES=['이 프로젝트는 무엇인가요?','커밋 전에 무엇을 확인해야 하나요?','실행 방법을 알려줘'];

const exWrap=document.getElementById('examples');
for(const t of EXAMPLES){
  const b=document.createElement('button'); b.type='button'; b.className='chip'; b.textContent=t;
  b.addEventListener('click',()=>{q.value=t; q.focus();});
  exWrap.appendChild(b);
}

function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function resetGraph(){
  document.querySelectorAll('.gnode').forEach(n=>n.classList.remove('active','visited'));
  document.querySelectorAll('.edge').forEach(e=>e.classList.remove('edge-active'));
  document.getElementById('g-START').classList.add('visited');
}
function activate(name){
  document.querySelectorAll('.gnode').forEach(n=>n.classList.remove('active'));
  const el=document.getElementById('g-'+name);
  if(el) el.classList.add('active','visited');
}
function lightEdge(a,b){
  document.querySelectorAll('.edge').forEach(e=>e.classList.remove('edge-active'));
  const e=document.getElementById('edge-'+a+'-'+b);
  if(e) e.classList.add('edge-active');
}
function setStatus(cls,text){ statusEl.className='status '+cls; statusText.textContent=text; }

function routerRow(note){
  const d=document.createElement('div'); d.className='router';
  d.innerHTML='<span class="rtag">판정</span>'+esc(note);
  timeline.appendChild(d); d.scrollIntoView({block:'nearest'});
}
function nodeCard(ev){
  const d=document.createElement('div'); d.className='card';
  let h='<div class="chead"><span class="cicon">'+(ICON[ev.node]||'•')+'</span><h3>'+esc(ev.label)+'</h3>';
  if(ev.elapsed_ms!=null) h+='<span class="ms">'+ev.elapsed_ms+' ms</span>';
  h+='</div>';
  if(ev.description) h+='<div class="desc">'+esc(ev.description)+'</div>';
  const b=[];
  if(ev.query!==undefined) b.push('검색어: '+esc(ev.query));
  if(ev.doc_count!==undefined) b.push('문서 '+ev.doc_count+'개');
  if(ev.rewrites!==undefined) b.push('재작성 '+ev.rewrites+'회');
  if(ev.generation!==undefined) b.push('생성 #'+(ev.generations||1));
  if(b.length) h+='<div class="badges">'+b.map(x=>'<span class="badge">'+x+'</span>').join('')+'</div>';
  if(ev.documents&&ev.documents.length){
    for(const doc of ev.documents){
      h+='<div class="doc"><span class="path">'+esc(doc.path)+'</span> '
       +'<span class="badge">score '+doc.score+'</span><div class="prev">'+esc(doc.preview)+'</div></div>';
    }
  }
  d.innerHTML=h; timeline.appendChild(d); d.scrollIntoView({block:'nearest'});
}

f.addEventListener('submit', e=>{
  e.preventDefault();
  const question=q.value.trim(); if(!question) return;
  if(es) es.close();
  timeline.innerHTML=''; answer.className='empty'; answer.textContent='실행 중…';
  stats.textContent=''; resetGraph(); go.disabled=true;
  setStatus('run','시작하는 중…'); t0=performance.now(); lastNode='START';

  const url='/stream?question='+encodeURIComponent(question)
          +'&model='+encodeURIComponent(modelSel.value)
          +'&k='+encodeURIComponent(kInput.value||'5');
  es=new EventSource(url);
  es.addEventListener('node', e=>{
    const ev=JSON.parse(e.data);
    lightEdge(ev.from||lastNode, ev.node);
    if(ev.decision) routerRow(ev.decision);
    activate(ev.node); nodeCard(ev);
    setStatus('run','실행 중: '+ev.label);
    if(ev.node==='generate' && ev.generation!==undefined){
      answer.className=''; answer.textContent=ev.generation;
    }
    lastNode=ev.node;
  });
  es.addEventListener('done', e=>{
    const d=JSON.parse(e.data);
    lightEdge(lastNode,'END'); activate('END');
    if(d.decision) routerRow(d.decision);
    answer.className=''; answer.textContent=d.generation||'(답변 없음)';
    const total=Math.round(performance.now()-t0);
    let s='모델 '+esc(d.model)+' · 재작성 '+d.rewrites+'회 · 생성 '+d.generations+'회 · 총 '+total+' ms';
    stats.innerHTML='<div>'+s+'</div>';
    if(d.sources&&d.sources.length){
      stats.innerHTML+='<div class="srcs">근거: '+d.sources.map(x=>'<span class="badge">'+esc(x)+'</span>').join('')+'</div>';
    }
    setStatus('ok','완료 · '+total+' ms');
    go.disabled=false; es.close();
  });
  es.addEventListener('error', e=>{
    answer.className='err';
    try{ answer.textContent='오류: '+JSON.parse(e.data).message; }
    catch(_){ answer.textContent='연결 오류'; }
    setStatus('err','오류가 발생했습니다');
    go.disabled=false; es.close();
  });
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
