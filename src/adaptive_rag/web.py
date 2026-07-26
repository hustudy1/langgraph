"""A dependency-free local web dashboard for the Adaptive RAG graph.

Runs on Python's standard-library HTTP server only -- no FastAPI, no external
service, no account, no cost. Start it on the machine that has the index and
API key; open the printed URL from any browser on the same network (iPhone
Safari, Windows Chrome, ...). The dashboard shows the LangGraph process as a
live diagram, lets you pick the Claude model and retrieval depth, asks a
question, and streams each node's execution via Server-Sent Events so you can
watch the corrective loops (document filtering, query rewrites, answer
regeneration) light up on the graph as they happen.

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
        try:
            for update in graph.stream(
                {"question": question, "model": model, "k": k},
                stream_mode="updates",
            ):
                for node, partial in update.items():
                    state.update(partial)
                    self._sse("node", _jsonify_update(node, partial))
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
# Injected server-side: <!--MODEL_OPTIONS--> (dropdown), <!--DEFAULT_K--> (k).
PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Adaptive RAG 대시보드</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0d0f14; color: #e6e9ef; line-height: 1.5;
  }
  a { color: inherit; text-decoration: none; }

  .app { display: grid; grid-template-columns: 250px 1fr; min-height: 100vh; }
  @media (max-width: 820px) { .app { grid-template-columns: 1fr; } }

  /* ---- Sidebar ---- */
  aside {
    background: #12151c; border-right: 1px solid #232833; padding: 18px 16px;
    position: sticky; top: 0; align-self: start; height: 100vh; overflow-y: auto;
  }
  @media (max-width: 820px) { aside { position: static; height: auto; } }
  .brand { font-size: 16px; font-weight: 700; }
  .brand small { display: block; font-weight: 400; font-size: 11px; color: #8b94a3; margin-top: 3px; }
  nav { margin: 18px 0; display: flex; flex-direction: column; gap: 2px; }
  nav a { padding: 8px 10px; border-radius: 8px; font-size: 13px; color: #b7bfcc; }
  nav a:hover { background: #1a1f28; color: #fff; }
  .section-title { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #6b7482; margin: 16px 0 8px; }
  .setting { margin-bottom: 12px; }
  .setting label { display: block; font-size: 12px; color: #9aa4b2; margin-bottom: 4px; }
  select, input[type=number], input[type=text] {
    width: 100%; padding: 9px 10px; font-size: 14px; border-radius: 8px;
    border: 1px solid #2b303b; background: #171b23; color: #e6e9ef;
  }
  .banner { background: #12261a; border: 1px solid #1f5a38; color: #9fe7bf; border-radius: 8px; padding: 8px 10px; font-size: 12px; margin-bottom: 12px; }
  .banner small { color: #7bbd97; }
  .legend { font-size: 12px; color: #9aa4b2; display: flex; flex-direction: column; gap: 6px; }
  .legend .dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: middle; }
  .dot.active { background: #60a5fa; }
  .dot.visited { background: #1e3a8a; }
  .dot.idle { background: #232833; border: 1px solid #2b303b; }

  /* ---- Main ---- */
  main { padding: 20px; max-width: 1000px; }
  header.top h1 { margin: 0; font-size: 20px; }
  header.top p { margin: 4px 0 0; font-size: 13px; color: #9aa4b2; }
  section { margin-top: 24px; }
  section > h2 { font-size: 15px; margin: 0 0 10px; display: flex; align-items: center; gap: 8px; }
  section > h2 .num { background: #1e3a8a; color: #cfe0ff; font-size: 11px; border-radius: 999px; padding: 1px 8px; }

  .graphwrap { overflow-x: auto; background: #12151c; border: 1px solid #232833; border-radius: 12px; padding: 12px; }
  svg .gnode rect { fill: #1a1e26; stroke: #2b303b; stroke-width: 1.5; transition: fill .2s, stroke .2s; }
  svg .gnode text { fill: #c3c9d3; font-size: 12px; }
  svg .gnode.visited rect { stroke: #3b6fd4; }
  svg .gnode.active rect { fill: #1e3a8a; stroke: #60a5fa; stroke-width: 2.5; }
  svg .gnode.active text { fill: #fff; }
  svg .edge { stroke: #39414f; stroke-width: 1.6; fill: none; }
  svg .elabel { fill: #7a8494; font-size: 10.5px; }

  form.ask { display: flex; gap: 8px; }
  form.ask input[type=text] { flex: 1; padding: 12px; font-size: 16px; }
  form.ask button {
    padding: 12px 18px; font-size: 15px; border: 0; border-radius: 10px;
    background: #3b82f6; color: #fff; font-weight: 600; cursor: pointer;
  }
  form.ask button:disabled { opacity: .5; cursor: default; }

  .card { background: #12151c; border: 1px solid #232833; border-radius: 12px; padding: 12px 14px; margin-bottom: 10px; }
  .card h3 { margin: 0 0 4px; font-size: 14px; }
  .card .desc { font-size: 12px; color: #9aa4b2; margin-bottom: 6px; }
  .doc { font-size: 12px; color: #c3c9d3; padding: 4px 0; border-top: 1px solid #202632; }
  .doc .path { color: #7dd3fc; }
  .badge { display: inline-block; font-size: 11px; color: #9aa4b2; background: #1a1f28; border-radius: 6px; padding: 1px 7px; margin: 2px 4px 2px 0; }
  #timeline:empty::after { content: "질문하면 각 노드 실행이 여기에 표시됩니다."; color: #6b7280; font-size: 13px; }

  #answer { white-space: pre-wrap; background: #10141b; border: 1px solid #2b303b; border-radius: 12px; padding: 14px; }
  #answer.empty { color: #6b7280; }
  #answer.err { color: #f87171; }
  .stats { font-size: 12px; color: #9aa4b2; margin-top: 8px; }
</style>
</head>
<body>
<div class="app">
  <aside>
    <div class="brand">Adaptive RAG<small>LangGraph 대시보드</small></div>
    <nav>
      <a href="#graph">그래프 구조</a>
      <a href="#run">질문 · 실행 흐름</a>
      <a href="#answer-sec">답변</a>
    </nav>
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
      <p>로컬 rag 라이브러리로 검색하고, Claude로 평가·재작성·생성·검증하는 그래프를 실시간으로 관찰합니다.</p>
    </header>

    <section id="graph">
      <h2><span class="num">구조</span>LangGraph 프로세스</h2>
      <div class="graphwrap">
        <svg viewBox="0 0 820 300" width="100%" style="min-width:640px" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" fill="#39414f"/>
            </marker>
          </defs>
          <!-- edges -->
          <path class="edge" marker-end="url(#arrow)" d="M105,45 L105,68"/>
          <path class="edge" marker-end="url(#arrow)" d="M170,93 L228,93"/>
          <path class="edge" marker-end="url(#arrow)" d="M382,93 L468,93"/>
          <text class="elabel" x="392" y="86">관련 있음</text>
          <path class="edge" marker-end="url(#arrow)" d="M305,116 L305,183"/>
          <text class="elabel" x="312" y="152">관련 없음</text>
          <path class="edge" marker-end="url(#arrow)" d="M230,208 L112,208 L105,120"/>
          <text class="elabel" x="140" y="223">재검색</text>
          <path class="edge" marker-end="url(#arrow)" d="M602,93 L668,93"/>
          <text class="elabel" x="612" y="86">충분</text>
          <path class="edge" marker-end="url(#arrow)" d="M505,70 C505,32 565,32 560,68"/>
          <text class="elabel" x="486" y="26">환각 → 재생성</text>
          <path class="edge" marker-end="url(#arrow)" d="M535,116 C535,208 470,208 384,208"/>
          <text class="elabel" x="430" y="235">미해결 → 재작성</text>
          <!-- nodes -->
          <g class="gnode" id="g-START"><rect x="45" y="16" width="120" height="30" rx="15"/><text x="105" y="35" text-anchor="middle">START</text></g>
          <g class="gnode" id="g-retrieve"><rect x="40" y="70" width="130" height="46" rx="10"/><text x="105" y="97" text-anchor="middle">retrieve</text></g>
          <g class="gnode" id="g-grade_documents"><rect x="230" y="70" width="152" height="46" rx="10"/><text x="306" y="97" text-anchor="middle">grade_documents</text></g>
          <g class="gnode" id="g-generate"><rect x="470" y="70" width="132" height="46" rx="10"/><text x="536" y="97" text-anchor="middle">generate</text></g>
          <g class="gnode" id="g-END"><rect x="668" y="77" width="92" height="32" rx="16"/><text x="714" y="97" text-anchor="middle">END</text></g>
          <g class="gnode" id="g-transform_query"><rect x="230" y="185" width="152" height="46" rx="10"/><text x="306" y="212" text-anchor="middle">transform_query</text></g>
        </svg>
      </div>
    </section>

    <section id="run">
      <h2><span class="num">실행</span>질문</h2>
      <form class="ask" id="f">
        <input id="q" type="text" placeholder="질문을 입력하세요" autocomplete="off">
        <button id="go" type="submit">질문</button>
      </form>
      <h2 style="margin-top:18px"><span class="num">흐름</span>실행 흐름</h2>
      <div id="timeline"></div>
    </section>

    <section id="answer-sec">
      <h2><span class="num">결과</span>답변</h2>
      <div id="answer" class="empty">아직 질문하지 않았습니다.</div>
      <div id="stats" class="stats"></div>
    </section>
  </main>
</div>
<script>
const f = document.getElementById('f');
const q = document.getElementById('q');
const go = document.getElementById('go');
const modelSel = document.getElementById('model');
const kInput = document.getElementById('k');
const timeline = document.getElementById('timeline');
const answer = document.getElementById('answer');
const stats = document.getElementById('stats');
let es = null;

function clearGraph() {
  document.querySelectorAll('.gnode').forEach(n => n.classList.remove('active', 'visited'));
  document.getElementById('g-START').classList.add('visited');
}
function setActive(name) {
  document.querySelectorAll('.gnode').forEach(n => n.classList.remove('active'));
  const el = document.getElementById('g-' + name);
  if (el) { el.classList.add('active', 'visited'); }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function card(ev) {
  const d = document.createElement('div');
  d.className = 'card';
  let html = '<h3>' + ev.label + '</h3>';
  if (ev.description) html += '<div class="desc">' + ev.description + '</div>';
  if (ev.query !== undefined) html += '<span class="badge">검색어: ' + escapeHtml(ev.query) + '</span>';
  if (ev.rewrites !== undefined) html += '<span class="badge">재작성 ' + ev.rewrites + '회</span>';
  if (ev.doc_count !== undefined) html += '<span class="badge">문서 ' + ev.doc_count + '개</span>';
  if (ev.generation !== undefined) html += '<span class="badge">생성 #' + (ev.generations || 1) + '</span>';
  if (ev.documents) {
    for (const doc of ev.documents) {
      html += '<div class="doc"><span class="path">[' + escapeHtml(doc.path) + ']</span> '
            + '<span class="badge">score ' + doc.score + '</span><br>' + escapeHtml(doc.preview) + '</div>';
    }
  }
  d.innerHTML = html;
  timeline.appendChild(d);
}

f.addEventListener('submit', e => {
  e.preventDefault();
  const question = q.value.trim();
  if (!question) return;
  if (es) es.close();
  timeline.innerHTML = '';
  answer.className = 'empty';
  answer.textContent = '실행 중…';
  stats.textContent = '';
  clearGraph();
  go.disabled = true;

  const url = '/stream?question=' + encodeURIComponent(question)
            + '&model=' + encodeURIComponent(modelSel.value)
            + '&k=' + encodeURIComponent(kInput.value || '5');
  es = new EventSource(url);
  es.addEventListener('node', e => {
    const ev = JSON.parse(e.data);
    setActive(ev.node);
    card(ev);
  });
  es.addEventListener('done', e => {
    const d = JSON.parse(e.data);
    setActive('END');
    answer.className = '';
    answer.textContent = d.generation || '(답변 없음)';
    let s = '모델: ' + d.model + '  ·  재작성 ' + d.rewrites + '회 · 생성 ' + d.generations + '회';
    if (d.sources && d.sources.length) s += '  ·  근거: ' + d.sources.join(', ');
    stats.textContent = s;
    go.disabled = false; es.close();
  });
  es.addEventListener('error', e => {
    answer.className = 'err';
    try { answer.textContent = '오류: ' + JSON.parse(e.data).message; }
    catch (_) { answer.textContent = '연결 오류'; }
    go.disabled = false; es.close();
  });
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
