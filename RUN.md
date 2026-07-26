# How to Run (Quick Start)

클론한 폴더 안에서 실행한다. **API 키 없이** 로컬 `claude` CLI(구독 로그인)로 동작한다.

## 0. 준비 (처음 한 번만)

```bash
cd langgraph
uv sync
```

> **폴더 이름 `langgraph`는?** `git clone`으로 내려받으면 생기는 폴더(= GitHub 저장소 이름
> `langgraph`)이자 실행 기준 위치(프로젝트 루트)다. 아래 모든 명령은 이 폴더 **안에서** 실행한다.
> 저장소를 다른 이름으로 만들었으면 그 이름으로 `cd` 한다. 이미 그 폴더 안이면 `cd`는 생략.
>
> 참고: **도구(파이썬 패키지) 이름은 `adaptive-rag`**, 실행 모듈은 `adaptive_rag`다 — 폴더/저장소
> 이름(`langgraph`)과는 별개이므로, `python -m adaptive_rag ...` 명령의 `adaptive_rag`는 그대로 쓴다.

## 1. 웹 대시보드 (추천)

```bash
uv run python -m adaptive_rag.web --source llm_wiki --host 0.0.0.0 --backend claude-cli
```

- `--source` = 검색할 폴더 · `--host 0.0.0.0` = 다른 기기 접속 허용 · `--port 8000`(기본)
- 접속: 이 맥 `http://127.0.0.1:8000` / 다른 기기 `http://<출력된 IP>:8000`(같은 와이파이)
- 왼쪽 드롭다운에서 모델(opus/sonnet/haiku) 선택 → 질문 · 끄기 `Ctrl+C`

## 2. 명령줄에서 한 번 질문

```bash
uv run python -m adaptive_rag --source llm_wiki --query "커밋 전에 무엇을 확인해야 하나요?" --backend claude-cli --model sonnet
```

- `--model` = opus / sonnet / haiku (생략 시 sonnet)

## (선택) API 키로 실행

`--backend claude-cli`를 빼고 키만 준다:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
uv run python -m adaptive_rag.web --source llm_wiki --host 0.0.0.0
```

## 막히면

| 증상 | 해결 |
|---|---|
| `Address already in use` | `--port 8123` 등 다른 포트 |
| 다른 기기에서 접속 안 됨 | 같은 와이파이 + 맥 방화벽에서 파이썬 허용 |
| `claude CLI 를 찾을 수 없습니다` | `claude --version` 확인, 안 되면 `claude` 로그인 |
| 첫 실행이 느림 | 검색 모델 최초 1회 다운로드(이후 빠름) |

> 정상이면 시작할 때 `백엔드: 로컬 claude CLI 직접 호출 ... — API 키 불필요` 줄이 뜬다.
> 자세한 설명은 `GUIDE.md`, 옵션 전체는 `README.md`.
