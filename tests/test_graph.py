"""Graph-flow tests that run fully offline.

The graph's LLM nodes (`adaptive_rag.llm.*`) and the retrieval node's index are
the only things that touch the network / an API key, so we stub both:
- a `FakeIndex` returns canned chunks per query, and
- `llm.*` functions are monkeypatched to deterministic behaviour.

This lets us assert the control flow (grading -> rewrite loop, hallucination
retry, termination) without downloading an embedding model or calling Claude.
"""

from __future__ import annotations

import pytest
from rag import Chunk

from adaptive_rag import build_graph
from adaptive_rag import llm, nodes


def make_chunk(text: str, path: str = "doc.md") -> Chunk:
    return Chunk(
        text=text,
        source_path=f"/src/{path}",
        relative_path=path,
        chunk_index=0,
        score=0.9,
    )


class FakeIndex:
    """Stands in for RAGIndex; returns preset chunks keyed by query."""

    def __init__(self, results_by_query: dict[str, list[Chunk]], default: list[Chunk]):
        self.results_by_query = results_by_query
        self.default = default
        self.searches: list[str] = []
        self.ks: list[int] = []

    def search(self, query: str, k: int = 5) -> list[Chunk]:
        self.searches.append(query)
        self.ks.append(k)
        return self.results_by_query.get(query, self.default)


@pytest.fixture
def stub_llm(monkeypatch):
    """Install default (all-happy) LLM stubs; tests override individual ones.

    Stubs take the same (…, model) signature as the real llm.* functions.
    """
    monkeypatch.setattr(llm, "grade_document", lambda q, c, m=None: True)
    monkeypatch.setattr(llm, "rewrite_query", lambda q, m=None: f"rewritten: {q}")
    monkeypatch.setattr(llm, "generate_answer", lambda q, chunks, m=None: "an answer")
    monkeypatch.setattr(llm, "answer_is_grounded", lambda chunks, gen, m=None: True)
    monkeypatch.setattr(llm, "answer_addresses_question", lambda q, gen, m=None: True)
    return monkeypatch


def test_happy_path_single_pass(stub_llm):
    """Relevant docs + grounded + on-topic answer => one retrieve, one generate."""
    chunk = make_chunk("relevant text")
    index = FakeIndex({}, default=[chunk])

    graph = build_graph(index)
    result = graph.invoke({"question": "what is X?"})

    assert result["generation"] == "an answer"
    assert result["documents"] == [chunk]
    assert index.searches == ["what is X?"]  # no rewrite loop
    assert result.get("rewrites", 0) == 0
    assert result["generations"] == 1


def test_irrelevant_docs_trigger_rewrite_then_succeed(stub_llm):
    """No relevant docs first -> rewrite -> retrieve again -> answer."""
    good = make_chunk("now relevant")
    index = FakeIndex(
        {"rewritten: q": [good]},  # second search (after rewrite) returns a good chunk
        default=[make_chunk("junk")],
    )

    # First pass: grade everything irrelevant; second pass (the 'now relevant'
    # chunk): relevant. Distinguish by text.
    stub_llm.setattr(
        llm, "grade_document", lambda q, c, m=None: c.text == "now relevant"
    )

    graph = build_graph(index)
    result = graph.invoke({"question": "q"})

    assert index.searches == ["q", "rewritten: q"]  # retrieved twice
    assert result["rewrites"] == 1
    assert result["documents"] == [good]
    assert result["generation"] == "an answer"


def test_hallucinated_answer_is_regenerated(stub_llm):
    """First generation ungrounded -> regenerate; second grounded -> accept."""
    index = FakeIndex({}, default=[make_chunk("relevant text")])

    calls = {"generate": 0, "grounded": 0}

    def fake_generate(question, chunks, model=None):
        calls["generate"] += 1
        return f"attempt {calls['generate']}"

    def fake_grounded(chunks, generation, model=None):
        calls["grounded"] += 1
        return calls["grounded"] > 1  # first check fails, second passes

    stub_llm.setattr(llm, "generate_answer", fake_generate)
    stub_llm.setattr(llm, "answer_is_grounded", fake_grounded)

    graph = build_graph(index)
    result = graph.invoke({"question": "q"})

    assert calls["generate"] == 2
    assert result["generation"] == "attempt 2"
    assert result["generations"] == 2


def test_terminates_when_no_documents_ever_relevant(stub_llm):
    """If nothing is ever relevant, the graph still terminates with an answer."""
    index = FakeIndex({}, default=[make_chunk("junk")])
    stub_llm.setattr(llm, "grade_document", lambda q, c, m=None: False)

    graph = build_graph(index)
    result = graph.invoke({"question": "q"})

    # rewrites are bounded (MAX_QUERY_REWRITES=2): initial + 2 rewrites = 3 searches.
    assert len(index.searches) == 3
    assert result["documents"] == []
    assert result["generation"] == "an answer"  # generated anyway, ends cleanly


def test_model_and_k_propagate_from_input(stub_llm):
    """The model chosen at invoke time reaches the llm calls; k reaches search."""
    seen_models: list[str] = []
    stub_llm.setattr(
        llm,
        "generate_answer",
        lambda q, chunks, m=None: seen_models.append(m) or "an answer",
    )
    index = FakeIndex({}, default=[make_chunk("relevant text")])

    graph = build_graph(index)
    graph.invoke({"question": "q", "model": "claude-haiku-4-5", "k": 3})

    assert seen_models == ["claude-haiku-4-5"]  # generate saw the chosen model
    assert index.ks == [3]  # retrieve searched with the chosen k


def test_decide_to_generate_units():
    """Unit-check the branching without the full graph."""
    assert nodes.decide_to_generate({"documents": [make_chunk("x")]}) == "generate"
    assert (
        nodes.decide_to_generate({"documents": [], "rewrites": 0})
        == "transform_query"
    )
    assert nodes.decide_to_generate({"documents": [], "rewrites": 99}) == "generate"
