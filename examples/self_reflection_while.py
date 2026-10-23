#!/usr/bin/env python3
"""같은 자기검토 루프를 **LangGraph 없이 그냥 파이썬 while 로** 짠 버전.

`examples/self_reflection.py`(LangGraph 버전)와 결과는 같다. 목적은 비교:

  - 단순한 **단일 루프**는 이렇게 while 몇 줄로 충분하다 → LangGraph가 꼭 필요하진 않다.
  - LangGraph가 이기는 지점은 분기가 **여러 갈래**로 늘고(재생성/재작성/종료), state가
    공유·누적되며, 스트리밍·영속화·사람 개입·시각화가 필요해질 때다.

실행:
    uv run python examples/self_reflection_while.py "재귀를 3문장으로 설명해줘"
"""

from __future__ import annotations

import argparse
import re
import sys

from adaptive_rag import llm

MAX_DRAFTS = 3  # 루프 상한 (LangGraph 버전과 동일)


def reflect(task: str, model: str | None = None) -> str:
    """generate → critique → (부족하면) 다시 generate 를 while 로 직접 돈다."""
    draft = llm.complete(
        f"다음 요청에 대한 답의 초안을 간결하게 써라.\n\n요청: {task}", model
    ).strip()
    print(f"✍️  초안 #1\n{draft}\n", file=sys.stderr)

    drafts = 1
    while drafts < MAX_DRAFTS:
        crit = llm.complete(
            "너는 엄격한 리뷰어다. 아래 답이 요청을 정확·명확하게 충족하는지 평가하라. "
            "마지막 줄에 정확히 'VERDICT: GOOD' 또는 'VERDICT: REVISE' 만 써라.\n\n"
            f"요청: {task}\n\n답:\n{draft}",
            model,
        ).strip()
        m = re.search(r"VERDICT:\s*(GOOD|REVISE)", crit.upper())
        verdict = m.group(1) if m else "GOOD"
        print(f"🔎 비평 → {verdict}\n{crit}\n", file=sys.stderr)
        if verdict == "GOOD":
            break
        draft = llm.complete(
            "아래 초안을 리뷰어 지적에 맞춰 더 낫게 고쳐 써라. 개선된 답만 출력해라.\n\n"
            f"요청: {task}\n\n초안:\n{draft}\n\n리뷰어 지적:\n{crit}",
            model,
        ).strip()
        drafts += 1
        print(f"✍️  초안 #{drafts} (개선)\n{draft}\n", file=sys.stderr)
    return draft


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python examples/self_reflection_while.py",
        description="LangGraph 없이 while 로 짠 자기검토 루프 (비교용).",
    )
    p.add_argument("task", nargs="?", default="재귀(recursion)를 3문장으로 쉽게 설명해줘")
    p.add_argument("--model", default=None, help="모델 (claude-cli: opus/sonnet/haiku).")
    args = p.parse_args(argv)

    print(f"백엔드: {llm.backend_description()}\n", file=sys.stderr)
    answer = reflect(args.task, args.model)
    print("=" * 60)
    print("✅ 최종 답:\n")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
