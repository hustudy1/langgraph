#!/usr/bin/env python3
"""Minimal LangGraph example — self-reflection loop, **RAG 없음**.

이 예제는 검색(RAG) 없이 LangGraph의 핵심만 보여준다:

  - **State**   : 노드 사이를 흐르는 공유 데이터 (TypedDict)
  - **Node**    : state를 받아 갱신해 반환하는 함수 (여기선 Claude 호출)
  - **조건부 엣지**: 비평 결과에 따라 '다시 고쳐써(revise)' vs '끝(stop)'
  - **Loop**    : generate → critique → (부족하면) generate … (상한으로 항상 종료)

패턴은 self-reflection: 초안 생성 → 스스로 비평 → 부족하면 개선 재생성.
"여러 번 검증해 더 좋은 답을 찾는" 바로 그 방법론이며, 검색과는 무관하다.

그래프 구조:

    START → generate → critique ──(조건부)──┐
              ▲                    revise    │ stop
              └────────────────────┘         └──→ END

LLM 호출은 프로젝트의 `adaptive_rag.llm` 백엔드를 그대로 재사용한다
(claude-cli 기본 → API 키 불필요, 요청별 모델 선택 가능).

실행:
    uv run python examples/self_reflection.py "3문장으로 파이썬 데코레이터를 설명해줘"
    uv run python examples/self_reflection.py --model opus "재귀를 초등학생에게 설명해줘"
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from adaptive_rag import llm

# 생성<->비평 루프의 상한. 비평이 계속 REVISE여도 여기서 멈춰 항상 종료한다.
MAX_DRAFTS = 3


class ReflectionState(TypedDict, total=False):
    """노드 사이를 흐르는 공유 상태."""

    task: str  # 사용자가 요청한 것 (원본, 바뀌지 않음)
    draft: str  # 현재 초안/개선본
    critique: str  # 마지막 비평 텍스트
    verdict: str  # "GOOD" | "REVISE"
    drafts: int  # generate 실행 횟수 (상한 체크용)
    model: str  # 작성자(generate) 모델 (없으면 백엔드 기본값)
    critic_model: str  # 검토자(critique) 모델 (없으면 작성자와 동일). "다른 모델로 검증"용.


def _model(state: ReflectionState) -> str | None:
    """작성자(generate) 모델."""
    return state.get("model")


def _critic_model(state: ReflectionState) -> str | None:
    """검토자(critique) 모델. 없으면 작성자 모델과 같게(= 동일 모델 자기검증).

    작성자와 다른/더 센 모델을 주면 맹점이 달라져 "LLM이 LLM을 검증"이 실제로
    의미 있어진다 — 두 명의 팀원(작성자 · 검토자)을 두는 셈.
    """
    return state.get("critic_model") or state.get("model")


# --- Nodes: 일하는 함수 (state -> 갱신) ---------------------------------------


def generate(state: ReflectionState) -> ReflectionState:
    """첫 회면 초안을, 이후엔 비평을 반영한 개선본을 만든다."""
    task = state["task"]
    if not state.get("draft"):
        prompt = f"다음 요청에 대한 답의 초안을 간결하게 써라.\n\n요청: {task}"
    else:
        prompt = (
            "아래 초안을 리뷰어 지적에 맞춰 더 낫게 고쳐 써라. "
            "개선된 답만 출력하고 다른 말은 붙이지 마라.\n\n"
            f"요청: {task}\n\n초안:\n{state['draft']}\n\n리뷰어 지적:\n{state.get('critique', '')}"
        )
    draft = llm.complete(prompt, _model(state)).strip()
    return {"draft": draft, "drafts": state.get("drafts", 0) + 1}


def critique(state: ReflectionState) -> ReflectionState:
    """초안을 스스로 비평하고 GOOD/REVISE 판정을 내린다."""
    prompt = (
        "너는 엄격한 리뷰어다. 아래 답이 요청을 정확하고 명확하게 충족하는지 평가하라. "
        "문제가 있으면 구체적으로 지적하라. 마지막 줄에는 정확히 "
        "'VERDICT: GOOD'(충분함) 또는 'VERDICT: REVISE'(개선 필요) 중 하나만 써라.\n\n"
        f"요청: {state['task']}\n\n답:\n{state.get('draft', '')}"
    )
    text = llm.complete(prompt, _critic_model(state)).strip()
    m = re.search(r"VERDICT:\s*(GOOD|REVISE)", text.upper())
    verdict = m.group(1) if m else "GOOD"  # 못 읽으면 안전하게 종료(GOOD)
    return {"critique": text, "verdict": verdict}


# --- Conditional edge: 다음에 뭘 할지 정하는 라우터 ----------------------------


def should_continue(state: ReflectionState) -> str:
    """판정과 상한을 보고 계속 고칠지(revise)/끝낼지(stop) 정한다."""
    if state.get("verdict") == "GOOD":
        return "stop"
    if state.get("drafts", 0) >= MAX_DRAFTS:
        return "stop"  # 예산 소진 → 무한 루프 방지
    return "revise"


def build_reflection_graph():
    """generate <-> critique 루프를 도는 컴파일된 LangGraph를 만든다."""
    builder = StateGraph(ReflectionState)
    builder.add_node("generate", generate)
    builder.add_node("critique", critique)
    builder.add_edge(START, "generate")
    builder.add_edge("generate", "critique")
    builder.add_conditional_edges(
        "critique", should_continue, {"revise": "generate", "stop": END}
    )
    return builder.compile()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python examples/self_reflection.py",
        description="RAG 없는 LangGraph 자기검토 루프 예제.",
    )
    parser.add_argument(
        "task",
        nargs="?",
        default="재귀(recursion)를 3문장으로 쉽게 설명해줘",
        help="AI에게 시킬 작업/질문.",
    )
    parser.add_argument(
        "--model", default=None, help="작성자(generate) 모델 (claude-cli: opus/sonnet/haiku)."
    )
    parser.add_argument(
        "--critic-model",
        default=None,
        help="검토자(critique) 모델. 지정 시 작성자와 다른 모델로 검증(팀 구성). 생략 시 작성자와 동일.",
    )
    args = parser.parse_args(argv)

    graph = build_reflection_graph()
    print(f"백엔드: {llm.backend_description()}", file=sys.stderr)
    print(f"작업: {args.task}\n", file=sys.stderr)

    inputs: ReflectionState = {"task": args.task}
    if args.model:
        inputs["model"] = args.model
    if args.critic_model:
        inputs["critic_model"] = args.critic_model

    # stream_mode="updates": 노드가 하나 끝날 때마다 그 결과(delta)를 받아 출력한다.
    final: ReflectionState = {}
    step = 0
    for update in graph.stream(inputs, stream_mode="updates"):
        for node, delta in update.items():
            final.update(delta)
            step += 1
            if node == "generate":
                print(f"[{step}] ✍️  초안 #{delta['drafts']}")
                print(delta["draft"] + "\n")
            elif node == "critique":
                print(f"[{step}] 🔎 비평 → 판정 {delta['verdict']}")
                print(delta["critique"] + "\n")

    print("=" * 60)
    print("✅ 최종 답:\n")
    print(final.get("draft", "(없음)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
