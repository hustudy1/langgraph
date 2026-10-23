#!/usr/bin/env python3
"""대시보드 간편 실행기 (one-command launcher).

긴 명령을 외우지 않고 대시보드를 띄운다. 뜬 화면 상단 메뉴에서 모드를 고른다:
  - 🔁 검증 루프 (RAG 없음): 생성 → 자기 비평 → 재생성
  - 🔍 RAG + 검증: 문서 검색 + 근거 기반 답 + 검증  (--source 폴더가 있을 때 활성화)

저장소 루트에서:

    uv run python dashboard.py                  # RAG(폴더 llm_wiki) + 검증 루프, 메뉴로 전환
    uv run python dashboard.py --source docs    # RAG 검색 폴더 지정
    uv run python dashboard.py --loop           # 검증 루프만 (RAG 비활성)
    uv run python dashboard.py --model opus     # 시작 모델 지정
    uv run python dashboard.py --local          # 이 기기에서만(외부 접속 차단)
    uv run python dashboard.py --port 8123      # 포트 변경

기본값: --source llm_wiki, --host 0.0.0.0(다른 기기 접속 허용), --backend claude-cli
(로컬 claude CLI, API 키 불필요). 실제 서버는 examples/menu_web.py 이며, 이 파일은
편의 기본값을 채워 넘기는 얇은 래퍼다.
"""

from __future__ import annotations

import argparse

DEFAULT_SOURCE = "llm_wiki"
DEFAULT_PORT = 8000
DEFAULT_BACKEND = "claude-cli"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python dashboard.py",
        description="Adaptive RAG 대시보드 간편 실행기.",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"검색·인덱싱할 폴더 (기본: {DEFAULT_SOURCE}).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="시작 모델. claude-cli: opus/sonnet/haiku, api: 전체 ID. "
        "실행 후 대시보드 드롭다운에서 실시간 변경 가능.",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"포트 (기본: {DEFAULT_PORT})."
    )
    parser.add_argument(
        "--backend",
        default=DEFAULT_BACKEND,
        choices=("auto", "api", "claude-cli"),
        help=f"LLM 백엔드 (기본: {DEFAULT_BACKEND}).",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="이 기기에서만 접속(host=127.0.0.1). 기본은 0.0.0.0(다른 기기 허용).",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="검증 루프 모드만 띄운다(RAG 비활성). --source 를 넘기지 않는 것과 같다.",
    )
    args = parser.parse_args(argv)

    host = "127.0.0.1" if args.local else "0.0.0.0"
    common = ["--host", host, "--port", str(args.port), "--backend", args.backend]
    if args.model:
        common += ["--model", args.model]

    # 실제 서버는 메뉴가 있는 통합 대시보드(examples/menu_web.py).
    from examples.menu_web import main as menu_main

    if args.loop:
        # 검증 루프만: --source 를 넘기지 않으면 RAG 메뉴가 비활성된다.
        return menu_main(common)
    return menu_main(["--source", args.source, *common])


if __name__ == "__main__":
    raise SystemExit(main())
