"""Shared lexical vectorizer for tool, skill and vault retrieval.

Two independent copies of the same idea used to live in the codebase:
``_build_vector`` in ``tools/tool_search.py`` and ``_build_term_vector`` in
``agent/memory_vault_index.py``. Both counted whole words and L2-normalized
the result, and both were labelled "vector similarity" — in ``search_catalog``
the cosine is even weighted 100x, so it decided the ranking on its own.

Counting whole words means exact-token overlap and nothing else:

    "worklog"       vs  retrieveWorklogs   ->  0.0
    "Zeiterfassung" vs  retrieveWorklogs   ->  0.0

The first is the expensive one. A model searching for "tempo worklog time
tracking" got eight Jira tools and no Tempo tool, concluded Tempo had nothing
to offer, and fetched worklogs issue by issue: sixteen calls to do what
``retrieveWorklogs`` does in one.

This module keeps the vectors lexical — no model, no network, no new
dependency — but makes near-misses score. Every string contributes both its
words and its character trigrams:

* **Words** carry the exact-match signal and are weighted higher, so a literal
  hit still outranks a fuzzy one.
* **Trigrams** make morphology and typos survive: ``worklog`` and ``worklogs``
  share every trigram but one, and ``retreive`` still finds ``retrieve``.

Synonyms and cross-language matches (``Zeiterfassung`` -> ``worklog``) are out
of reach for any lexical scheme; those need real embeddings, which layer on top
of this one rather than replacing it.
"""

from __future__ import annotations

import math
import re
from typing import Dict, Iterable, List, Optional

__all__ = [
    "VECTOR_SCHEMA_VERSION",
    "build_vector",
    "compute_idf",
    "cosine",
    "split_words",
    "tokenize",
]

# Bump whenever a change here makes previously stored vectors incomparable to
# freshly built ones. Persisted vectors (``doc_meta.vector_json``) are derived
# data, so a consumer that stores them must detect the mismatch and rebuild —
# otherwise queries score near zero against every stale row, silently, with no
# error anywhere. Version 1 was whole-word frequency; version 2 added
# character trigrams and IDF.
VECTOR_SCHEMA_VERSION = 2

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Trigrams outnumber words by roughly the length of each word, so without a
# counterweight they would drown the exact-match signal after normalization.
_WORD_WEIGHT = 2.0
_TRIGRAM_WEIGHT = 1.0

_TRIGRAM_N = 3
# Below this length a word has no interesting internal structure; "id" or "to"
# would only contribute boundary noise.
_MIN_TRIGRAM_WORD = 3


def split_words(text: str) -> str:
    """Break an identifier into space-separated words.

    Splits on ``_ . - :`` and on camelCase boundaries, so
    ``mcp_TempoMCP_retrieveWorklogs`` becomes
    ``mcp Tempo MCP retrieve Worklogs``. Without the camelCase split a
    PascalCase server name collapses into one opaque token that a literal query
    can never match.
    """
    separated = (
        str(text or "")
        .replace("_", " ")
        .replace(".", " ")
        .replace("-", " ")
        .replace(":", " ")
    )

    return _CAMEL_BOUNDARY_RE.sub(" ", separated)


def tokenize(text: str) -> List[str]:
    """Lowercased alphanumeric word tokens."""
    if not text:
        return []

    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _trigrams(word: str) -> List[str]:
    """Character trigrams of a word, padded so its edges are matchable.

    The padding matters: without it ``tempo`` and ``tempos`` differ only in
    trigrams that both already share, but a query for ``tempo`` could not
    distinguish a word *starting* with it from one merely containing it.
    """
    if len(word) < _MIN_TRIGRAM_WORD:
        return []
    padded = f" {word} "

    return [padded[i : i + _TRIGRAM_N] for i in range(len(padded) - _TRIGRAM_N + 1)]


def _raw_counts(text: str) -> Dict[str, float]:
    """Un-normalized feature counts: weighted words plus trigrams."""
    words = tokenize(split_words(text))
    if not words:
        return {}

    counts: Dict[str, float] = {}
    for word in words:
        key = f"w:{word}"
        counts[key] = counts.get(key, 0.0) + _WORD_WEIGHT
        for gram in _trigrams(word):
            key = f"g:{gram}"
            counts[key] = counts.get(key, 0.0) + _TRIGRAM_WEIGHT

    return counts


def compute_idf(texts: Iterable[str]) -> Dict[str, float]:
    """Inverse document frequency over a corpus, for use with `build_vector`.

    Without this every feature counts the same, so a term shared by the whole
    catalog (``mcp``, ``jira``) pulls as hard as the one that actually
    discriminates. Computed once per catalog build rather than per query.
    """
    doc_count = 0
    seen: Dict[str, int] = {}
    for text in texts:
        doc_count += 1
        for feature in _raw_counts(text):
            seen[feature] = seen.get(feature, 0) + 1

    if doc_count == 0:
        return {}

    # Smoothed so a feature present in every document still contributes a
    # little rather than collapsing to exactly zero.
    return {
        feature: math.log(1.0 + (doc_count / (1 + df)))
        for feature, df in seen.items()
    }


def build_vector(text: str, *, idf: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Build an L2-normalized lexical vector for `text`.

    Pass the corpus `idf` from :func:`compute_idf` to weight discriminating
    features higher. Without it the vector is plain weighted frequency, which
    still beats whole-word counting because the trigrams are present.
    """
    counts = _raw_counts(text)
    if not counts:
        return {}

    if idf:
        # A feature absent from the corpus is maximally specific, not
        # meaningless — fall back to the highest weight rather than dropping it.
        default = max(idf.values()) if idf else 1.0
        counts = {f: c * idf.get(f, default) for f, c in counts.items()}

    norm = math.sqrt(sum(c * c for c in counts.values()))
    if norm <= 0:
        return {}

    return {f: c / norm for f, c in counts.items()}


def cosine(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    """Cosine similarity of two vectors from :func:`build_vector`.

    Both are already L2-normalized, so this is the dot product. Iterates the
    smaller vector; a query vector is typically far smaller than a document's.
    """
    if not v1 or not v2:
        return 0.0
    if len(v1) > len(v2):
        v1, v2 = v2, v1

    return sum(weight * v2[feature] for feature, weight in v1.items() if feature in v2)
