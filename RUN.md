# How to Run (Quick Start)

복사해서 터미널에 붙여넣으면 된다. 아래 명령은 **저장소를 클론한 폴더 안**에서 실행한다.
(예시의 `adaptive-rag`는 `git clone` 후 생긴 폴더 이름이며, 저장소 이름에 따라 다를 수 있다.)

---

## 0. 준비 (처음 한 번만)

```bash
cd adaptive-rag
uv sync
```

> API 키가 없어도 된다. 로컬 `claude` CLI(구독 로그인)를 그대로 쓰는 방식이 기본이다.

---

## 방법 1 — 웹 대시보드 (추천)

```bash
cd adaptive-rag
uv run python -m adaptive_rag.web --source llm_wiki --host 0.0.0.0 --backend claude-cli
```

- `--source` 뒤는 **검색할 폴더**. 예시는 `llm_wiki`. 아무 폴더나 넣으면 된다.
- 켜지면 터미널에 주소가 뜬다:
  - 이 맥에서: 브라우저로 `http://127.0.0.1:8000`
  - **아이폰/윈도우**에서: 같이 출력되는 `http://192.168.0.xx:8000` (같은 와이파이)
- 화면 **왼쪽 드롭다운에서 모델(opus / sonnet / haiku)** 을 고르고, 질문을 입력한다.
  고른 모델이 질문할 때마다 바로 적용된다.
- **끄기**: 그 터미널에서 `Ctrl + C`.

---

## 방법 2 — 명령줄에서 한 번 질문

```bash
cd adaptive-rag
uv run python -m adaptive_rag \
  --source llm_wiki \
  --query "커밋 전에 무엇을 확인해야 하나요?" \
  --backend claude-cli --model sonnet
```

- `--query` 에 질문을 쓴다.
- `--model` 은 `opus` / `sonnet` / `haiku` 중 하나 (생략하면 `sonnet`).
- 답변과 근거 파일이 화면에 출력된다.

---

## 실행이 잘 됐는지 확인

시작할 때 이 줄이 보이면 정상이다 (괄호 안 `claude` 경로는 환경마다 다르다):

```
백엔드: 로컬 claude CLI 직접 호출 (.../claude) — API 키 불필요, 모델 요청별 선택
```

---

## 자주 막히는 것 (짧게)

| 증상 | 해결 |
|---|---|
| `Address already in use` (포트 충돌) | `--port 8123` 처럼 다른 번호를 붙인다 |
| 아이폰/윈도우에서 접속 안 됨 | 같은 와이파이인지 확인 + 맥 `시스템 설정 → 네트워크 → 방화벽`에서 파이썬 허용 |
| `claude CLI 를 찾을 수 없습니다` | 터미널에서 `claude --version` 이 되는지 확인. 안 되면 `claude` 로그인 필요 |
| 첫 실행이 느림 | 검색용 모델을 최초 1회 내려받는 중(인터넷 필요). 이후엔 빠르다 |

---

## (선택) API 키로 쓰고 싶다면

`claude` CLI 대신 Anthropic API 키를 쓰려면, 위 명령에서 `--backend claude-cli` 를 빼고
키만 넣으면 된다:

```bash
export ANTHROPIC_API_KEY="sk-ant-본인-키"
uv run python -m adaptive_rag.web --source llm_wiki --host 0.0.0.0
```

더 자세한 설명·트러블슈팅은 `GUIDE.md`, 옵션 전체는 `README.md` 참고.
