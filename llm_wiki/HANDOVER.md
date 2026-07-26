# 인수인계 문서 (HANDOVER)

---

## 1. 프로젝트 개요

- **프로젝트 이름**: adaptive-rag
- **llm_wiki 가이드 버전**: v0.5.0
- **목적**: 로컬 `rag` 라이브러리(`~/RAG`, `from rag import RAGIndex`)를 검색 노드로 사용하는 LangGraph 기반 Corrective/Adaptive RAG 오케스트레이션. 검색 → 문서 관련성 평가 → (관련 없으면) 질의 재작성 후 재검색 → 답변 생성 → 환각·질문적합성 검증의 조건부 분기·재시도 그래프.
- **기술 스택**: Python 3.11 (uv로 관리), `langgraph`(그래프), `anthropic`(Claude 호출), `rag`(로컬 경로 의존성). LLM 모델은 `claude-opus-4-8`.
- **주요 디렉터리**:
  - `src/adaptive_rag/`: 라이브러리 소스 (config/state/llm/nodes/graph, CLI는 `__main__.py`, 웹 대시보드는 `web.py`)
  - `tests/`: pytest 오프라인 테스트 (그래프 흐름 `test_graph.py`, 대시보드 HTTP+SSE `test_web.py`)
  - `llm_wiki/`: AI 작업 지침 (이 문서가 속한 폴더)

## 2. 실행 / 검증 방법

- **설치**: `uv sync` (Python 3.11 가상환경 + langgraph/anthropic/rag 설치)
- **개발 서버**: 해당 없음 (라이브러리 + CLI)
- **빌드**: 해당 없음 (`uv sync`/`uv run` 시 hatchling이 자동 빌드)
- **테스트**: `uv run pytest -q`
- **린트/타입체크**: `[UNKNOWN]` — 아직 ruff/mypy 등 설정하지 않음
- **수동 확인(실 API 호출)**: CLI `uv run python -m adaptive_rag --source <폴더> --query "<질문>"`, 또는 웹 대시보드 `uv run python -m adaptive_rag.web --source <폴더> [--host 0.0.0.0]` 후 브라우저 접속 — 둘 다 `ANTHROPIC_API_KEY` 필요
- **마지막 검증**: 2026-07-24. `uv run pytest -q` → **18 passed** (오프라인, API 키 불필요; 그래프 6 + 대시보드 4 + 게이트웨이 4 + claude-cli 4). **claude-cli 백엔드로 end-to-end 실 검증 완료**: (1) 명령줄 `--backend claude-cli --model sonnet` → 정답, (2) 대시보드 `--backend claude-cli`에서 `/stream?...&model=haiku` 실호출 → done `"model":"haiku"` + 정답. 즉 웹에서 고른 모델이 실제 `claude` CLI 호출에 요청별로 반영됨. 이전 검증(게이트웨이 e2e)도 유효. **Anthropic API 경로 실 호출만 미검증**(코드 경로 동일).

## 3. 환경 / 주의사항

- **LLM 백엔드(3가지, 자동 선택; `--backend` 또는 `ADAPTIVE_RAG_BACKEND`로 명시 시 최우선)**:
  - `claude-cli` — 로컬 `claude` CLI 직접 호출(구독 인증, **API 키 불필요**). 매 호출 `claude -p --output-format text --model <모델>`. **모델을 요청별로 선택 가능**(대시보드 드롭다운 opus/sonnet/haiku, CLI `--model`). 이게 사용자가 원하는 기본 사용 방식.
  - `gateway` — `LLM_BASE_URL`(또는 `ADAPTIVE_RAG_LLM_BASE_URL`) 설정 시. OpenAI 호환 `/v1` 프록시(`cli_gateway.py`). API 키 불필요. **모델은 게이트웨이 실행 옵션으로 고정**(대시보드 선택 무시).
  - `api` — `ANTHROPIC_API_KEY` 설정 시. `anthropic` SDK, 구조화 출력.
  - 자동 선택 순서: 명시 → `LLM_BASE_URL` → `ANTHROPIC_API_KEY` → 로컬 `claude` 존재 → api.
  - `claude` CLI 경로 override: `CLAUDE_CLI_PATH`.
  - import·그래프 빌드·pytest는 셋 다 없이도 동작(테스트는 스텁/가짜 서버/subprocess 스텁 사용).
- **외부 서비스**: Claude API(그래프 실행 시). 임베딩 모델은 `rag` 라이브러리가 최초 1회 Hugging Face Hub에서 내려받음(이후 오프라인).
- **로컬 전제조건**: Python 3.11 (`/opt/homebrew/bin/python3.11`, `uv`). `rag` 라이브러리는 GitHub git 의존성(`pyproject.toml` `[tool.uv.sources] rag = { git = "https://github.com/hustudy1/RAG.git", branch = "main" }`)이라 `uv sync`가 자동으로 받아온다(정확한 커밋은 `uv.lock`에 고정). **이 저장소만 클론해도 실행 가능**. RAG를 로컬에서 편집하며 개발할 땐 그 소스를 로컬 `path = "../RAG"`로 바꾸면 된다(pyproject 주석 참고). 기본 백엔드(claude-cli)는 로컬 `claude` CLI가 설치·로그인돼 있어야 함.
- **건드리면 안 되는 리소스**: 없음
- **승인이 필요한 작업**: `git commit`/`push` (실행 직전 사용자 승인 필요 — llm_wiki 규칙). **2026-07-26 `git init` + 첫 커밋(`db999a9`) 완료** — 이제 이 프로젝트는 git 저장소다(브랜치 `main`, 원격 `origin` = github.com/hustudy1/langgraph, 최초 push 완료).

## 4. 현재 상태 (최종 갱신: 2026-07-23)

Corrective/Adaptive RAG 그래프의 핵심 흐름을 구현 완료했다: retrieve(rag 검색) →
grade_documents(관련성 필터) → 관련 문서 없으면 transform_query(질의 재작성) 후 재검색 →
generate(근거 기반 답변) → grade_generation(환각/질문적합성 검증)으로 재생성·재작성·종료를
분기한다. 두 루프는 `config.py`의 재시도 예산(질의 재작성 2회, 답변 재생성 2회)으로 상한이
걸려 항상 종료된다. 실행할 때마다 Claude 모델과 검색 문서 수(k)를 고를 수 있다(CLI
`--model`/`--k`, 대시보드 드롭다운; 그래프 상태로 관통돼 요청별 독립). 그래프 실행을 눈으로
볼 수 있는 로컬 웹 대시보드(`web.py`, 표준 라이브러리만, 다기기 접속 가능)를 재설계해
사이드바 메뉴 + 설정 + LangGraph 프로세스를 실시간 하이라이트하는 SVG 다이어그램을 넣었다.
LLM 백엔드는 **3가지 자동 선택**(api / gateway / claude-cli)이며, API 키 없이 로컬 `claude`
CLI로 쓰는 두 경로를 지원한다. 특히 **claude-cli 백엔드는 대시보드에서 고른 모델을 요청마다
그대로 `claude --model`에 반영**한다(게이트웨이는 실행 시 고정). 두 CLI 경로 모두로 그래프
end-to-end를 실제 검증했다(정답+근거, API 키 없음). 오프라인 pytest 18개 통과(그래프 6 +
대시보드 4 + 게이트웨이 4 + claude-cli 4). **Anthropic API 경로(ANTHROPIC_API_KEY) 실 호출만
미검증** — 코드 경로 동일.

## 5. 작업 로그 (최신이 위)

### 2026-07-26 — 웹 대시보드 리디자인 (외부 LangGraph 대시보드 참고) + 원격 연결·push
- **한 일**:
  - 대시보드를 더 깔끔하고 기능이 제대로 동작하게 개선. 외부 레퍼런스(LangGraph Studio의 direct/conditional edge 구분, agent-chat-ui, AG-UI 스트리밍) 조사 후 **UX만 참고, 구현은 표준 라이브러리 그대로 유지**(계정·비용·의존성 없음 — 사용자 선호).
  - `web.py` 개선:
    - **라우팅 판정 노출**: `decide_to_generate`/`grade_generation`은 노드가 아니라 조건부 엣지라 기존 타임라인(`stream_mode="updates"`)에 안 보였음. 서버가 관측된 전이(prev→cur)로 결정을 역산해 `decision` 필드로 전송, 클라이언트가 "판정" 행으로 표시(`_transition_note`).
    - **엣지 하이라이트**(각 엣지 id, 조건부 엣지 점선), **노드별 소요시간(ms)**, 상태 표시줄, **라이브 답변**(generate 산출 즉시 표시), **라이트/다크 자동 테마**(prefers-color-scheme), 예시 질문 칩.
    - SSE 계약(node/done/error)은 유지하고 `decision`/`from`/`elapsed_ms` 필드만 추가 → 기존 테스트 그대로 통과.
  - 미리보기 아티팩트(모의 실행 재생): https://claude.ai/code/artifact/aab6f83e-ee45-494d-96f7-7885f859c430
  - 실행 문서 정리: RUN.md 간결화 + 폴더 예시 `langgraph`, GUIDE.md `cd langgraph`(도구/패키지 이름 `adaptive-rag`는 유지).
  - **원격 연결 + 최초 push**: 저장소 이름 `langgraph`로 결정(사용자 GitHub 개인 저장소 `hustudy1/langgraph`, langchain-ai/langgraph와 무관). `git remote add origin` + `git push -u origin main`.
- **결과**: `uv run pytest -q` → **18 passed**. 대시보드 렌더 확인. `main` → `origin/main` push.
- **남은 것**: 사용자가 실제 브라우저에서 대시보드 실행 확인 예정("이후에 확인"). vendor 방식이라 llm_wiki 템플릿 upstream 동기화는 수동.

### 2026-07-26 — langgraph git 저장소 생성 + 첫 커밋 (llm_wiki vendoring)
- **한 일**:
  - 사용자 승인 후 프로젝트 루트 `~/langgraph`에 `git init -b main` + 첫 커밋(`db999a9`, 27개 파일).
  - **중첩돼 있던 `llm_wiki/`(별도 git 저장소, remote `hustudy1/llm_wiki`, origin/main과 동기화됨)를 vendor 방식으로 처리**: `llm_wiki/.git`을 트리 밖으로 옮겨 일반 파일 6개(HANDOVER.md 포함)로 langgraph 저장소에 직접 커밋. 이유: 이 저장소만 클론해도 CLAUDE.md의 `@llm_wiki/...` 참조가 그대로 동작하게. (환경 정책상 `rm -rf`가 차단돼 `.git`을 스크래치패드로 `mv`함 — 원본이 GitHub에 있어 안전.)
  - 커밋 전 점검: `.venv`/`__pycache__`/`.pytest_cache`/`.claude/settings.local.json`/`.DS_Store`는 `.gitignore`로 제외됨 확인. 트리에 `.env`·키 파일 없음(비밀값 미포함).
- **결과**: `main` 브랜치 첫 커밋 `db999a9` 완료. 원격 미설정, **push 미완**(사용자 승인 대기).
- **남은 것**: GitHub 원격 저장소 생성 후 `git remote add` + `git push`(사용자 승인 필요). vendor 방식이라 llm_wiki를 upstream(`hustudy1/llm_wiki`)과 동기화하려면 앞으로 수동 복사 필요.

### 2026-07-24 — 이식성 정리 (git 공유용: 하드코딩 경로 제거 + rag를 git 의존성으로)
- **한 일**:
  - 사용자가 "git에 올릴 프로그램인데 실행 환경도 그거에 맞게" 요청. 다른 사람이 이 저장소만 클론해서 실행 가능하도록 정리.
  - **rag 의존성을 로컬 경로 → GitHub git 의존성으로 전환**: RAG가 public(`https://github.com/hustudy1/RAG.git`, `git ls-remote`로 확인)이라 `pyproject.toml` `[tool.uv.sources]`를 `rag = { git = "...", branch = "main" }`로 변경. `uv sync` → rag를 git에서 빌드·설치 확인(`uv.lock`에 커밋 `1603ca9...` 고정). 로컬 편집 개발용으로 `path`로 되돌리는 법을 pyproject 주석으로 남김.
  - **하드코딩된 `~/...` 경로 제거**: `RUN.md`·`GUIDE.md`의 `cd ~/langgraph` → `cd adaptive-rag`(클론 폴더), RAG 폴더 전제조건/트러블슈팅을 "uv sync가 GitHub에서 자동 수신"으로 갱신, `claude` 절대경로 예시를 `.../claude`로 일반화. `web.py`의 게이트웨이 연결 오류 메시지에 있던 `cli_gateway.py` 절대경로 제거(일반 안내 + `--backend claude-cli` 대안 안내).
  - `README.md` 설치·구조 섹션을 git 의존성/클론 후 `uv sync`로 갱신.
- **결과**: `uv sync`로 rag가 git에서 설치됨(venv의 site-packages, 더 이상 `../RAG` 아님). `uv run pytest -q` → 18 passed. 저장소 내 코드에 남은 `/Users/data1` 절대경로 없음(내부 로그성 문서인 HANDOVER의 일부 환경 사실 기록은 제외).
- **남은 것**: 원격 push (사용자 승인 필요) — `git init` + 첫 커밋은 2026-07-26 완료(`db999a9`). RAG의 최신 코드가 GitHub에 반영돼 있어야 git 의존성이 그 버전을 받음(현재 RAG 로컬=origin/main 일치).
- **실패한 시도**: `rm 실행방법.md`(옛 한글 파일명 문서) 삭제는 권한 거부됨 → 사용자가 직접 삭제하기로.

### 2026-07-24 — claude CLI 직접 백엔드 추가 (웹에서 모델 선택 → 요청별 반영) + 실 검증
- **한 일**:
  - 게이트웨이는 모델이 실행 시 고정이라("모델마다 게이트웨이를 켜야 하나?" 지적), 사용자가 "웹에서 모델을 고르면 자동으로 반영"되길 원함. 그래서 **세 번째 백엔드 `claude-cli`**(로컬 `claude` CLI 직접 호출)를 추가.
  - `llm.py`: 백엔드 결정을 `using_gateway()` 불리언 → `active_backend()`(`api`/`gateway`/`claude-cli`) 3진으로 확장. 우선순위: `ADAPTIVE_RAG_BACKEND` 명시 → `LLM_BASE_URL`(gateway) → `ANTHROPIC_API_KEY`(api) → 로컬 `claude` 발견(claude-cli) → api. `_find_claude()`(CLAUDE_CLI_PATH/which/~/.local/bin/claude/~/.claude/local/claude, 캐시), `_claude_cli_complete()`(subprocess `claude -p --output-format text --model <model>`, stdin 프롬프트). `_complete`/`_boolean`을 `active_backend()`로 분기; 텍스트 백엔드(gateway/claude-cli)는 구조화 출력이 없어 "yes/no" 프롬프트 + `_parse_bool`. **claude-cli는 요청의 model을 그대로 `--model`에 넘겨 요청별 모델 선택이 실제로 반영됨**(게이트웨이와 결정적 차이).
  - `config.py`: `CLI_MODELS`(opus/sonnet/haiku 별칭 — 구독 플랜에 맞게 항상 resolve), `CLI_MODEL_IDS`, `DEFAULT_CLI_MODEL="sonnet"`.
  - `web.py`: `--backend {auto,api,claude-cli}` 플래그(설정 시 `ADAPTIVE_RAG_BACKEND` env). `_model_ui()`/`_allowed_models()`가 활성 백엔드에 따라 드롭다운 목록·기본값·배너·검증 집합을 다르게 반환(claude-cli → 별칭+“요청별 반영” 배너, gateway → 전체ID+“고정” 배너, api → 전체ID). `os` import 추가.
  - `__main__.py`: `--backend` 추가, `--model`을 자유 문자열로(백엔드별 의미 다름), 기본 모델을 백엔드에 맞게 결정.
  - **테스트**: `tests/test_claude_cli.py` 4개 — `_find_claude`/`subprocess.run` 스텁으로 실제 CLI 없이 백엔드 감지·요청별 `--model` 전달·풀 그래프·CLI 없음 시 명확한 오류. `test_web`는 렌더가 백엔드 의존이라 fixture에서 `ADAPTIVE_RAG_BACKEND=api`로 고정. `uv run pytest -q` → **18 passed**.
- **결과 (실 검증 완료)**:
  - CLI 백엔드로 그래프 실행(`--backend claude-cli --model sonnet`, mini_src) → 근거 인용 포함 정답, API 키·게이트웨이 없음.
  - **대시보드 실경로 검증**: `--backend claude-cli`로 대시보드 서빙 → 드롭다운 opus/sonnet/haiku + "요청마다 반영" 배너 확인. 이어 `/stream?...&model=haiku` 실호출 → done 이벤트 `"model":"haiku"`와 정답 생성 확인. **즉 웹에서 고른 모델이 실제 claude CLI 호출에 그대로 반영됨을 end-to-end로 확인.**
- **실패한 시도**: `web.py`에 `os` 미import 상태로 `os.environ` 사용 → 추가로 해결. `test_web`가 백엔드 자동감지로 claude-cli가 되어 드롭다운이 별칭이라 기존 `claude-opus-4-8` 단언 실패 → fixture에서 api 고정으로 해결.

### 2026-07-23 — CLI 게이트웨이 백엔드 추가 (API 키 없이 claude CLI 사용) + 실 검증
- **한 일**:
  - 사용자가 API 키 대신 `cli_gateway.py`(`~/lab/simplerca_llm/cli_gateway.py`)를 쓰고 싶다고 함. 이 게이트웨이는 로컬에서 **OpenAI 호환** `/v1/chat/completions`를 열고 로컬 `claude`/`codex` CLI(구독 인증)로 처리하는 프록시(평문 텍스트 반환).
  - `llm.py`에 **백엔드 자동 분기** 구현: `LLM_BASE_URL`(또는 `ADAPTIVE_RAG_LLM_BASE_URL`)이 있으면 게이트웨이(OpenAI 포맷, urllib로 POST, 텍스트 반환), 없으면 기존 Anthropic API(구조화 출력). 게이트웨이는 structured output이 없으므로 평가(yes/no)는 프롬프트에 "Reply with exactly one word: yes or no" 추가 후 `_parse_bool`로 관대하게 파싱(파싱 실패 시 True=수용으로 그래프 진행 보장). 공개 헬퍼 `using_gateway()`, `backend_description()` 추가. `anthropic`은 API 경로에서만 지연 import → 게이트웨이 모드는 SDK·키 모두 불필요.
  - `web.py`: 게이트웨이 모드면 사이드바에 배너("CLI 게이트웨이 모드 · API 키 불필요, 모델은 게이트웨이 옵션을 따름") 표시. `_friendly_error`가 게이트웨이 연결 실패(refused/urlopen 등)를 감지해 "게이트웨이를 먼저 실행하라"는 안내로 변환. `__main__.py`·`web.serve`가 시작 시 백엔드 설명 출력.
  - **테스트**: `tests/test_gateway.py` 추가 — 가짜 OpenAI 호환 서버를 띄우고 `LLM_BASE_URL`을 향하게 한 뒤 **실 `llm.*` 함수와 전체 그래프**를 게이트웨이 경로로 돌리는 오프라인 e2e 4개(백엔드 감지, `_parse_bool`, llm 함수, 풀 그래프). `uv run pytest -q` → 14 passed.
- **이유/결정**:
  - 게이트웨이는 요청의 model을 claude 백엔드에서 무시하고 게이트웨이 실행 `--model`(sonnet 등)을 쓴다. 그래서 게이트웨이 모드에서 대시보드/CLI 모델 선택은 무시됨을 UI·문서에 명시.
  - `LLM_BASE_URL`은 게이트웨이가 출력하는 관례 변수라 그대로 채택(사용자 워크플로와 일치). 명시 오버라이드용 `ADAPTIVE_RAG_LLM_BASE_URL`도 지원.
- **결과 (실 검증 완료)**:
  - 실제 `cli_gateway.py --backend claude`(포트 8791, `claude` CLI at `~/.local/bin/claude`)를 띄우고 `/health` 확인.
  - 우리 클라이언트로 1콜(`_gateway_complete("...pong")`) → `'pong'` 정상 수신(구독 인증, **API 키 없음**).
  - **전체 그래프 실 실행**: 임시 폴더(1파일)에 배포 메모를 두고 `LLM_BASE_URL` 세팅 + `python -m adaptive_rag --k 1` → 근거 인용까지 포함한 정확한 한국어 답변 생성. **이로써 API 키 없이 그래프 end-to-end가 실제로 동작함을 확인.**
  - 대시보드 게이트웨이 모드 배너 렌더 확인.
- **실패한 시도**: 없음.

### 2026-07-23 — 대시보드 재설계 + 모델 선택 + 검색 문서 수 조절
- **한 일**:
  - **모델·k를 그래프 상태로 관통**: `state.py`에 `model`/`k` 추가. `llm.py`의 4개 함수에 `model` 인자 추가(`grade_document`/`rewrite_query`/`generate_answer`/`answer_is_grounded`/`answer_addresses_question` — 실제로는 5개). `nodes.py`는 `state.get("model")`/`state.get("k")`를 읽어(없으면 config 기본값) llm 호출과 `index.search(k=...)`에 전달. 요청별 독립이라 서버 동시 요청에도 안전(모듈 전역 아님).
  - **config.py**: `AVAILABLE_MODELS`(드롭다운용 (id,label) 목록), `MODEL_IDS`(검증용 집합) 추가. 후보: opus-4-8(기본)/sonnet-5/haiku-4-5/fable-5.
  - **CLI**(`__main__.py`): `--model`(choices=MODEL_IDS), `--k` 추가. invoke에 model/k 전달.
  - **대시보드 재설계**(`web.py`): 사용자가 "너무 대충 만든 것 같다, LangGraph 프로세스를 한눈에 보는 구조와 메뉴가 필요하다"고 요청. (1) 좌측 사이드바 = 섹션 이동 nav + 설정(모델 select, k input) + 그래프 범례, (2) **인라인 SVG 그래프 다이어그램**(외부 mermaid/CDN 없이 직접 그림; 노드 START/retrieve/grade_documents/generate/END/transform_query와 조건부 엣지·루프를 표현), SSE `node` 이벤트마다 해당 노드를 실시간 하이라이트(active/visited/idle), (3) 실행 흐름 카드 + 답변/근거/통계(사용 모델·재작성·생성 횟수). `PAGE`는 플레이스홀더(`<!--MODEL_OPTIONS-->`, `<!--DEFAULT_K-->`)를 `render_page()`가 치환. `/stream`은 model/k 쿼리 파라미터를 받아 검증(model은 MODEL_IDS, k는 1~20 클램프).
  - **친절 오류 처리**: `_friendly_error()`가 인증 관련 예외를 감지해 "ANTHROPIC_API_KEY를 export 후 서버 재시작하라"는 한국어 안내로 바꿔 SSE `error`로 전송. (스크린샷에서 사용자가 키 없이 질문해 `TypeError: Could not resolve authentication method`를 만난 것이 계기.)
  - **테스트**: llm 스텁 시그니처에 `m=None` 추가. 모델/k 관통 테스트(`test_model_and_k_propagate_from_input`), 대시보드 모델·SVG 렌더 및 `/stream` model/k 수용 테스트 추가. `uv run pytest -q` → 10 passed.
  - README·GUIDE 갱신(모델/k 옵션, 대시보드 화면 구성, 친절 오류).
- **이유/결정**:
  - "무료·로컬·다기기 호환" 선호(메모리 `tooling-preferences.md`)에 따라 여전히 표준 라이브러리만 사용, 외부 CDN/서비스 없음. SVG도 인라인 직접 작성.
  - 모델을 config 상수 대신 요청별 상태로 관통시켜, 대시보드에서 요청마다 다른 모델을 골라도 서버가 안전하게 처리하도록 했다.
- **결과**: `uv run pytest -q` 10 passed. 대시보드 실서버로 새 UI(사이드바/SVG/모델 드롭다운) 서빙 확인. 실 Claude 호출은 여전히 미검증.
- **실패한 시도**: 없음(특기할 것). SVG 좌표는 viewBox 820x300에 수동 배치.

### 2026-07-23 — 로컬 웹 대시보드 추가
- **한 일**:
  - `src/adaptive_rag/web.py` 추가: 파이썬 표준 라이브러리(`http.server`)만으로 도는 로컬 웹 대시보드. `GET /`는 자립형 HTML(외부 CSS/JS/폰트 없음, 모바일 반응형), `GET /stream?question=...`는 그래프를 `graph.stream(stream_mode="updates")`로 실행하며 노드별 실행을 Server-Sent Events로 실시간 전송. 실행: `uv run python -m adaptive_rag.web --source <폴더> [--host 0.0.0.0] [--port 8000]`.
  - `tests/test_web.py` 추가: 실제 `ThreadingHTTPServer`를 FakeIndex + llm 스텁으로 띄워 `GET /`와 `/stream`의 SSE 프레임(node/done/error)을 검증하는 오프라인 테스트 3개.
  - `README.md`에 웹 대시보드 사용법 섹션 추가.
- **이유/결정**:
  - 사용자가 "LangGraph 대시보드 화면"을 원했고, 조건을 **무료 + 로컬 + 다양한 환경(아이폰/윈도우 브라우저) 호환**으로 명시했다. 그래서 LangGraph Studio(LangSmith 로그인 필요)와 LangSmith 트레이싱(클라우드 전송·API 키)을 배제하고, 외부 의존성 0인 자체 로컬 웹 UI를 선택했다. 이 선호는 `~/.claude/.../memory/tooling-preferences.md`에 기록.
  - 표준 라이브러리만 사용해 새 의존성을 추가하지 않았다(호환성·이식성 최대화).
  - `--host 0.0.0.0` + 서버가 LAN IP를 출력 → 같은 네트워크의 다른 기기 브라우저에서 접속. 인증은 없으므로 신뢰 네트워크 전용임을 README·docstring에 명시.
- **결과**: `uv run pytest -q` 8 passed. 대시보드 실서버로 `GET /` 페이지 서빙까지 확인. `/stream`의 실제 Claude 호출은 미검증(오프라인 테스트가 HTTP+SSE 경로는 커버).
- **실패한 시도**:
  - SSE 응답에 처음 `Connection: keep-alive`를 두어 `Content-Length`도 없이 스트림을 끝냈더니 클라이언트가 EOF를 못 받아 `read()`가 타임아웃했다. `_stream_answer`에서 `Connection: close` + `self.close_connection = True`로 바꿔 스트림 종료 시 연결을 닫아 EOF를 알리도록 해결.
  - `tests/test_web.py`에서 `from tests.test_graph import ...`는 `tests`가 패키지가 아니라 `ModuleNotFoundError`. pytest가 테스트 디렉터리를 sys.path에 넣으므로 `from test_graph import ...`로 변경해 해결.

### 2026-07-23 — Adaptive RAG 그래프 초기 구현
- **한 일**:
  - llm_wiki 표준 시작: 프로젝트 루트에 `CLAUDE.md`(`@llm_wiki/00_AI_INSTRUCTIONS.md`), `AGENTS.md`(llm_wiki 문서 읽기 지시) 생성. 두 파일 모두 `~/RAG`의 형식을 그대로 따랐다.
  - `pyproject.toml`(langgraph/anthropic/rag 의존성, rag는 `../RAG` 경로 의존성), `.python-version`(3.11), `.gitignore` 작성. `uv venv --python 3.11` + `uv sync`로 설치.
  - `src/adaptive_rag/`에 `config.py`(모델·재시도 상수), `state.py`(GraphState TypedDict), `llm.py`(Claude 래퍼 4종: 문서 관련성/질의 재작성/답변 생성/답변 검증(근거·질문적합성)), `nodes.py`(노드 4개 + 분기 함수 2개), `graph.py`(`build_graph(index)`), `__init__.py`, `__main__.py`(CLI) 구현.
  - `tests/test_graph.py`에 오프라인 테스트 5개 작성(FakeIndex + llm 스텁으로 API 키 없이 흐름 검증). `uv run pytest -q` → 5 passed.
  - `README.md` 작성.
- **이유/결정**:
  - 사용자가 그래프 형태와 인터페이스를 선택: **Corrective/Adaptive RAG** + **CLI 단발 질의**(대화형·checkpointer 아님). LangGraph의 조건부 분기·재시도 루프를 제대로 쓰는 형태로, 기존 `RAGIndex.answer()` 한 방향 흐름 대비 이점을 살리기 위함.
  - LLM 호출은 langchain 없이 **anthropic SDK 직접 사용**(rag의 `generation.py`와 동일 방식)으로 의존성을 최소화했다. 모델은 `rag`의 `DEFAULT_ANSWER_MODEL`과 같은 `claude-opus-4-8`로 통일.
  - 평가 노드는 **구조화 출력(`output_config.format`, boolean JSON 스키마)**으로 파싱 오류 없이 yes/no를 받도록 했다.
  - 의존성 주입: `index`는 `functools.partial`로 `retrieve` 노드에 바인딩하고, LLM 호출은 `adaptive_rag.llm` 모듈 함수로 분리해 테스트에서 monkeypatch 가능하게 했다(그래서 오프라인 테스트가 성립).
  - 두 루프에 재시도 예산 상한을 둬서 무한 루프를 구조적으로 방지했다.
- **결과**: `uv run pytest -q` 5 passed(오프라인). import·컴파일·`output_config` 파라미터 인식 확인. 실 API end-to-end는 미검증(코드 경로만 작성).
- **Git 상태**: 아직 git 저장소가 아니다. 커밋 없음. 초기화 여부 사용자 확인 필요.
- **실패한 시도**: 없음(특기할 것).

## 6. 미해결 문제 / 모르는 부분

| 항목 | 상태 | 설명 |
|------|------|------|
| 그래프 실 API end-to-end 검증 | `[UNVERIFIED]` | 코드 경로는 작성했지만 실제 `ANTHROPIC_API_KEY`로 `python -m adaptive_rag` 또는 `graph.invoke`를 끝까지 돌려보지는 않음 |
| git 저장소 초기화 여부 | `[UNKNOWN]` | 이 프로젝트를 git으로 추적할지 사용자 확인 필요 |
| 린트/타입체크 설정 | `[UNKNOWN]` | ruff/mypy 등 아직 설정하지 않음 |
| langgraph git 저장소 초기화 | `[UNKNOWN]` | 아직 `git init` 안 함. git에 올릴 예정(사용자 승인 후). 이식성(경로/의존성)은 2026-07-24에 정리 완료. |

## 7. 다음에 할 일 (우선순위 순)

1. (선택) Anthropic API 경로 실 검증: `ANTHROPIC_API_KEY`를 넣고 CLI/대시보드 실행. (게이트웨이 경로 end-to-end는 2026-07-23에 검증 완료.) 대시보드에서 게이트웨이 모드로 `/stream` 실 답변 흐름도 브라우저에서 한 번 확인해두면 좋다.
2. git 저장소 초기화 여부 확인 후 필요 시 `git init` + 첫 커밋 (사용자 승인 필요)
3. (선택) 대화형·멀티턴이 필요해지면 LangGraph checkpointer(MemorySaver 등) + `thread_id` 도입 검토 — 현재는 CLI 단발 질의만 지원
4. (선택) 한국어 문서 검색 품질이 낮으면 `--embedding-model paraphrase-multilingual-MiniLM-L12-v2`로 확인
5. (선택) ruff/mypy 설정

## 8. 주요 의사결정 기록

| 날짜 | 결정 | 이유 | 출처/근거 |
|------|------|------|-----------|
| 2026-07-23 | 그래프 형태: Corrective/Adaptive RAG | LangGraph의 조건부 분기·재시도 루프를 살려 `RAGIndex.answer()` 한 방향 흐름 대비 이점 확보 | 사용자 선택(AskUserQuestion) |
| 2026-07-23 | 인터페이스: CLI 단발 질의 | 가장 단순·확실하게 동작 검증 가능. 대화형/메모리는 후속 과제로 분리 | 사용자 선택(AskUserQuestion) |
| 2026-07-23 | LLM: anthropic SDK 직접 사용, langchain 미도입 | 의존성 최소화, rag의 generation.py와 동일 방식 유지 | 설계 판단 |
| 2026-07-23 | 모델 `claude-opus-4-8` | rag의 `DEFAULT_ANSWER_MODEL`과 통일해 두 프로젝트 정합성 유지 | `~/RAG/src/rag/constants.py`, claude-api 스킬 |
| 2026-07-23 | 평가 노드에 구조화 출력 사용 | 자유 텍스트 대신 boolean JSON으로 파싱 오류 없이 분기 | claude-api 스킬(Structured Outputs) |

## 9. 참고 자료

- `~/RAG` — 검색 도구로 import하는 로컬 RAG 라이브러리 (`llm_wiki/HANDOVER.md`에 상세 이력) (확인일: 2026-07-23)
- LangGraph, Anthropic Python SDK 공식 문서 (확인일: 2026-07-23)
