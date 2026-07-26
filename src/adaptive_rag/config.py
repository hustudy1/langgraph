"""Default configuration for the Adaptive RAG graph.

Values here are plain constants so the whole graph's behaviour (which model,
how many retries, how many chunks) can be read and tuned in one place.
"""

# Default Claude model for every LLM node (document grading, query rewriting,
# answer generation, answer grading). Matches the model the `rag` library uses
# for its own optional answer generation, so the two stay in sync. The CLI
# (--model) and the web dashboard (dropdown) can override this per run.
ANSWER_MODEL = "claude-opus-4-8"

# Models offered in the dashboard dropdown / accepted by --model, as
# (model_id, human_label) pairs. Ordered most-capable-and-costly first. Keep
# `ANSWER_MODEL` present in this list.
AVAILABLE_MODELS = [
    ("claude-opus-4-8", "Claude Opus 4.8 — 가장 똑똑 (기본)"),
    ("claude-sonnet-5", "Claude Sonnet 5 — 균형, 더 저렴"),
    ("claude-haiku-4-5", "Claude Haiku 4.5 — 가장 빠르고 저렴"),
    ("claude-fable-5", "Claude Fable 5 — 최고 성능, 가장 비쌈"),
]

# Set of valid model ids, for server-side validation of user input.
MODEL_IDS = {model_id for model_id, _label in AVAILABLE_MODELS}

# Models for the `claude-cli` backend, passed to `claude --model <alias>`.
# Aliases (not dated ids) are used because they map to whatever the user's
# subscription plan currently offers for that tier, so they always resolve.
CLI_MODELS = [
    ("opus", "Claude Opus — 가장 똑똑"),
    ("sonnet", "Claude Sonnet — 균형 (기본)"),
    ("haiku", "Claude Haiku — 가장 빠름"),
]
CLI_MODEL_IDS = {model_id for model_id, _label in CLI_MODELS}
DEFAULT_CLI_MODEL = "sonnet"

# Chunks retrieved per search attempt (passed to RAGIndex.search(k=...)).
RETRIEVE_K = 5

# Maximum number of query rewrites before the graph gives up and answers with
# whatever it has. Bounds the retrieve -> grade -> rewrite loop.
MAX_QUERY_REWRITES = 2

# Maximum number of generate attempts for a single set of documents before the
# graph accepts the answer even if grading is unhappy. Bounds the
# generate -> grade_answer -> generate loop.
MAX_GENERATIONS = 2

# max_tokens for the (non-streaming) grading and rewriting calls. These return
# short JSON or a single rewritten sentence, so they stay small.
GRADING_MAX_TOKENS = 1024

# max_tokens for the final answer generation.
ANSWER_MAX_TOKENS = 1024
