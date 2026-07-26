"""Command-line entry point: index a folder and answer one question.

    uv run python -m adaptive_rag --source <folder> --query "<question>"

This mirrors `rag`'s scripts/quickstart.py: it builds/refreshes the index over
the given folder, runs the Adaptive RAG graph once, and prints the answer plus
the sources it was grounded in. Requires ANTHROPIC_API_KEY (the graph's grading
and generation nodes call Claude).
"""

from __future__ import annotations

import argparse
import os
import sys

from rag import RAGIndex

from adaptive_rag import build_graph, llm
from adaptive_rag.config import ANSWER_MODEL, DEFAULT_CLI_MODEL, RETRIEVE_K


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m adaptive_rag",
        description="Answer a question over a folder using Adaptive RAG.",
    )
    parser.add_argument(
        "--source", required=True, help="Path to the folder to index and search."
    )
    parser.add_argument("--query", required=True, help="The question to answer.")
    parser.add_argument(
        "--model",
        default=None,
        help="LLM 노드가 쓸 모델. api 백엔드는 claude-opus-4-8 같은 전체 ID, "
        "claude-cli 백엔드는 opus/sonnet/haiku 별칭. 생략 시 백엔드 기본값.",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "api", "claude-cli"),
        default="auto",
        help="LLM 백엔드 (auto/api/claude-cli). 게이트웨이는 LLM_BASE_URL 로 자동 감지.",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=RETRIEVE_K,
        help=f"Chunks to retrieve per search (default: {RETRIEVE_K}).",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="Override the rag embedding model (e.g. a multilingual model).",
    )
    args = parser.parse_args(argv)

    if args.backend != "auto":
        os.environ["ADAPTIVE_RAG_BACKEND"] = args.backend

    kwargs = {"source_dir": args.source}
    if args.embedding_model:
        kwargs["embedding_model"] = args.embedding_model
    index = RAGIndex(**kwargs)

    print(f"Indexing {args.source} ...", file=sys.stderr)
    stats = index.refresh()
    print(
        f"  added={stats.added} updated={stats.updated} deleted={stats.deleted} "
        f"unchanged={stats.unchanged} total_chunks={stats.total_chunks}",
        file=sys.stderr,
    )

    graph = build_graph(index)
    backend = llm.active_backend()
    print(f"백엔드: {llm.backend_description()}", file=sys.stderr)

    if args.model:
        model = args.model
    elif backend == "claude-cli":
        model = DEFAULT_CLI_MODEL
    else:
        model = ANSWER_MODEL
    if backend != "gateway":
        print(f"모델: {model}", file=sys.stderr)

    result = graph.invoke({"question": args.query, "model": model, "k": args.k})

    print("\n=== Answer ===")
    print(result.get("generation", "(no answer generated)"))

    documents = result.get("documents", [])
    if documents:
        sources = sorted({c.relative_path for c in documents})
        print("\n=== Grounded in ===")
        for path in sources:
            print(f"  - {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
