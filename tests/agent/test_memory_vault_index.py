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
    # Character trigrams give unrelated strings a little incidental overlap —
    # that is the price of matching "worklog" against "worklogs". What must
    # hold is the separation: a real match scores far above the noise floor.
    assert sim_1_3 < 0.1
    assert sim_1_2 > sim_1_3 * 3


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


def test_sync_skills_vault_is_incremental_with_the_real_skill_shape(monkeypatch):
    """The incremental skip only works if the discovery side reports mtimes.

    `_find_all_skills` returns name/description/category by default. The
    indexer fell back to `time.time()` for a missing `updated_at`, so its
    "unchanged since last sync" comparison could never match and every skill
    was re-embedded and re-committed on every single turn. The previous test
    hid this by handing in dicts richer than the real function returns.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# demo\nDoes a demo thing.", encoding="utf-8")

        # Exactly what _find_all_skills(include_source=True) yields.
        real_shape = [
            {
                "name": "demo",
                "description": "Does a demo thing",
                "category": "general",
                "path": str(skill_file),
                "updated_at": int(skill_file.stat().st_mtime),
            }
        ]
        monkeypatch.setattr("tools.skills_tool._find_all_skills", lambda *a, **kw: real_shape)

        index = VaultMetaIndex(db_path=tmp_path / "vault_index.sqlite")

        assert index.sync_skills_vault() == 1
        assert index.sync_skills_vault() == 0, "unchanged skills must not be re-embedded"


def test_sync_skills_vault_reindexes_a_changed_skill(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# demo\nOriginal.", encoding="utf-8")

        entry = {
            "name": "demo",
            "description": "Does a demo thing",
            "category": "general",
            "path": str(skill_file),
            "updated_at": int(skill_file.stat().st_mtime),
        }
        monkeypatch.setattr("tools.skills_tool._find_all_skills", lambda *a, **kw: [entry])

        index = VaultMetaIndex(db_path=tmp_path / "vault_index.sqlite")
        assert index.sync_skills_vault() == 1

        entry["updated_at"] += 60  # the file moved on

        assert index.sync_skills_vault() == 1


def test_sync_skills_vault_indexes_the_skill_body(monkeypatch):
    """Description alone is too thin to find a skill by what it does."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(
            "# release-changelog\nGenerates HTML release notes with Jira links.",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "tools.skills_tool._find_all_skills",
            lambda *a, **kw: [
                {
                    "name": "release-changelog",
                    "description": "",
                    "category": "general",
                    "path": str(skill_file),
                    "updated_at": int(skill_file.stat().st_mtime),
                }
            ],
        )

        index = VaultMetaIndex(db_path=tmp_path / "vault_index.sqlite")
        index.sync_skills_vault()

        hits = index.hybrid_search("release notes Jira", scope_filter="skill")

        assert hits and "release-changelog" in hits[0]["title"]


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

        fake_detailed_tools = [
            {
                "server_name": "MSOffice365MCP",
                "tool_name": "m365_get_events",
                "registered_name": "m365_get_events",
                "description": "Fetch upcoming calendar events from Outlook Graph API",
            }
        ]

        def _fake_get_mcp_meta():
            return fake_mcp_meta

        def _fake_get_detailed_tools():
            return fake_detailed_tools

        monkeypatch.setattr("tools.mcp_tool.get_mcp_server_metadata", _fake_get_mcp_meta)
        monkeypatch.setattr("tools.mcp_tool.get_all_mcp_tools_metadata", _fake_get_detailed_tools)

        index = VaultMetaIndex(db_path=db_path)
        count = index.sync_mcp_tools()
        assert count == 2

        results = index.hybrid_search("Outlook Calendar Mail")
        assert len(results) >= 1
        assert any(r["slug"] == "mcp:MSOffice365MCP" for r in results)

        tool_results = index.hybrid_search("calendar events Outlook Graph")
        assert len(tool_results) >= 1
        assert any(r["slug"] == "mcp_tool:m365_get_events" for r in tool_results)


def test_sync_workspace_vault_indexes_real_obsidian_notes():
    """The user's actual Obsidian vault content (arbitrary .md notes outside
    HermesMemory) must be searchable, not just Hermes-authored memory notes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "vault_index.sqlite"
        vault_dir = tmp_path / "AIMDS-Suite-Vault"
        (vault_dir / "Projects").mkdir(parents=True, exist_ok=True)

        note = vault_dir / "Projects" / "eco-tickets-uebersicht.md"
        note.write_text("ECO Tickets Uebersicht: offene Jira Tickets fuer das ECO Projekt.", encoding="utf-8")

        index = VaultMetaIndex(db_path=db_path)
        count = index.sync_workspace_vault(workspace_dir=vault_dir)
        assert count == 1

        results = index.hybrid_search("ECO Tickets Jira")
        assert len(results) >= 1
        assert results[0]["slug"] == "vault:Projects/eco-tickets-uebersicht.md"
        assert results[0]["type"] == "vault_note"


def test_sync_workspace_vault_skips_hermes_memory_and_noise_dirs():
    """HermesMemory is already covered by sync_filesystem_vault(); re-indexing
    it here would double-count records. Noise dirs (node_modules/.git/...)
    should never be walked either."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "vault_index.sqlite"
        vault_dir = tmp_path / "AIMDS-Suite-Vault"
        (vault_dir / "HermesMemory").mkdir(parents=True, exist_ok=True)
        (vault_dir / "node_modules" / "pkg").mkdir(parents=True, exist_ok=True)
        (vault_dir / "real-note-dir").mkdir(parents=True, exist_ok=True)

        (vault_dir / "HermesMemory" / "mirrored.md").write_text("mirrored memory note", encoding="utf-8")
        (vault_dir / "node_modules" / "pkg" / "readme.md").write_text("dependency noise", encoding="utf-8")
        (vault_dir / "real-note-dir" / "keep-me.md").write_text("genuine vault content", encoding="utf-8")

        index = VaultMetaIndex(db_path=db_path)
        count = index.sync_workspace_vault(workspace_dir=vault_dir)
        assert count == 1

        results = index.hybrid_search("genuine vault content")
        assert len(results) == 1
        assert results[0]["slug"] == "vault:real-note-dir/keep-me.md"


def test_sync_workspace_vault_scoped_to_vault_root_not_whole_documents_tree():
    """Sibling directories outside the resolved workspace/vault root must
    never be scanned -- the user may keep unrelated private files elsewhere
    in Documents that must not be indexed/embedded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "vault_index.sqlite"
        documents_dir = tmp_path / "Documents"
        vault_dir = documents_dir / "AIMDS-Suite-Vault"
        other_dir = documents_dir / "Private-Finances"
        vault_dir.mkdir(parents=True, exist_ok=True)
        other_dir.mkdir(parents=True, exist_ok=True)

        (vault_dir / "vault-note.md").write_text("legit vault note content", encoding="utf-8")
        (other_dir / "private-note.md").write_text("banking password codes xyzzy", encoding="utf-8")

        index = VaultMetaIndex(db_path=db_path)
        count = index.sync_workspace_vault(workspace_dir=vault_dir)
        assert count == 1

        # Assert on what was indexed, not on an empty result list: fuzzy
        # matching means an unrelated query can still return a weak hit on the
        # legitimate note, which would mask — or falsely fail — the property
        # under test. What matters is that nothing from the sibling directory
        # ever entered the index.
        results = index.hybrid_search("banking password codes xyzzy")
        indexed_paths = [str(r.get("path") or r.get("id") or "") for r in results]

        assert not any(str(other_dir) in path for path in indexed_paths), indexed_paths


def test_sync_workspace_vault_incremental_skips_unchanged_files(monkeypatch):
    """Re-syncing without any file changes must not re-process (re-embed)
    unchanged notes, so calling this every turn stays cheap on a large vault."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "vault_index.sqlite"
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        (vault_dir / "note.md").write_text("first version of the note", encoding="utf-8")

        index = VaultMetaIndex(db_path=db_path)
        first_count = index.sync_workspace_vault(workspace_dir=vault_dir)
        assert first_count == 1

        calls = []
        original_sync_record = index.sync_record

        def _tracking_sync_record(record):
            calls.append(record)
            return original_sync_record(record)

        monkeypatch.setattr(index, "sync_record", _tracking_sync_record)

        second_count = index.sync_workspace_vault(workspace_dir=vault_dir)
        assert second_count == 0
        assert calls == []  # no re-embedding of the unchanged file


def test_sync_workspace_vault_reindexes_changed_files():
    """A file whose content/mtime changed since the last sync must be
    re-embedded so search results reflect the latest content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "vault_index.sqlite"
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        note = vault_dir / "note.md"
        note.write_text("original content about apples", encoding="utf-8")

        index = VaultMetaIndex(db_path=db_path)
        index.sync_workspace_vault(workspace_dir=vault_dir)

        import os
        import time as _time
        _time.sleep(1.1)  # ensure a distinct integer mtime
        note.write_text("updated content about oranges", encoding="utf-8")
        os.utime(note, None)

        count = index.sync_workspace_vault(workspace_dir=vault_dir)
        assert count == 1

        results = index.hybrid_search("oranges")
        assert len(results) >= 1
        assert "oranges" in results[0]["content"]
