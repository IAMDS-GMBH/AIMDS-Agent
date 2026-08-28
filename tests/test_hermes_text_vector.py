"""The shared lexical vectorizer behind tool, skill and vault retrieval.

Replaces two independent whole-word counters (`tool_search._build_vector` and
`memory_vault_index._build_term_vector`) that scored exact-token overlap and
nothing else. The case that motivated it: a model searching for "tempo worklog
time tracking" received eight Jira tools and no Tempo tool, concluded Tempo had
nothing to offer, and fetched worklogs issue by issue — sixteen calls to do
what `retrieveWorklogs` does in one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hermes_text_vector import build_vector, compute_idf, cosine, split_words, tokenize


TEMPO = "mcp_TempoMCP_retrieveWorklogs Retrieve Tempo worklogs in a date range for the authenticated user"
JIRA_WORKLOG = "mcp_AtlassianMCP_jira_get_worklog Get worklog entries for a Jira issue"
JIRA_SEARCH = "mcp_AtlassianMCP_jira_search Search Jira issues using JQL"
JIRA_CREATE = "mcp_AtlassianMCP_jira_create_issue Create a Jira issue"

CORPUS = [TEMPO, JIRA_WORKLOG, JIRA_SEARCH, JIRA_CREATE]


def _ranked(query):
    """Corpus entries scored against `query`, best first."""
    idf = compute_idf(CORPUS)
    qv = build_vector(query, idf=idf)

    return sorted(
        ((cosine(qv, build_vector(doc, idf=idf)), doc) for doc in CORPUS),
        key=lambda pair: pair[0],
        reverse=True,
    )


class TestSplitWords:
    def test_breaks_camel_case_server_names(self):
        assert tokenize(split_words("mcp_TempoMCP_retrieveWorklogs")) == [
            "mcp",
            "tempo",
            "mcp",
            "retrieve",
            "worklogs",
        ]

    def test_breaks_the_usual_separators(self):
        assert tokenize(split_words("a.b-c:d_e")) == ["a", "b", "c", "d", "e"]


class TestMorphology:
    """The failure that cost sixteen tool calls."""

    def test_singular_query_reaches_a_plural_name(self):
        scored = dict((doc, score) for score, doc in _ranked("worklog"))

        assert scored[TEMPO] > 0, "a query for 'worklog' must reach retrieveWorklogs"

    def test_exact_match_still_outranks_the_fuzzy_one(self):
        """Morphology tolerance must not cost precision."""
        best_score, best_doc = _ranked("worklog")[0]

        assert best_doc == JIRA_WORKLOG

    def test_a_typo_still_finds_the_tool(self):
        best_score, best_doc = _ranked("retreive worklogs")[0]

        assert best_doc == TEMPO

    def test_the_server_name_wins_when_it_is_named(self):
        best_score, best_doc = _ranked("tempo")[0]

        assert best_doc == TEMPO


class TestTheRegressionCase:
    def test_tempo_outranks_jira_for_the_session_query(self):
        """Verbatim from session 20260828_222131, which returned no Tempo tool."""
        ranked = _ranked("tempo worklog time tracking currentUser")

        assert ranked[0][1] == TEMPO, [doc[:40] for _, doc in ranked]


class TestIdf:
    def test_a_term_shared_by_everything_weighs_less_than_a_rare_one(self):
        idf = compute_idf(CORPUS)

        assert idf["w:jira"] < idf["w:tempo"]

    def test_empty_corpus_yields_no_weights(self):
        assert compute_idf([]) == {}

    def test_a_feature_absent_from_the_corpus_is_not_dropped(self):
        """An unseen term is maximally specific, not meaningless."""
        idf = compute_idf(CORPUS)

        assert build_vector("kubernetes", idf=idf) != {}


class TestVectorMechanics:
    def test_vectors_are_unit_length(self):
        vec = build_vector(TEMPO)
        magnitude = sum(v * v for v in vec.values()) ** 0.5

        assert magnitude == pytest.approx(1.0)

    def test_identical_text_scores_one(self):
        vec = build_vector(TEMPO)

        assert cosine(vec, vec) == pytest.approx(1.0)

    def test_unrelated_text_scores_zero(self):
        assert cosine(build_vector("kubernetes deployment"), build_vector("piano tuning")) == 0.0

    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_empty_input_is_safe(self, text):
        assert build_vector(text) == {}

    def test_cosine_handles_empty_operands(self):
        assert cosine({}, build_vector(TEMPO)) == 0.0

    def test_short_words_contribute_no_trigrams(self):
        """'id' or 'to' would only add boundary noise."""
        vec = build_vector("id to")

        assert all(feature.startswith("w:") for feature in vec)


class TestKnownLimits:
    def test_synonyms_remain_out_of_reach(self):
        """Documents the boundary: this is lexical, not semantic.

        'Zeiterfassung' cannot reach an English tool description by any
        character-level method. That gap is what the opt-in embedding layer
        exists to close — this test should start failing once it lands, and be
        moved there rather than deleted.
        """
        scored = dict((doc, score) for score, doc in _ranked("Zeiterfassung"))

        assert scored[TEMPO] == 0.0
