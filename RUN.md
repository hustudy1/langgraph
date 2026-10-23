# 실행 방법 (Quick Start)

## ✅ 이것만 하면 됩니다

```bash
uv sync                       # 처음 한 번만 (환경 준비)
uv run python dashboard.py    # 대시보드 실행
```

→ 브라우저에서 **http://127.0.0.1:8000** 열기 → 화면 **위쪽 메뉴**에서 모드를 고르고 질문 입력.

- **🔁 검증 루프** — 답을 생성한 뒤 스스로 비평·재생성해서 더 나은 답을 냄 (검색 없음)
- **🔍 RAG + 검증** — 폴더의 문서를 검색해 근거 기반으로 답하고 검증 (기본 폴더 `llm_wiki`)

끄기: 터미널에서 `Ctrl + C`. **API 키 필요 없음** — 로컬 `claude` CLI(구독 로그인)로 동작합니다.

> 실행은 클론한 폴더(`langgraph`) **안에서**. 이미 그 안이면 그대로, 아니면 `cd langgraph` 먼저.
> 다른 기기(폰 등)에서도 볼 수 있습니다 — 켤 때 같이 출력되는 `http://192.168.x.x:8000` 으로 접속(같은 와이파이).

## 옵션 (필요할 때만)

| 하고 싶은 것 | 명령 |
|---|---|
| 검증 루프만 (빠름, 검색 안 함) | `uv run python dashboard.py --loop` |
| 다른 폴더를 RAG로 검색 | `uv run python dashboard.py --source <폴더>` |
| 포트 바꾸기 | `uv run python dashboard.py --port 8123` |
| 시작 모델 지정 | `uv run python dashboard.py --model opus` (opus/sonnet/haiku) |
| 이 기기에서만 (외부 접속 차단) | `uv run python dashboard.py --local` |

> 모델은 실행 후 화면 드롭다운에서 언제든 바꿀 수 있습니다.

## 막히면

| 증상 | 해결 |
|---|---|
| `Address already in use` | 대시보드가 이미 켜져 있음 → `Ctrl+C`로 끄거나 `--port 8123` |
| `claude CLI 를 찾을 수 없습니다` | `claude --version` 확인, 안 되면 `claude` 로그인 |
| 첫 실행이 느림 | 검색 모델을 최초 1회 다운로드(이후 빠름) |
| 다른 기기 접속 안 됨 | 같은 와이파이 + 맥 `시스템 설정 → 네트워크 → 방화벽`에서 파이썬 허용 |

## 그 밖에 (고급)

- **대시보드 없이 터미널에서 한 번만 질문**:
  `uv run python -m adaptive_rag --source llm_wiki --query "커밋 전에 무엇을 확인해야 하나요?"`
- **API 키(Anthropic API)로 실행**: `export ANTHROPIC_API_KEY="sk-ant-..."` 후 위 명령 실행 (그러면 claude CLI 대신 API 사용)
- 옵션 전체·구조 설명은 `GUIDE.md`, `README.md` 참고.
