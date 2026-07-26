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


def test_sync_filesystem_vault():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "vault_index.sqlite"
        vault_dir = tmp_path / "memories"
        user_dir = vault_dir / "user"
        user_dir.mkdir(parents=True, exist_ok=True)

        note_file = user_dir / "test-obsidian-note.md"
        note_file.write_text(
            '---\n{"slug": "test-obsidian-note", "title": "Obsidian Vault Test", "type": "notes", "scope": "user"}\n---\nLokaler Vault Inhalt mit Vektor Indexing.',
            encoding="utf-8"
        )

        index = VaultMetaIndex(db_path=db_path)
        indexed_count = index.sync_filesystem_vault(vault_dir=vault_dir)
        assert indexed_count == 1

        results = index.hybrid_search("Obsidian Vault Vektor")
        assert len(results) >= 1
        assert results[0]["slug"] == "test-obsidian-note"
        assert "Lokaler Vault Inhalt" in results[0]["content"]


def test_sync_skills_vault(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "vault_index.sqlite"

        fake_skills = [
            {
                "name": "m365-calendar",
                "category": "msoffice",
                "description": "Verwalte Termine, Kalender und Abwesenheiten in Microsoft Outlook",
                "tags": ["outlook", "termin", "kalender"],
                "content": "Skill instructions for m365-calendar",
            }
        ]

        def _fake_find_skills(*a, **kw):
            return fake_skills

        monkeypatch.setattr("tools.skills_tool._find_all_skills", _fake_find_skills)

        index = VaultMetaIndex(db_path=db_path)
        count = index.sync_skills_vault()
        assert count == 1

        results = index.hybrid_search("Outlook Termine Kalender")
        assert len(results) >= 1
        assert results[0]["slug"] == "skill:m365-calendar"


def test_sync_mcp_tools(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "vault_index.sqlite"

        fake_mcp_meta = {
            "MSOffice365MCP": {
                "keywords": ["outlook", "calendar", "event", "email"],
                "tools": ["m365_get_events", "m365_send_mail"],
            }
        }

        def _fake_get_mcp_meta():
            return fake_mcp_meta

        monkeypatch.setattr("tools.mcp_tool.get_mcp_server_metadata", _fake_get_mcp_meta)

        index = VaultMetaIndex(db_path=db_path)
        count = index.sync_mcp_tools()
        assert count == 1

        results = index.hybrid_search("Outlook Calendar Mail")
        assert len(results) >= 1
        assert results[0]["slug"] == "mcp:MSOffice365MCP"



