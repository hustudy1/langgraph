"""adaptive_rag -- a LangGraph Corrective/Adaptive RAG graph over the local
`rag` retrieval library.

    from rag import RAGIndex
    from adaptive_rag import build_graph

    index = RAGIndex(source_dir="/path/to/folder")
    index.refresh()
    graph = build_graph(index)
    result = graph.invoke({"question": "..."})
    print(result["generation"])

The graph retrieves with `rag`, grades document relevance, rewrites the query
and retries when nothing relevant is found, generates a grounded answer, and
checks that answer for hallucination and relevance before returning. Every LLM
node calls Claude; `ANTHROPIC_API_KEY` must be set to invoke the graph.
"""

from adaptive_rag.graph import build_graph

__version__ = "0.1.0"

__all__ = ["build_graph", "__version__"]
