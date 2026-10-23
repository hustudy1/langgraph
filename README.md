# adaptive-rag

로컬 `rag` 라이브러리를 검색 도구로 사용하는 **LangGraph 기반 Corrective/Adaptive RAG**
오케스트레이션이다. 단순히 "검색 → 답변"에 그치지 않고, 검색 결과의 관련성을 평가하고
필요하면 질의를 다시 써서 재검색하며, 생성한 답변의 환각(hallucination)과 질문 적합성을
검증한 뒤 반환한다.

## 왜 LangGraph인가

`rag` 라이브러리에는 이미 `RAGIndex.answer()`(검색 → Claude 답변 생성)가 있다. 이
프로젝트가 LangGraph를 쓰는 이유는 그 한 방향 흐름으로는 못 하는 **조건부 분기와 재시도
루프**를 얹기 위해서다.

```
START → retrieve → grade_documents
                        │
        (관련 문서 있음?) ─┼─ 없음 · 재시도 예산 남음 → transform_query ─┐
                        │                                            │(재검색)
                        │ 있음 / 예산 소진                              │
                        ▼                                            │
                    generate ←──────────────────────────────────┐   │
                        │                                        │   │
                  grade_generation                              │   │
                        │                                        │   │
     환각(근거 부족) 재시도 ─┼─→ generate ───────────────────────────┘   │
     질문 미해결(재작성) ───┼─→ transform_query ─────────────────────────┘
     충분함 ─────────────┴─→ END
```

두 루프(질의 재작성, 답변 재생성) 모두 `config.py`의 재시도 예산으로 상한이 걸려 있어
그래프는 항상 답변과 함께 종료된다.

> **바로 실행하려면** → [`RUN.md`](RUN.md) (복사·붙여넣기용 빠른 시작).
> 처음 쓰는 사람의 자세한 안내 → [`GUIDE.md`](GUIDE.md).

## 설치

```bash
uv sync
```

Python 3.11 이상이 필요하다. `rag` 라이브러리는 GitHub(`hustudy1/RAG`)에서 git 의존성으로
자동으로 받아온다(`pyproject.toml`의 `[tool.uv.sources]`, 정확한 커밋은 `uv.lock`에 고정).
따라서 이 저장소만 클론해도 `uv sync` 한 번이면 된다. `rag`를 로컬에서 직접 편집하며
개발하려면 그 소스를 로컬 `path`로 바꾸면 된다(pyproject 안 주석 참고). 임베딩 모델은
`rag`가 최초 1회 로컬로 내려받는다.

Claude 호출 백엔드(기본: 로컬 `claude` CLI)는 API 키 없이 동작하지만, 그 경우 `claude`
CLI가 설치·로그인돼 있어야 한다. 자세한 백엔드 설명은 아래 참고.

## 사용법

### 명령줄

```bash
uv run python -m adaptive_rag --source <폴더 경로> --query "질문" [--model <모델>] [--k <개수>]
```

- `--source`: 인덱싱·검색할 폴더 (아무 폴더나 가능)
- `--query`: 답하고자 하는 질문
- `--model` (선택): LLM 노드가 쓸 Claude 모델. `claude-opus-5-5`(기본) ·
  `claude-sonnet-5-5` · `claude-haiku-4-5` · `claude-fable-5-1`
- `--k` (선택): 검색당 가져올 청크 수 (기본 5)
- `--embedding-model` (선택): `rag`의 임베딩 모델 교체 (예: 한국어 비중이 크면
  `paraphrase-multilingual-MiniLM-L12-v2`)

`ANTHROPIC_API_KEY` 환경 변수가 필요하다. 그래프의 평가·생성 노드가 모두 Claude를 호출한다.

### 웹 대시보드 (로컬, 무료, 다기기 접속)

그래프 실행을 눈으로 보고 싶으면 로컬 웹 대시보드를 띄운다. 외부 서비스·계정·요금 없이
파이썬 표준 라이브러리만으로 동작한다.

```bash
uv run python -m adaptive_rag.web --source <폴더 경로> [--host 0.0.0.0] [--port 8000]
```

- `http://127.0.0.1:8000` 을 브라우저에서 연다.
- `--host 0.0.0.0` 으로 띄우면 같은 네트워크의 **아이폰 사파리·윈도우 브라우저** 등
  다른 기기에서 접속할 수 있다(서버가 접속용 LAN 주소를 함께 출력한다).
- 화면 구성:
  - **왼쪽 메뉴**: 섹션 이동 링크와 **설정**(Claude 모델 선택, 검색 문서 수 k), 그래프 범례.
  - **그래프 구조**: LangGraph 프로세스를 다이어그램으로 그리고, 실행 중인 노드가
    실시간으로 강조된다(실행 중 → 지나온 노드 → 미실행).
  - **실행 흐름**: 각 노드(검색 → 문서 평가 → 질의 재작성 → 답변 생성)의 결과가
    Server-Sent Events로 카드로 쌓인다(걸러진 문서 수, 재작성된 검색어, 재생성 횟수 등).
  - **답변**: 최종 답변 + 근거 파일 + 사용 모델·재작성·생성 통계.
- API 키가 없으면 답변 영역에 **무엇을 어떻게 설정해야 하는지 안내 메시지**가 뜬다.

주의: `--host 0.0.0.0` 은 인증 없이 네트워크의 모든 기기에 대시보드를 노출한다.
신뢰할 수 있는 네트워크에서만 사용한다.

### Python에서 직접

```python
from rag import RAGIndex
from adaptive_rag import build_graph

index = RAGIndex(source_dir="/path/to/folder")
index.refresh()

graph = build_graph(index)
result = graph.invoke({"question": "찾고 싶은 내용"})

print(result["generation"])              # 답변
for c in result["documents"]:            # 답변의 근거가 된 청크
    print(c.relative_path)
```

## LLM 백엔드 — 3가지 (실행 시 자동 선택)

LLM 호출 백엔드는 다음 우선순위로 정해진다. `--backend`(또는 `ADAPTIVE_RAG_BACKEND`)로
명시하면 그게 최우선이다.

1. **`claude-cli` — 로컬 `claude` CLI 직접 호출 (추천, API 키 불필요)**
   도구가 매 호출마다 `claude -p --model <선택 모델>`을 직접 부른다(구독 인증). **대시보드
   드롭다운/CLI `--model`로 고른 모델이 요청마다 그대로 반영**된다(게이트웨이 재시작 불필요).
   모델은 별칭 `opus`/`sonnet`/`haiku`를 쓴다.
   ```bash
   uv run python -m adaptive_rag.web --source <폴더> --host 0.0.0.0 --backend claude-cli
   # 또는 API 키·LLM_BASE_URL 이 없으면 --backend 없이도 자동으로 claude-cli 가 된다.
   ```

2. **`gateway` — 외부 OpenAI 호환 프록시 (선택 · `LLM_BASE_URL` 설정 시, API 키 불필요)**
   `/v1/chat/completions`를 여는 **별도 프로그램**(이 저장소에는 포함되지 않음 — 예: 다른
   프로젝트의 `cli_gateway.py`)으로 보낸다. 이 경우 **모델은 게이트웨이 실행 옵션으로 고정**되어
   대시보드 선택은 무시된다. 대부분 `claude-cli`로 충분하므로 필수는 아니다.
   ```bash
   # 터미널 1: 별도 게이트웨이 프로그램을 직접 실행 (이 저장소 밖)
   #   예: python3.11 /path/to/cli_gateway.py --backend claude --port 8765 --model sonnet
   # 터미널 2:
   export LLM_BASE_URL=http://127.0.0.1:8765/v1
   uv run python -m adaptive_rag.web --source <폴더> --host 0.0.0.0
   ```

3. **`api` — Anthropic API (`ANTHROPIC_API_KEY` 필요)**
   `anthropic` SDK로 Claude API 호출. 평가 노드는 구조화 출력(structured outputs)으로
   정확히 파싱한다. 모델은 전체 ID(`claude-opus-5-5` 등).

자동 선택 순서: 명시(`--backend`) → `LLM_BASE_URL` 있으면 gateway → `ANTHROPIC_API_KEY`
있으면 api → 로컬 `claude` 있으면 claude-cli → api. `anthropic` SDK는 api 경로에서만 지연
import되므로 나머지 두 경로는 SDK도 키도 필요 없다. 텍스트 백엔드(gateway/claude-cli)는
구조화 출력이 없어 평가 노드가 "yes/no" 프롬프트 + 관대한 파싱으로 대체된다.

## 구성

- **LLM 모델**: 기본 `claude-opus-5-5` (문서 관련성 평가, 질의 재작성, 답변 생성, 답변 검증).
  `rag` 라이브러리가 자체 답변 생성에 쓰는 모델과 동일하게 맞춰 두 프로젝트가 어긋나지 않게 했다.
  실행할 때마다 CLI `--model` 또는 대시보드 드롭다운으로 바꿀 수 있다(후보는
  `config.py`의 `AVAILABLE_MODELS`). 모델·검색 문서 수(k)는 그래프 상태로 전달되므로
  서버가 여러 요청을 동시에 처리해도 요청별로 독립적이다.
- **평가 노드**는 Anthropic 구조화 출력(structured outputs, `output_config.format`)으로
  boolean JSON을 받아 파싱 오류 없이 분기한다.
- 재시도 상한과 검색 청크 수 등 모든 튜닝 값은 `src/adaptive_rag/config.py`에 모여 있다.

## 프로젝트 구조

```
src/adaptive_rag/
├── __init__.py     # 공개 API: build_graph
├── __main__.py     # CLI (python -m adaptive_rag)
├── config.py       # 모델·재시도 예산 등 기본 설정값
├── state.py        # GraphState (노드 간 전달되는 상태 TypedDict)
├── llm.py          # Claude 호출 래퍼 (평가/재작성/생성/검증)
├── nodes.py        # 그래프 노드 + 조건부 분기 함수
├── graph.py        # StateGraph 조립 (build_graph)
└── web.py          # 로컬 웹 대시보드 (표준 라이브러리만, python -m adaptive_rag.web)
tests/test_graph.py # 오프라인 그래프 흐름 테스트 (LLM·인덱스를 스텁 처리)
tests/test_web.py   # 대시보드 HTTP+SSE 경로 오프라인 테스트
```

## 테스트

```bash
uv run pytest -q
```

테스트는 `llm.*` 함수와 인덱스를 스텁으로 대체해 **API 키 없이 오프라인으로** 그래프의
제어 흐름(관련성 필터 → 재작성 루프, 환각 재생성, 종료 보장)을 검증한다.

## 관련 프로젝트

- [`hustudy1/RAG`](https://github.com/hustudy1/RAG): 이 프로젝트가 검색 도구로 import하는 RAG 라이브러리(git 의존성).
- 설계 배경·미해결 항목·다음 할 일은 `llm_wiki/HANDOVER.md`에 기록되어 있다.
