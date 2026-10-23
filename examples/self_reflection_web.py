#!/usr/bin/env python3
"""자기검토 루프(RAG 없음)를 위한 실행 가능한 로컬 웹 대시보드.

`examples/self_reflection.py`의 LangGraph(생성→비평→재생성 루프)를 브라우저에서
실시간으로 관찰한다. RAG 대시보드(`adaptive_rag.web`)와 같은 방식이지만 검색이
전혀 없다: 표준 라이브러리 HTTP 서버 + SSE 만 쓴다.

    uv run python examples/self_reflection_web.py [--host 0.0.0.0] [--port 8000]

기본 백엔드는 claude-cli(로컬 claude CLI, API 키 불필요). 그래프 노드에는 영어
이름과 한글 이름을 함께 표시한다.
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

from adaptive_rag import llm
from adaptive_rag.config import (
    ANSWER_MODEL,
    AVAILABLE_MODELS,
    CLI_MODEL_IDS,
    CLI_MODELS,
    DEFAULT_CLI_MODEL,
    MODEL_IDS,
)
from examples.self_reflection import build_reflection_graph

# 노드 이름 → (영어 라벨, 한글 설명) — 타임라인 카드에 표시.
NODE_LABELS = {
    "generate": ("generate", "초안 생성 / 개선"),
    "critique": ("critique", "자기 비평 · GOOD/REVISE 판정"),
}


def _model_ui():
    """(options, default_model) for the active backend (claude-cli 별칭 or 전체 ID).

    ADAPTIVE_RAG_WEB_MODEL(= --model)이 있으면 드롭다운 기본 선택을 그걸로 한다.
    """
    if llm.active_backend() == "claude-cli":
        models, default = CLI_MODELS, DEFAULT_CLI_MODEL
    else:
        models, default = AVAILABLE_MODELS, ANSWER_MODEL
    override = os.environ.get("ADAPTIVE_RAG_WEB_MODEL")
    if override and override in {mid for mid, _label in models}:
        default = override
    return models, default


def _allowed_models():
    if llm.active_backend() == "claude-cli":
        return CLI_MODEL_IDS, DEFAULT_CLI_MODEL
    return MODEL_IDS, ANSWER_MODEL


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html()
        elif parsed.path == "/stream":
            params = parse_qs(parsed.query)
            task = (params.get("task") or [""])[0].strip()
            allowed, default_model = _allowed_models()
            model = (params.get("model") or [default_model])[0]
            if model not in allowed:
                model = default_model
            critic = (params.get("critic_model") or [model])[0]
            if critic not in allowed:
                critic = model
            self._stream(task, model, critic)
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

    def _stream(self, task: str, model: str, critic_model: str | None = None):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        if not task:
            self._sse("error", {"message": "작업(질문)이 비어 있습니다."})
            return

        graph = self.server.graph  # type: ignore[attr-defined]
        state: dict = {}
        prev = "START"
        last = time.monotonic()
        try:
            for update in graph.stream(
                {"task": task, "model": model, "critic_model": critic_model or model},
                stream_mode="updates",
            ):
                for node, delta in update.items():
                    state.update(delta)
                    now = time.monotonic()
                    label, desc = NODE_LABELS.get(node, (node, ""))
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
                    "critic_model": critic_model or model,
                },
            )
        except BrokenPipeError:
            return
        except Exception as exc:  # surface errors (e.g. missing claude CLI)
            self._sse("error", {"message": f"{type(exc).__name__}: {exc}"})


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def render_page() -> str:
    models, default_model = _model_ui()
    options = "".join(
        f'<option value="{mid}"{" selected" if mid == default_model else ""}>{label}</option>'
        for mid, label in models
    )
    return PAGE.replace("<!--MODEL_OPTIONS-->", options)


def serve(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    server.graph = build_reflection_graph()  # type: ignore[attr-defined]
    print(f"백엔드: {llm.backend_description()}", file=sys.stderr)
    print(f"자기검토 루프 대시보드:  http://127.0.0.1:{port}", file=sys.stderr)
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
        prog="python examples/self_reflection_web.py",
        description="자기검토 루프(RAG 없음) 로컬 웹 대시보드.",
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
    args = parser.parse_args(argv)

    if args.backend != "auto":
        os.environ["ADAPTIVE_RAG_BACKEND"] = args.backend
    if args.model:
        os.environ["ADAPTIVE_RAG_WEB_MODEL"] = args.model  # (참고용; 드롭다운 기본 선택엔 미반영)

    serve(args.host, args.port)
    return 0


# 자기 완결 페이지: 외부 CSS/JS/폰트 없음. <!--MODEL_OPTIONS--> 만 서버에서 주입.
PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>자기검토 루프 대시보드</title>
<style>
  :root{ color-scheme: light dark;
    --bg:#f4f6f9; --panel:#fff; --panel-2:#f1f4f8; --border:#e2e7ef; --text:#182031; --muted:#5c6675; --faint:#8a93a2;
    --accent:#2563eb; --accent-soft:#e0e9ff; --node-bg:#fff; --active:#2563eb; --active-bg:#e0e9ff; --visited:#93b4f7;
    --edge:#c3ccd9; --loop:#c2620a; --good:#0f9d58; --good-soft:#e6f6ec; --good-border:#b6e0c6; --danger:#dc2626;
    --shadow:0 1px 2px rgba(20,30,60,.06),0 6px 20px rgba(20,30,60,.05); }
  @media (prefers-color-scheme: dark){:root{
    --bg:#0d0f14; --panel:#12151c; --panel-2:#171b23; --border:#242a35; --text:#e6e9ef; --muted:#9aa4b2; --faint:#6b7482;
    --accent:#3b82f6; --accent-soft:#16233b; --node-bg:#171b23; --active:#60a5fa; --active-bg:#16233b; --visited:#2b4a86;
    --edge:#39414f; --loop:#fbbf24; --good:#34d399; --good-soft:#12261a; --good-border:#1f5a38; --danger:#f87171;
    --shadow:0 1px 2px rgba(0,0,0,.3),0 6px 20px rgba(0,0,0,.25); }}
  *{box-sizing:border-box;} body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans KR",sans-serif;background:var(--bg);color:var(--text);line-height:1.55;}
  .wrap{max-width:920px;margin:0 auto;padding:24px 20px 60px;}
  h1{margin:0;font-size:21px;letter-spacing:-.02em;} .sub{margin:6px 0 0;font-size:13px;color:var(--muted);}
  .pill{display:inline-block;font-size:11px;font-weight:600;color:var(--accent);background:var(--accent-soft);border-radius:999px;padding:2px 10px;margin-bottom:9px;}
  section{margin-top:24px;} h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--faint);margin:0 0 11px;}
  .box{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:15px;box-shadow:var(--shadow);}
  .graphwrap{overflow-x:auto;}
  svg .gnode rect{fill:var(--node-bg);stroke:var(--border);stroke-width:1.5;transition:fill .25s,stroke .25s;}
  svg .gnode text{fill:var(--muted);font-size:13px;font-weight:600;transition:fill .25s;}
  svg .gnode text.ko{fill:var(--faint);font-size:10.5px;font-weight:500;}
  svg .gnode.visited rect{stroke:var(--visited);} svg .gnode.visited text{fill:var(--text);}
  svg .gnode.active rect{fill:var(--active-bg);stroke:var(--active);stroke-width:2.5;} svg .gnode.active text{fill:var(--active);}
  svg .term rect{fill:var(--panel-2);}
  svg .edge{stroke:var(--edge);stroke-width:1.8;fill:none;transition:stroke .25s,stroke-width .25s;}
  svg .edge.cond{stroke-dasharray:5 4;} svg .edge.loop{stroke:var(--loop);stroke-dasharray:5 4;}
  svg .edge.edge-active{stroke:var(--active);stroke-width:3.4;stroke-dasharray:none;} svg .edge.loop.edge-active{stroke:var(--loop);}
  svg .elabel{fill:var(--faint);font-size:11px;} svg .elabel.loop{fill:var(--loop);font-weight:600;}
  form{display:flex;gap:9px;} input[type=text]{flex:1;padding:12px 14px;font-size:15px;border-radius:10px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);}
  input:focus,select:focus{outline:2px solid var(--accent-soft);border-color:var(--accent);}
  select{padding:9px 11px;font-size:14px;border-radius:10px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);}
  button{padding:12px 18px;font-size:15px;border:0;border-radius:10px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;white-space:nowrap;}
  button:disabled{opacity:.5;cursor:default;}
  .row{display:flex;gap:9px;align-items:center;margin-bottom:10px;} .row label{font-size:12px;color:var(--muted);}
  .status{margin-top:11px;font-size:13px;color:var(--muted);min-height:1.2em;} .status.ok{color:var(--good);} .status.err{color:var(--danger);}
  #timeline{display:flex;flex-direction:column;gap:9px;margin-top:12px;}
  #timeline:empty::after{content:"작업을 입력하고 실행하면 루프가 여기에 표시됩니다.";color:var(--faint);font-size:13px;}
  .router{font-size:12.5px;padding:2px 2px 2px 12px;margin-left:6px;border-left:2px dashed var(--loop);color:var(--muted);}
  .router.good{border-color:var(--good);} .router .rt{font-size:10px;font-weight:700;margin-right:6px;} .router .rt.rev{color:var(--loop);} .router .rt.ok{color:var(--good);}
  .step{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:11px 14px;box-shadow:var(--shadow);}
  .step .h{display:flex;align-items:center;gap:8px;} .step .ic{width:22px;height:22px;display:inline-flex;align-items:center;justify-content:center;background:var(--accent-soft);border-radius:6px;font-size:12px;}
  .step h3{margin:0;font-size:13.5px;flex:1;} .step .ms{font-size:11px;color:var(--faint);}
  .step .body{font-size:13px;margin-top:6px;white-space:pre-wrap;} .step .crit{color:var(--muted);}
  #answer{margin-top:12px;white-space:pre-wrap;background:var(--good-soft);border:1px solid var(--good-border);border-radius:12px;padding:15px;font-size:14px;}
  #answer.empty{background:var(--panel);border-color:var(--border);color:var(--faint);} #answer.err{background:var(--panel);border-color:var(--danger);color:var(--danger);}
</style>
</head>
<body>
<div class="wrap">
  <div class="pill">LangGraph · RAG 없음</div>
  <h1>자기검토 루프 대시보드</h1>
  <p class="sub">초안 생성 → 스스로 비평 → 부족하면 되돌아가 개선. 주황 점선이 <b style="color:var(--loop)">REVISE 되돌이(루프)</b>입니다. 실제 claude 호출로 돕니다.</p>

  <section>
    <h2>LangGraph 프로세스 (루프)</h2>
    <div class="box graphwrap">
      <svg viewBox="0 0 760 240" width="100%" style="min-width:560px" xmlns="http://www.w3.org/2000/svg">
        <defs><marker id="ar" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="var(--edge)"/></marker></defs>
        <path class="edge" id="e-START-generate" marker-end="url(#ar)" d="M112,59 L156,59"/>
        <path class="edge" id="e-generate-critique" marker-end="url(#ar)" d="M306,59 L380,59"/>
        <path class="edge cond" id="e-critique-END" marker-end="url(#ar)" d="M530,59 L600,59"/>
        <text class="elabel" x="536" y="50">GOOD → 종료</text>
        <path class="edge loop" id="e-critique-generate" marker-end="url(#ar)" d="M455,84 C455,180 231,180 231,84"/>
        <text class="elabel loop" x="286" y="200">REVISE → 되돌아가 재생성 (루프)</text>
        <g class="gnode term" id="g-START"><rect x="24" y="44" width="88" height="30" rx="15"/><text x="68" y="63" text-anchor="middle">START</text><text class="ko" x="68" y="92" text-anchor="middle">시작</text></g>
        <g class="gnode" id="g-generate"><rect x="156" y="34" width="150" height="50" rx="11"/><text x="231" y="55" text-anchor="middle">generate</text><text class="ko" x="231" y="72" text-anchor="middle">초안 생성</text></g>
        <g class="gnode" id="g-critique"><rect x="380" y="34" width="150" height="50" rx="11"/><text x="455" y="55" text-anchor="middle">critique</text><text class="ko" x="455" y="72" text-anchor="middle">자기 비평</text></g>
        <g class="gnode term" id="g-END"><rect x="600" y="42" width="96" height="34" rx="17"/><text x="648" y="63" text-anchor="middle">END</text><text class="ko" x="648" y="92" text-anchor="middle">종료</text></g>
      </svg>
    </div>
  </section>

  <section>
    <h2>실행</h2>
    <div class="row"><label for="model">작성자 모델</label><select id="model"><!--MODEL_OPTIONS--></select></div>
    <div class="row"><label for="critic-model">검토자 모델</label><select id="critic-model"><!--MODEL_OPTIONS--></select></div>
    <div style="font-size:11px;color:var(--faint);margin:-4px 0 8px;">검토자를 다른/더 센 모델로 두면 "LLM이 LLM 검증"이 실제로 의미 있어집니다(작성자·검토자 = 두 팀원).</div>
    <form id="f"><input id="q" type="text" value="재귀(recursion)를 3문장으로 쉽게 설명해줘" autocomplete="off"><button id="go" type="submit">실행 ▶</button></form>
    <div class="status" id="status"></div>
  </section>

  <section><h2>실행 흐름 · 판정</h2><div id="timeline"></div></section>
  <section><h2>최종 답</h2><div id="answer" class="empty">▶ 실행을 누르면 루프가 돕니다.</div></section>
</div>
<script>
const f=document.getElementById('f'),q=document.getElementById('q'),go=document.getElementById('go'),modelSel=document.getElementById('model'),criticSel=document.getElementById('critic-model');
const timeline=document.getElementById('timeline'),answer=document.getElementById('answer'),statusEl=document.getElementById('status');
let es=null,lastNode='START';
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function reset(){document.querySelectorAll('.gnode').forEach(n=>n.classList.remove('active','visited'));document.querySelectorAll('.edge').forEach(e=>e.classList.remove('edge-active'));document.getElementById('g-START').classList.add('visited');}
function activate(n){document.querySelectorAll('.gnode').forEach(x=>x.classList.remove('active'));const e=document.getElementById('g-'+n);if(e)e.classList.add('active','visited');}
function edge(a,b){document.querySelectorAll('.edge').forEach(e=>e.classList.remove('edge-active'));const e=document.getElementById('e-'+a+'-'+b);if(e)e.classList.add('edge-active');}
function router(kind,txt){const d=document.createElement('div');d.className='router '+(kind==='GOOD'?'good':'');d.innerHTML='<span class="rt '+(kind==='GOOD'?'ok':'rev')+'">판정</span>'+esc(txt);timeline.appendChild(d);}
function stepCard(icon,title,ms,body,cls){const d=document.createElement('div');d.className='step';let h='<div class="h"><span class="ic">'+icon+'</span><h3>'+esc(title)+'</h3>';if(ms!=null)h+='<span class="ms">'+ms+' ms</span>';h+='</div><div class="body '+(cls||'')+'">'+esc(body)+'</div>';d.innerHTML=h;timeline.appendChild(d);d.scrollIntoView({block:'nearest'});}
f.addEventListener('submit',e=>{e.preventDefault();const task=q.value.trim();if(!task)return;if(es)es.close();
  timeline.innerHTML='';answer.className='empty';answer.textContent='실행 중…';reset();go.disabled=true;statusEl.className='status';statusEl.textContent='시작…';lastNode='START';
  const url='/stream?task='+encodeURIComponent(task)+'&model='+encodeURIComponent(modelSel.value)+'&critic_model='+encodeURIComponent(criticSel.value);
  es=new EventSource(url);
  es.addEventListener('node',e=>{const ev=JSON.parse(e.data);edge(ev.from||lastNode,ev.node);activate(ev.node);
    if(ev.node==='generate'){stepCard('✍️','초안 #'+ev.drafts+' ('+ev.label+' · 초안 생성)',ev.elapsed_ms,ev.draft||'');answer.className='';answer.textContent=ev.draft||'';statusEl.textContent='실행 중: 초안 생성';}
    else if(ev.node==='critique'){const v=ev.verdict||'';router(v,(v==='GOOD'?'충분함 → 종료':'부족함 → 되돌아가 재생성'));stepCard('🔎','비평 → '+v+' ('+ev.label+' · 자기 비평)',ev.elapsed_ms,ev.critique||'','crit');statusEl.textContent='실행 중: 자기 비평 → '+v;}
    lastNode=ev.node;});
  es.addEventListener('done',e=>{const d=JSON.parse(e.data);edge('critique','END');activate('END');answer.className='';answer.textContent=d.draft||'(없음)';
    statusEl.className='status ok';statusEl.textContent='완료 · 초안 '+d.drafts+'회 · 판정 '+d.verdict+' · 작성 '+d.model+' / 검토 '+(d.critic_model||d.model);go.disabled=false;es.close();});
  es.addEventListener('error',e=>{answer.className='err';try{answer.textContent='오류: '+JSON.parse(e.data).message;}catch(_){answer.textContent='연결 오류';}statusEl.className='status err';statusEl.textContent='오류';go.disabled=false;es.close();});
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
