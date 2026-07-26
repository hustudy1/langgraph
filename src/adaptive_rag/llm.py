"""Claude-backed helpers for the graph's LLM nodes.

Every call to Claude in this project goes through one of the functions here, so
model choice, prompt wording, and response parsing live in one file and the
graph nodes stay about control flow.

Two backends are supported and chosen automatically per call:

- **Anthropic API** (default): the official `anthropic` SDK against the Claude
  API. Grading uses structured outputs (boolean JSON) for exact parsing.
  Requires ANTHROPIC_API_KEY.
- **CLI gateway** (when `LLM_BASE_URL` is set): an OpenAI-compatible
  `/v1/chat/completions` proxy such as `cli_gateway.py`, which fronts the local
  `claude`/`codex` CLI using subscription auth -- no API key needed. The gateway
  returns plain text, so grading falls back to a "reply yes/no" prompt that we
  parse leniently. Set it up with:

      export LLM_BASE_URL=http://127.0.0.1:8765/v1

`anthropic` is imported lazily and only on the Anthropic path, so the gateway
path needs neither the SDK nor an API key.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request

from rag import Chunk

from adaptive_rag.config import (
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    GRADING_MAX_TOKENS,
)

# Shelling out to a CLI per call can be slow; give it generous headroom above
# the gateway's / CLI's own per-call timeout.
GATEWAY_TIMEOUT = 300
CLI_TIMEOUT = 300

_client = None
_claude_bin: str | None = None


# ---------------------------------------------------------------------------
# Backend selection
#
# Three backends, resolved per call (so env changes take effect without a
# restart) in this priority order:
#   1. explicit: ADAPTIVE_RAG_BACKEND in {api, gateway, claude-cli}
#   2. gateway:  LLM_BASE_URL / ADAPTIVE_RAG_LLM_BASE_URL set
#   3. api:      ANTHROPIC_API_KEY set
#   4. claude-cli: a local `claude` CLI is available
#   5. fallback: api
# ---------------------------------------------------------------------------


def _gateway_base() -> str:
    """Base URL of the OpenAI-compatible gateway, or "" if none configured."""
    base = os.environ.get("ADAPTIVE_RAG_LLM_BASE_URL") or os.environ.get("LLM_BASE_URL")
    return (base or "").rstrip("/")


def _find_claude() -> str | None:
    """Locate the local `claude` CLI (cached). None if not found."""
    global _claude_bin
    if _claude_bin is not None:
        return _claude_bin or None
    candidates = [
        os.environ.get("CLAUDE_CLI_PATH"),
        shutil.which("claude"),
        os.path.expanduser("~/.local/bin/claude"),
        os.path.expanduser("~/.claude/local/claude"),
    ]
    for cand in candidates:
        if cand and os.path.exists(cand):
            _claude_bin = cand
            return _claude_bin
    _claude_bin = ""  # cache "not found"
    return None


def active_backend() -> str:
    """The resolved backend id: 'api', 'gateway', or 'claude-cli'."""
    explicit = os.environ.get("ADAPTIVE_RAG_BACKEND", "").strip().lower()
    if explicit in {"api", "gateway", "claude-cli"}:
        return explicit
    if _gateway_base():
        return "gateway"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if _find_claude():
        return "claude-cli"
    return "api"


def using_gateway() -> bool:
    """Public: is the OpenAI-compatible CLI gateway the active backend?"""
    return active_backend() == "gateway"


def backend_description() -> str:
    """Public: one-line human description of the active LLM backend."""
    backend = active_backend()
    if backend == "gateway":
        return f"CLI 게이트웨이 ({_gateway_base()}) — API 키 불필요"
    if backend == "claude-cli":
        return f"로컬 claude CLI 직접 호출 ({_find_claude()}) — API 키 불필요, 모델 요청별 선택"
    return f"Anthropic API — 기본 모델 {ANSWER_MODEL}, ANTHROPIC_API_KEY 필요"


# ---------------------------------------------------------------------------
# Anthropic API backend
# ---------------------------------------------------------------------------


def _get_client():
    """Return a lazily-constructed, cached Anthropic client."""
    global _client
    if _client is None:
        import anthropic  # local import -- only needed on the Anthropic path

        _client = anthropic.Anthropic()
    return _client


def _anthropic_complete(prompt: str, model: str, max_tokens: int) -> str:
    response = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return next(b.text for b in response.content if b.type == "text")


def _anthropic_bool(prompt: str, field: str, model: str) -> bool:
    """Structured-output yes/no: guarantees a machine-parseable boolean."""
    response = _get_client().messages.create(
        model=model,
        max_tokens=GRADING_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {field: {"type": "boolean"}},
                    "required": [field],
                    "additionalProperties": False,
                },
            }
        },
    )
    text = next(b.text for b in response.content if b.type == "text")
    return bool(json.loads(text)[field])


# ---------------------------------------------------------------------------
# CLI gateway backend (OpenAI-compatible /v1/chat/completions)
# ---------------------------------------------------------------------------


def _gateway_complete(prompt: str, model: str) -> str:
    url = _gateway_base() + "/chat/completions"
    body = json.dumps(
        {"model": model, "messages": [{"role": "user", "content": prompt}]}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            # The gateway ignores the key, but some proxies require the header.
            "Authorization": "Bearer " + os.environ.get("LLM_API_KEY", "cli"),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=GATEWAY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"게이트웨이 오류 {e.code}: {detail}") from e
    return data["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------------------
# Local `claude` CLI backend (subscription auth, no API key, per-request model)
# ---------------------------------------------------------------------------


def _claude_cli_complete(prompt: str, model: str) -> str:
    claude = _find_claude()
    if not claude:
        raise RuntimeError(
            "claude CLI 를 찾을 수 없습니다. `claude` 가 PATH 에 있는지, 또는 "
            "CLAUDE_CLI_PATH 환경변수로 경로를 지정했는지 확인하세요."
        )
    cmd = [claude, "-p", "--output-format", "text", "--model", model]
    try:
        p = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=CLI_TIMEOUT
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude CLI timeout ({CLI_TIMEOUT}s)") from e
    if p.returncode != 0:
        raise RuntimeError(f"claude CLI exit {p.returncode}: {p.stderr.strip()[:300]}")
    out = p.stdout.strip()
    if not out:
        raise RuntimeError("claude CLI 가 빈 출력을 반환했습니다.")
    return out


_TRUE_TOKENS = {"yes", "true", "relevant", "grounded", "addresses", "y"}
_FALSE_TOKENS = {"no", "false", "irrelevant", "n"}


def _parse_bool(text: str) -> bool:
    """Lenient yes/no parse of free CLI text; defaults True (accept) on failure.

    Defaulting to True keeps the graph moving: an unparseable relevance check
    keeps the document, and an unparseable answer check accepts the answer,
    rather than dropping everything or looping.
    """
    for token in re.findall(r"[a-zA-Z]+", text.lower()):
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
    return True


# ---------------------------------------------------------------------------
# Backend-neutral primitives used by the public helpers
# ---------------------------------------------------------------------------


def _text_complete(prompt: str, model: str) -> str:
    """Plain-text completion via whichever text backend is active."""
    if active_backend() == "gateway":
        return _gateway_complete(prompt, model)
    return _claude_cli_complete(prompt, model)


def _complete(prompt: str, model: str, max_tokens: int) -> str:
    if active_backend() == "api":
        return _anthropic_complete(prompt, model, max_tokens)
    return _text_complete(prompt, model)


def _boolean(prompt: str, field: str, model: str) -> bool:
    if active_backend() == "api":
        return _anthropic_bool(prompt, field, model)
    # Text backends can't do structured outputs -> ask for yes/no and parse.
    text = _text_complete(prompt + "\n\nReply with exactly one word: yes or no.", model)
    return _parse_bool(text)


def _format_context(chunks: list[Chunk]) -> str:
    """Render retrieved chunks as bracketed, source-labelled context blocks."""
    return "\n\n".join(f"[{c.relative_path}]\n{c.text}" for c in chunks)


# ---------------------------------------------------------------------------
# Public helpers (used by nodes)
# ---------------------------------------------------------------------------


def grade_document(question: str, chunk: Chunk, model: str = ANSWER_MODEL) -> bool:
    """True if `chunk` is relevant to answering `question`.

    Deliberately lenient: the goal is to filter out clearly-unrelated chunks,
    not to demand that a single chunk fully answers the question.
    """
    prompt = (
        "You are grading whether a retrieved document is relevant to a user "
        "question. Relevant means it contains keywords or meaning that could "
        "help answer the question, even partially. Be lenient -- only reject "
        "documents that are clearly unrelated.\n\n"
        f"Question: {question}\n\n"
        f"Document [{chunk.relative_path}]:\n{chunk.text}"
    )
    return _boolean(prompt, "relevant", model)


def rewrite_query(question: str, model: str = ANSWER_MODEL) -> str:
    """Rewrite `question` into a query better suited for semantic retrieval."""
    prompt = (
        "The following question retrieved no relevant documents from a vector "
        "search. Rewrite it into a single, clearer search query that is more "
        "likely to match relevant documents -- expand abbreviations, add "
        "synonyms, and make the intent explicit. Reply with only the rewritten "
        "query, no preamble or quotes.\n\n"
        f"Question: {question}"
    )
    return _complete(prompt, model, GRADING_MAX_TOKENS).strip()


def generate_answer(
    question: str, chunks: list[Chunk], model: str = ANSWER_MODEL
) -> str:
    """Generate an answer to `question` grounded in `chunks`, citing sources."""
    prompt = (
        "Answer the question using only the context below. Cite the source "
        "file (in brackets) for each claim you make. If the context does not "
        "contain enough information to answer, say so explicitly.\n\n"
        f"Context:\n{_format_context(chunks)}\n\n"
        f"Question: {question}"
    )
    return _complete(prompt, model, ANSWER_MAX_TOKENS)


def answer_is_grounded(
    chunks: list[Chunk], generation: str, model: str = ANSWER_MODEL
) -> bool:
    """True if `generation` is supported by `chunks` (i.e. not hallucinated)."""
    prompt = (
        "You are checking whether an answer is grounded in a set of source "
        "documents. Grounded means every factual claim in the answer is "
        "supported by the documents. Answer true only if the whole answer is "
        "supported.\n\n"
        f"Documents:\n{_format_context(chunks)}\n\n"
        f"Answer:\n{generation}"
    )
    return _boolean(prompt, "grounded", model)


def answer_addresses_question(
    question: str, generation: str, model: str = ANSWER_MODEL
) -> bool:
    """True if `generation` actually addresses `question`."""
    prompt = (
        "You are checking whether an answer resolves the user's question. "
        "Answer true if it directly addresses what was asked (an explicit "
        "'the context does not contain enough information' counts as "
        "addressing it). Answer false if it is off-topic or evasive.\n\n"
        f"Question: {question}\n\n"
        f"Answer:\n{generation}"
    )
    return _boolean(prompt, "addresses", model)
