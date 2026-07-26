"""Graph nodes and conditional-edge decision functions.

Nodes are pure-ish functions of the state that return a partial state update.
The retrieval nodes need a `RAGIndex`, which is bound in by `graph.build_graph`
via functools.partial so the node signatures stay `(state) -> update` the way
LangGraph expects. All Claude calls go through `adaptive_rag.llm`.
"""

from __future__ import annotations

from rag import RAGIndex

from adaptive_rag import llm
from adaptive_rag.config import (
    ANSWER_MODEL,
    MAX_GENERATIONS,
    MAX_QUERY_REWRITES,
    RETRIEVE_K,
)
from adaptive_rag.state import GraphState


def _model(state: GraphState) -> str:
    """Model for this run, falling back to the configured default."""
    return state.get("model") or ANSWER_MODEL


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def retrieve(state: GraphState, index: RAGIndex) -> GraphState:
    """Search the index for the current query and store the raw chunks."""
    query = state.get("query") or state["question"]
    documents = index.search(query, k=state.get("k") or RETRIEVE_K)
    return {"query": query, "documents": documents}


def grade_documents(state: GraphState) -> GraphState:
    """Drop chunks that a grader judges irrelevant to the original question."""
    question = state["question"]
    model = _model(state)
    relevant = [c for c in state["documents"] if llm.grade_document(question, c, model)]
    return {"documents": relevant}


def transform_query(state: GraphState) -> GraphState:
    """Rewrite the question into a better search query and count the attempt."""
    new_query = llm.rewrite_query(state["question"], _model(state))
    return {"query": new_query, "rewrites": state.get("rewrites", 0) + 1}


def generate(state: GraphState) -> GraphState:
    """Generate a grounded answer from the current documents."""
    generation = llm.generate_answer(state["question"], state["documents"], _model(state))
    return {
        "generation": generation,
        "generations": state.get("generations", 0) + 1,
    }


# ---------------------------------------------------------------------------
# Conditional-edge decision functions
# ---------------------------------------------------------------------------


def decide_to_generate(state: GraphState) -> str:
    """After grading, choose between answering and rewriting the query.

    - If any relevant documents survived, generate.
    - If none survived but we still have rewrite budget, rewrite and retry.
    - If none survived and the budget is spent, generate anyway (so the graph
      always terminates with an answer rather than looping forever).
    """
    if state["documents"]:
        return "generate"
    if state.get("rewrites", 0) < MAX_QUERY_REWRITES:
        return "transform_query"
    return "generate"


def grade_generation(state: GraphState) -> str:
    """Grade the generated answer and route accordingly.

    Two independent checks:
    - grounded: is the answer supported by the documents (not hallucinated)?
    - addresses: does the answer actually resolve the question?

    Routing (all bounded so the graph always terminates):
    - not grounded, generations left  -> regenerate from the same documents
    - grounded but off-topic, rewrites left -> rewrite the query and re-retrieve
    - otherwise (useful, or budgets spent) -> accept and end
    """
    documents = state["documents"]
    generation = state["generation"]
    model = _model(state)

    if documents and not llm.answer_is_grounded(documents, generation, model):
        if state.get("generations", 0) < MAX_GENERATIONS:
            return "not_grounded"
        return "useful"

    if not llm.answer_addresses_question(state["question"], generation, model):
        if state.get("rewrites", 0) < MAX_QUERY_REWRITES:
            return "not_useful"
        return "useful"

    return "useful"
