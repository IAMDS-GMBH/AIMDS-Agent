import tempfile
from pathlib import Path

from agent.memory_vault_index import VaultMetaIndex, _build_term_vector, _cosine_similarity


def test_term_vector_and_cosine_similarity():
    v1 = _build_term_vector("Hermes agent vector search memory")
    v2 = _build_term_vector("vector search memory optimization")
    v3 = _build_term_vector("unrelated completely different topic")

    sim_1_2 = _cosine_similarity(v1, v2)
    sim_1_3 = _cosine_similarity(v1, v3)

    assert sim_1_2 > 0.3
    assert sim_1_3 == 0.0


def test_vault_meta_index_sync_and_hybrid_search():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "vault_index.sqlite"
        index = VaultMetaIndex(db_path=db_path)

        # Sync sample memory records
        index.sync_record({
            "slug": "notes-hermes-vector-search",
            "title": "Hermes Vector Search Optimization",
            "content": "Implement SQLite and Vector index for memory stubs to reduce token usage by 80-90%.",
            "type": "notes",
            "scope": "project",
            "tags": ["memory", "vector"],
        })

        index.sync_record({
            "slug": "profile-work-schedule",
            "title": "Arbeitszeiten und Ruhetage",
            "content": "Arbeitstage sind Montag bis Freitag. Sonntag ist Ruhetag.",
            "type": "profile",
            "scope": "user",
            "tags": ["profile", "schedule"],
        })

        # Hybrid search for vector search
        results = index.hybrid_search("vector search memory")
        assert len(results) >= 1
        assert results[0]["slug"] == "notes-hermes-vector-search"

        # Search for Arbeitszeiten
        results_de = index.hybrid_search("Arbeitstage Sonntag Ruhetag")
        assert len(results_de) >= 1
        assert results_de[0]["slug"] == "profile-work-schedule"

        # Recall block formatting
        block = index.build_recall_block("vector search", max_chars=500)
        assert "Relevant saved memories" in block
        assert "Hermes Vector Search Optimization" in block
