"""Assembles the Corrective/Adaptive RAG graph.

Topology:

    START -> retrieve -> grade_documents
                              |
              (relevant?)  ---+---  no, budget left --> transform_query --+
                              |                                           |
                              | yes / budget spent                        |
                              v                                           |
                          generate <---------------------------------+   |
                              |                                       |   |
                        grade_generation                             |   |
                              |                                       |   |
        not grounded (retry) -+---> generate --------------------------   |
        not useful (rewrite) -+---> transform_query --------------------- +
        useful ---------------+---> END

Both loops are bounded by the retry budgets in `config`, so the graph always
terminates with an answer.
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph
from rag import RAGIndex

from adaptive_rag import nodes
from adaptive_rag.state import GraphState


def build_graph(index: RAGIndex):
    """Build and compile the Adaptive RAG graph bound to `index`.

    Returns a compiled LangGraph runnable; call `.invoke({"question": ...})`.
    """
    builder = StateGraph(GraphState)

    builder.add_node("retrieve", partial(nodes.retrieve, index=index))
    builder.add_node("grade_documents", nodes.grade_documents)
    builder.add_node("transform_query", nodes.transform_query)
    builder.add_node("generate", nodes.generate)

    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "grade_documents")
    builder.add_conditional_edges(
        "grade_documents",
        nodes.decide_to_generate,
        {
            "generate": "generate",
            "transform_query": "transform_query",
        },
    )
    builder.add_edge("transform_query", "retrieve")
    builder.add_conditional_edges(
        "generate",
        nodes.grade_generation,
        {
            "not_grounded": "generate",
            "not_useful": "transform_query",
            "useful": END,
        },
    )

    return builder.compile()
