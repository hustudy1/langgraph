#!/usr/bin/env python3
"""멀티 에이전트 코딩 팀 (LangGraph) — AI들이 역할을 나눠 코딩하고 서로 검증한다.

코드는 **실행·테스트로 객관적 검증**이 되는 도메인이라, 검증 루프가 실제로 정확도를
올린다("LLM만 LLM 검증"과 달리 진짜 정답=테스트 통과가 있음).

팀(각 노드 = 팀원):
  - 🧪 테스터(write_tests)   : 요청을 보고 pytest 테스트를 먼저 작성 → 정답을 정의
  - ⌨️ 작성자(write_code)    : 테스트를 통과할 코드를 작성 (실패하면 오류를 받아 수정)
  - ▶️ 실행기(run_tests, 도구): 코드+테스트를 실제로 실행 → 통과/실패 판정 (객관적 검증)
  - 🔍 검토자(review)        : 다른 모델로 품질·엣지케이스 리뷰 (AI가 AI를 검증)

흐름:
    START → 테스트 작성 → 코드 작성 → 실행
              (실패 & 예산) → 코드 수정 → 실행 ...        [테스트 통과까지 루프]
              (통과)        → 검토 → (개선 요청 & 예산) → 코드 수정 → 실행 ...
                                   → (승인/예산 소진)     → END
`MAX_ATTEMPTS`로 항상 종료.

⚠️  보안: 이 파이프라인은 **LLM이 생성한 코드를 실제로 실행**한다(임시 폴더 + 타임아웃).
    로컬에서 신뢰할 수 있는 작업에만 쓰고, 민감한 환경에서는 컨테이너 등 격리된 샌드박스에서
    돌려라. 생성 코드는 네트워크·파일 삭제 등 무엇이든 할 수 있다.

실행:
    uv run python examples/code_team.py "문자열을 뒤집는 함수 reverse_str 을 만들어줘"
    uv run python examples/code_team.py --coder-model sonnet --reviewer-model opus "..."
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from adaptive_rag import llm

MAX_ATTEMPTS = 4  # 코드 작성(수정) 총 시도 상한 → 항상 종료
RUN_TIMEOUT = 30  # 생성 코드 실행 타임아웃(초)


class CodeTeamState(TypedDict, total=False):
    task: str  # 코딩 요청 (원본)
    tests: str  # 테스터가 작성한 pytest 코드 (solution 모듈을 import)
    code: str  # 작성자가 만든 solution.py 코드
    test_output: str  # 실행기 출력(마지막)
    passed: bool  # 테스트 통과 여부
    review: str  # 검토자 리뷰
    verdict: str  # "APPROVE" | "REVISE"
    attempts: int  # write_code 실행 횟수
    coder_model: str  # 작성자 모델
    reviewer_model: str  # 검토자 모델 (없으면 작성자와 동일)
    tester_model: str  # 테스터 모델 (없으면 작성자와 동일)


def _extract_code(text: str) -> str:
    """```python ...``` 코드블록을 뽑는다. 없으면 전체 텍스트."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def _coder_model(s: CodeTeamState) -> str | None:
    return s.get("coder_model")


def _reviewer_model(s: CodeTeamState) -> str | None:
    return s.get("reviewer_model") or s.get("coder_model")


def _tester_model(s: CodeTeamState) -> str | None:
    return s.get("tester_model") or s.get("coder_model")


# --- 팀원 노드 --------------------------------------------------------------


def write_tests(state: CodeTeamState) -> CodeTeamState:
    """🧪 테스터: 요청에 대한 pytest 테스트를 먼저 작성한다."""
    prompt = (
        "너는 테스터다. 아래 요청을 만족하는지 검증할 pytest 테스트를 작성하라. "
        "구현 파일은 `solution.py`이고, 거기서 `from solution import ...` 로 가져온다. "
        "정상 케이스와 엣지 케이스를 몇 개 담아라. **오직 파이썬 코드 블록 하나만** 출력하라.\n\n"
        f"요청: {state['task']}"
    )
    return {"tests": _extract_code(llm.complete(prompt, _tester_model(state)))}


def write_code(state: CodeTeamState) -> CodeTeamState:
    """⌨️ 작성자: 테스트를 통과할 코드를 작성/수정한다."""
    base = (
        "너는 코드 작성자다. 아래 요청과 테스트를 통과하는 파이썬 코드를 `solution.py` 내용으로 "
        "작성하라. **오직 파이썬 코드 블록 하나만** 출력하라(설명 금지).\n\n"
        f"요청: {state['task']}\n\n테스트(solution 을 import 함):\n{state.get('tests', '')}"
    )
    if state.get("attempts"):
        # 재시도: 직전 코드 + 실패 원인(테스트 출력) 또는 리뷰를 준다.
        extra = ""
        if not state.get("passed", False) and state.get("test_output"):
            extra += f"\n\n직전 코드는 테스트에 실패했다. 실행 결과:\n{state['test_output']}"
        if state.get("review"):
            extra += f"\n\n리뷰 지적:\n{state['review']}"
        base += f"\n\n직전 코드:\n{state.get('code', '')}{extra}\n\n지적을 반영해 고쳐라."
    code = _extract_code(llm.complete(base, _coder_model(state)))
    return {"code": code, "attempts": state.get("attempts", 0) + 1}


def run_tests(state: CodeTeamState) -> CodeTeamState:
    """▶️ 실행기(도구): 코드+테스트를 실제로 실행해 통과/실패를 판정한다(객관적 검증)."""
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "solution.py"), "w", encoding="utf-8") as f:
            f.write(state.get("code", ""))
        with open(os.path.join(d, "test_solution.py"), "w", encoding="utf-8") as f:
            f.write(state.get("tests", ""))
        try:
            p = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "test_solution.py"],
                cwd=d,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT,
            )
            output = (p.stdout + p.stderr).strip()[-3000:]
            passed = p.returncode == 0
        except subprocess.TimeoutExpired:
            output, passed = f"실행 타임아웃({RUN_TIMEOUT}s)", False
    return {"test_output": output, "passed": passed}


def review(state: CodeTeamState) -> CodeTeamState:
    """🔍 검토자(다른 모델): 통과한 코드를 품질·엣지케이스 관점에서 리뷰한다."""
    prompt = (
        "너는 코드 리뷰어다. 아래 코드는 테스트를 통과했다. 품질·가독성·놓친 엣지케이스·버그 "
        "가능성을 검토하라. 구체적 문제가 있으면 지적하고, 마지막 줄에 정확히 "
        "'VERDICT: APPROVE'(충분함) 또는 'VERDICT: REVISE'(개선 필요) 만 써라.\n\n"
        f"요청: {state['task']}\n\n코드:\n{state.get('code', '')}"
    )
    text = llm.complete(prompt, _reviewer_model(state)).strip()
    m = re.search(r"VERDICT:\s*(APPROVE|REVISE)", text.upper())
    verdict = m.group(1) if m else "APPROVE"
    return {"review": text, "verdict": verdict}


# --- 조건부 엣지(라우터) -----------------------------------------------------


def after_run(state: CodeTeamState) -> str:
    """실행 후: 통과면 검토로, 실패면 예산 내에서 수정, 예산 소진이면 종료."""
    if state.get("passed"):
        return "review"
    if state.get("attempts", 0) < MAX_ATTEMPTS:
        return "fix"
    return "end"  # 계속 실패 + 예산 소진 → 포기(마지막 상태로 종료)


def after_review(state: CodeTeamState) -> str:
    """검토 후: 개선 요청이고 예산 남으면 수정, 아니면 종료."""
    if state.get("verdict") == "REVISE" and state.get("attempts", 0) < MAX_ATTEMPTS:
        return "fix"
    return "end"


def build_code_team_graph():
    """멀티 에이전트 코딩 팀 그래프를 컴파일해 반환한다."""
    builder = StateGraph(CodeTeamState)
    builder.add_node("write_tests", write_tests)
    builder.add_node("write_code", write_code)
    builder.add_node("run_tests", run_tests)
    builder.add_node("review", review)

    builder.add_edge(START, "write_tests")
    builder.add_edge("write_tests", "write_code")
    builder.add_edge("write_code", "run_tests")
    builder.add_conditional_edges(
        "run_tests", after_run, {"fix": "write_code", "review": "review", "end": END}
    )
    builder.add_conditional_edges(
        "review", after_review, {"fix": "write_code", "end": END}
    )
    return builder.compile()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python examples/code_team.py",
        description="멀티 에이전트 코딩 팀 (작성·테스트·실행·검토).",
    )
    parser.add_argument(
        "task",
        nargs="?",
        default="정수 리스트에서 두 번째로 큰 값을 반환하는 함수 second_largest 를 만들어줘",
        help="코딩 요청.",
    )
    parser.add_argument("--coder-model", default=None, help="작성자 모델 (claude-cli: opus/sonnet/haiku).")
    parser.add_argument("--reviewer-model", default=None, help="검토자 모델 (없으면 작성자와 동일).")
    parser.add_argument("--tester-model", default=None, help="테스터 모델 (없으면 작성자와 동일).")
    args = parser.parse_args(argv)

    graph = build_code_team_graph()
    print(f"백엔드: {llm.backend_description()}", file=sys.stderr)
    print(f"요청: {args.task}\n", file=sys.stderr)

    inputs: CodeTeamState = {"task": args.task}
    if args.coder_model:
        inputs["coder_model"] = args.coder_model
    if args.reviewer_model:
        inputs["reviewer_model"] = args.reviewer_model
    if args.tester_model:
        inputs["tester_model"] = args.tester_model

    icons = {"write_tests": "🧪 테스터", "write_code": "⌨️ 작성자", "run_tests": "▶️ 실행기", "review": "🔍 검토자"}
    final: CodeTeamState = {}
    for update in graph.stream(inputs, stream_mode="updates"):
        for node, delta in update.items():
            final.update(delta)
            print(f"── {icons.get(node, node)} ──")
            if node == "write_tests":
                print(delta.get("tests", "") + "\n")
            elif node == "write_code":
                print(f"(시도 #{delta.get('attempts')})\n{delta.get('code', '')}\n")
            elif node == "run_tests":
                print(("✅ 테스트 통과" if delta.get("passed") else "❌ 테스트 실패"))
                print(delta.get("test_output", "") + "\n")
            elif node == "review":
                print(f"판정 {delta.get('verdict')}\n{delta.get('review', '')}\n")

    print("=" * 60)
    print(("✅ 완료 — 테스트 통과" if final.get("passed") else "⚠️ 미완료 — 테스트 미통과(예산 소진)"))
    print(f"\n최종 코드 (solution.py):\n{final.get('code', '(없음)')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
