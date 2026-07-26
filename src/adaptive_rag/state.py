"""Graph state passed between nodes.

LangGraph threads a single mutable state dict through the graph; each node
returns a partial dict that is merged into it. This TypedDict documents every
key the Adaptive RAG graph reads or writes.
"""

from __future__ import annotations

from typing import TypedDict

from rag import Chunk


class GraphState(TypedDict, total=False):
    """State for the Corrective/Adaptive RAG graph.

    total=False because nodes populate keys incrementally -- the initial input
    only carries `question`, and later nodes fill in the rest.
    """

    # The user's original question. Never mutated after the initial input.
    question: str

    # The query actually used for retrieval. Starts equal to `question` and is
    # replaced by transform_query when a rewrite is needed.
    query: str

    # Chunks kept after relevance grading. What generate() answers from.
    documents: list[Chunk]

    # The generated answer text.
    generation: str

    # How many times transform_query has run (bounds the retrieval loop).
    rewrites: int

    # How many times generate has run for the current documents (bounds the
    # generation loop).
    generations: int

    # Claude model id for this run's LLM nodes. Optional in the input; nodes
    # fall back to config.ANSWER_MODEL when absent.
    model: str

    # Chunks to retrieve per search. Optional; falls back to config.RETRIEVE_K.
    k: int
