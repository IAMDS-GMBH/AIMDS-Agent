"""SQLite + Vector + FTS5 Hybrid Meta-Index for Hermes Memory & Obsidian Vault.

Provides ultra-fast, token-efficient stub and chunk retrieval (80-90% context reduction)
by combining BM25 full-text search and dense term-frequency vector similarity in a local
SQLite database (~/.hermes/memories/vault_index.sqlite).
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


from hermes_text_vector import VECTOR_SCHEMA_VERSION
from hermes_text_vector import build_vector as _shared_build_vector
from hermes_text_vector import cosine as _shared_cosine


def _tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase alphanumeric terms >=2 chars."""
    return [p for p in re.sub(r"[^\w\s]", " ", str(text or "").lower()).split() if len(p) >= 2]


def _build_term_vector(text: str) -> Dict[str, float]:
    """Lexical vector for `text`, from the shared vectorizer.

    Previously an independent whole-word frequency count — the same approach
    `tools/tool_search.py` carried in its own copy, with the same blind spot:
    exact-token overlap only, so a query for "worklog" scored zero against
    "worklogs". `hermes_text_vector` is now the single implementation behind
    both, adding character trigrams so morphology and typos survive.
    """
    return _shared_build_vector(text)


def _cosine_similarity(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    """Cosine similarity between two vectors from :func:`_build_term_vector`."""
    return _shared_cosine(v1, v2)


# Directory names skipped when walking the workspace/vault root in
# sync_workspace_vault() -- version-control/dependency/build noise that never
# contains genuine vault notes, plus toolchain caches that would otherwise be
# walked on every incremental sync for no benefit.
_VAULT_SCAN_EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".tox", ".idea", ".vscode",
})

# Safety bounds for sync_workspace_vault(): a real personal vault is a
# bounded set of notes. If the resolved workspace root turns out to be a
# broad multi-repo dev directory instead (e.g. a coding-agent session whose
# cwd is the parent of many git checkouts), refuse to index it wholesale --
# see sync_workspace_vault()'s docstring for the incident this guards against.
_MAX_VAULT_SCAN_FILES = 2000
_MAX_VAULT_SYNC_PER_CALL = 200


class VaultMetaIndex:
    """Local SQLite Meta-Index providing hybrid BM25 + Vector recall over memory stubs."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            mem_dir = get_hermes_home() / "memories"
            mem_dir.mkdir(parents=True, exist_ok=True)
            db_path = mem_dir / "vault_index.sqlite"
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS doc_meta (
                    id TEXT PRIMARY KEY,
                    slug TEXT UNIQUE,
                    path TEXT,
                    scope TEXT,
                    type TEXT,
                    title TEXT,
                    content TEXT,
                    tags TEXT,
                    updated_at INTEGER,
                    vector_json TEXT
                )
            """)
            # Migration: tag each row with its sync origin ("memory-mirror",
            # "vault", "skill", "mcp", ...) so callers (e.g. the incremental
            # workspace-vault scan) can filter/compare without re-parsing
            # every record. ALTER TABLE ADD COLUMN has no IF NOT EXISTS form
            # in SQLite, so guard against re-running on an already-migrated DB.
            try:
                conn.execute("ALTER TABLE doc_meta ADD COLUMN source TEXT")
            except sqlite3.OperationalError:
                pass
            # Check if FTS table exists
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
                        slug, title, content, type, tags,
                        content='doc_meta',
                        content_rowid='rowid'
                    )
                """)
            except sqlite3.OperationalError:
                # Fallback if FTS5 not available
                pass

            # Stored vectors are derived data built by hermes_text_vector. When
            # that vectorizer changes shape, every persisted vector becomes
            # incomparable to freshly built query vectors — cosine goes to zero
            # against every row, silently, with no error anywhere. The
            # incremental sync would keep the stale vectors indefinitely, since
            # the source files themselves never changed. Rebuilding is cheap
            # (~900 records) and correct.
            try:
                stored_version = conn.execute("PRAGMA user_version").fetchone()[0]
                if stored_version != VECTOR_SCHEMA_VERSION:
                    conn.execute("DELETE FROM doc_meta")
                    conn.execute(f"PRAGMA user_version = {int(VECTOR_SCHEMA_VERSION)}")
                    logger.info(
                        "Vault index vectors rebuilt: schema %s -> %s",
                        stored_version,
                        VECTOR_SCHEMA_VERSION,
                    )
            except sqlite3.Error:
                # A version check must never make the index unusable.
                pass

            conn.commit()

    def sync_record(self, record: Dict[str, Any], *, rebuild_fts: bool = True) -> None:
        """Upsert a single memory or vault record into the SQLite index.

        `rebuild_fts` triggers a full `doc_fts` rebuild after the upsert.
        This is O(table size) per call, so bulk syncers (sync_filesystem_vault,
        sync_workspace_vault, sync_skills_vault, sync_mcp_tools, the JSONL
        mirror loop in sync_mirror_store) must pass `rebuild_fts=False` and
        let sync_mirror_store() do a single rebuild at the end of the whole
        pass -- calling rebuild per-record turns an N-record sync into an
        O(N * table_size) operation, which is what made every conversation
        turn hang for 90+ seconds once the memory/skill corpus grew past
        ~150 records.
        """
        if not isinstance(record, dict) or not record:
            return
        slug = str(record.get("slug") or record.get("id") or "").strip()
        if not slug:
            return

        title = str(record.get("title") or "").strip()
        content = str(record.get("content") or "").strip()
        tags_list = record.get("tags") or []
        tags_str = ", ".join(str(t).strip() for t in tags_list if str(t).strip()) if isinstance(tags_list, list) else str(tags_list)
        doc_type = str(record.get("type") or "notes").strip()
        scope = str(record.get("scope") or "project").strip()
        updated_at = int(record.get("updated_at") or time.time())
        doc_id = str(record.get("id") or slug)
        doc_path = str(record.get("path") or "")
        source = str(record.get("source") or "").strip() or None

        full_text = f"{title} {content} {tags_str} {doc_type}"
        vec = _build_term_vector(full_text)
        vec_json = json.dumps(vec, ensure_ascii=False)

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO doc_meta (id, slug, path, scope, type, title, content, tags, updated_at, vector_json, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    title=excluded.title,
                    content=excluded.content,
                    tags=excluded.tags,
                    updated_at=excluded.updated_at,
                    vector_json=excluded.vector_json,
                    path=excluded.path,
                    scope=excluded.scope,
                    type=excluded.type,
                    source=excluded.source
            """, (doc_id, slug, doc_path, scope, doc_type, title, content, tags_str, updated_at, vec_json, source))

            if rebuild_fts:
                try:
                    conn.execute("INSERT INTO doc_fts(doc_fts) VALUES('rebuild')")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def _rebuild_fts(self) -> None:
        """Rebuild the doc_fts external-content index once, after a bulk sync."""
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT INTO doc_fts(doc_fts) VALUES('rebuild')")
                conn.commit()
        except sqlite3.OperationalError:
            pass

    def sync_filesystem_vault(self, vault_dir: Optional[Path] = None) -> int:
        """Scan and index all local markdown (.md) memory notes from ~/.hermes/memories/.

        Incremental: notes whose mtime is unchanged since the last sync are
        skipped entirely (see sync_workspace_vault() for the same pattern).
        Without this, every conversation turn re-embedded and re-committed
        every memory note unconditionally, which is what made this call take
        30-90+ seconds once the corpus grew past ~100 notes.
        """
        if vault_dir is None:
            vault_dir = get_hermes_home() / "memories"
        if not vault_dir.exists():
            return 0

        existing: Dict[str, int] = {}
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT slug, updated_at FROM doc_meta WHERE source = 'memory-mirror'"
                ).fetchall()
            existing = {row["slug"]: int(row["updated_at"] or 0) for row in rows}
        except sqlite3.OperationalError:
            existing = {}

        count = 0
        md_files = list(vault_dir.rglob("*.md"))
        for p in md_files:
            try:
                text = p.read_text(encoding="utf-8")
                raw = str(text or "")
                fm: Dict[str, Any] = {}
                body = raw.strip()
                if raw.startswith("---") and "---" in raw[3:]:
                    parts = raw.split("---", 2)
                    if len(parts) >= 3:
                        try:
                            fm = json.loads(parts[1].strip())
                        except Exception:
                            fm = {}
                        body = parts[2].strip()

                slug = str(fm.get("slug") or p.stem).strip()
                scope = str(fm.get("scope") or ("user" if p.parent.name == "user" else "project")).strip()
                mem_type = str(fm.get("type") or ("profile" if scope == "user" else "notes")).strip()
                title = str(fm.get("title") or p.stem.replace("-", " ").strip()).strip()
                tags = fm.get("tags") or []
                updated_at = int(fm.get("updated_at") or int(p.stat().st_mtime))

                if existing.get(slug) == updated_at:
                    continue  # unchanged since the last sync -- skip re-embedding

                record = {
                    "id": str(p),
                    "slug": slug,
                    "path": str(p),
                    "scope": scope,
                    "type": mem_type,
                    "title": title,
                    "content": body,
                    "tags": tags if isinstance(tags, list) else [str(tags)],
                    "updated_at": updated_at,
                    "source": "memory-mirror",
                }
                self.sync_record(record, rebuild_fts=False)
                count += 1
            except Exception:
                continue
        return count

    def sync_workspace_vault(self, workspace_dir: Optional[Path] = None) -> int:
        """Scan and index arbitrary markdown notes from the resolved local
        workspace/vault root (e.g. the user's real Obsidian vault content),
        so hybrid_search()/build_recall_block() can actually surface them
        instead of the model falling back to blind Read File exploration.

        Deliberately scoped to ONLY the resolved workspace root (never the
        whole home/Documents tree) so unrelated personal files elsewhere are
        never scanned or embedded. Runs incrementally: files whose mtime is
        unchanged since the last sync are skipped entirely, so this stays
        cheap to call on every turn even for a large vault.

        Bails out entirely (returns 0, indexes nothing) if the resolved
        workspace root contains more than _MAX_VAULT_SCAN_FILES markdown
        files. A real personal vault is a bounded set of notes; a directory
        this large is almost certainly a broad multi-repo dev workspace
        (e.g. a coding-agent session whose cwd is a parent folder of many
        git checkouts) rather than a vault, and treating every README/docs
        file in there as a "vault note" both pollutes search results and
        can turn a single rglob() into a multi-thousand-file, multi-minute
        scan that blocks the whole turn. Also caps the number of files
        actually (re-)embedded in a single call at _MAX_VAULT_SYNC_PER_CALL
        so even a legitimately large first-time vault catches up over a
        few turns instead of stalling the first one.

        The `HermesMemory` subfolder is skipped here since it's a symlink
        into `~/.hermes/memories`, already covered by sync_filesystem_vault().
        """
        if workspace_dir is None:
            try:
                from agent.runtime_cwd import resolve_agent_cwd
                workspace_dir = resolve_agent_cwd()
            except Exception:
                return 0
        if not workspace_dir or not workspace_dir.exists():
            return 0

        # Previously-indexed vault-doc mtimes, for the incremental skip below.
        existing: Dict[str, int] = {}
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT slug, updated_at FROM doc_meta WHERE source = 'vault'"
                ).fetchall()
            existing = {row["slug"]: int(row["updated_at"] or 0) for row in rows}
        except sqlite3.OperationalError:
            existing = {}

        candidates: List[Path] = []
        for p in workspace_dir.rglob("*.md"):
            candidates.append(p)
            if len(candidates) > _MAX_VAULT_SCAN_FILES:
                return 0  # not a bounded personal vault -- refuse to index it

        count = 0
        for p in candidates:
            try:
                rel_parts = p.relative_to(workspace_dir).parts[:-1]
            except ValueError:
                continue
            if any(part in _VAULT_SCAN_EXCLUDED_DIRS or part == "HermesMemory" for part in rel_parts):
                continue
            try:
                mtime = int(p.stat().st_mtime)
            except OSError:
                continue

            slug = f"vault:{p.relative_to(workspace_dir).as_posix()}"
            if existing.get(slug) == mtime:
                continue  # unchanged since the last sync -- skip re-embedding

            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            title = p.stem.replace("-", " ").replace("_", " ").strip() or p.name
            record = {
                "id": str(p),
                "slug": slug,
                "path": str(p),
                "scope": "vault",
                "type": "vault_note",
                "title": title,
                "content": text.strip(),
                "tags": ["vault"],
                "updated_at": mtime,
                "source": "vault",
            }
            self.sync_record(record, rebuild_fts=False)
            count += 1
            if count >= _MAX_VAULT_SYNC_PER_CALL:
                break
        return count

    def sync_skills_vault(self, skills_dir: Optional[Path] = None) -> int:
        """Scan and index all installed and bundled skills into the SQLite meta-index.

        Incremental: skills whose updated_at is unchanged since the last
        sync are skipped (same pattern as sync_workspace_vault()) -- without
        it, every turn re-embedded and re-committed every skill unconditionally.
        """
        try:
            from tools.skills_tool import _find_all_skills
            skills = _find_all_skills(skip_disabled=True)
        except Exception:
            return 0

        existing: Dict[str, int] = {}
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT slug, updated_at FROM doc_meta WHERE source = 'skill'"
                ).fetchall()
            existing = {row["slug"]: int(row["updated_at"] or 0) for row in rows}
        except sqlite3.OperationalError:
            existing = {}

        count = 0
        for s in skills:
            if not isinstance(s, dict):
                continue
            name = str(s.get("name") or "").strip()
            if not name:
                continue

            desc = str(s.get("description") or "").strip()
            cat = str(s.get("category") or "general").strip()
            tags = s.get("tags") or []
            tags_list = tags if isinstance(tags, list) else [str(tags)]
            body = str(s.get("body") or s.get("content") or "").strip()

            slug = f"skill:{name}"
            updated_at = int(s.get("updated_at") or time.time())
            if existing.get(slug) == updated_at:
                continue  # unchanged since the last sync -- skip re-embedding

            doc_id = f"skill:{cat}/{name}"
            title = f"Skill: {name}"
            content = f"{desc}\nCategory: {cat}\nTags: {', '.join(tags_list)}\n\n{body[:600]}".strip()

            record = {
                "id": doc_id,
                "slug": slug,
                "path": str(s.get("path") or ""),
                "scope": "skill",
                "type": "skill",
                "title": title,
                "content": content,
                "tags": tags_list + [cat, "skill"],
                "updated_at": updated_at,
                "source": "skill",
            }
            self.sync_record(record, rebuild_fts=False)
            count += 1
        return count

    def sync_mcp_tools(self) -> int:
        """Scan and index all connected MCP servers and individual MCP tools into SQLite vector meta-index."""
        try:
            from tools.mcp_tool import get_mcp_server_metadata, get_all_mcp_tools_metadata
            mcp_meta = get_mcp_server_metadata()
            detailed_tools = get_all_mcp_tools_metadata()
        except Exception:
            mcp_meta = {}
            detailed_tools = []

        try:
            from hermes_cli.mcp_config import _get_mcp_servers
            configured_servers = _get_mcp_servers()
        except Exception:
            configured_servers = {}

        count = 0
        now = int(time.time())

        # Merge configured server stubs into mcp_meta if not already present
        for s_name, s_cfg in configured_servers.items():
            if s_name not in mcp_meta and isinstance(s_cfg, dict):
                mcp_meta[s_name] = {
                    "keywords": [s_name, "aimds", "mcp"],
                    "tools": s_cfg.get("tools") or [],
                }

        # 1. Server-level stubs
        for server_name, meta in mcp_meta.items():
            if not isinstance(meta, dict):
                continue
            keywords = meta.get("keywords") or []
            tools_list = meta.get("tools") or []
            if isinstance(tools_list, dict):
                tools_list = tools_list.get("include") or []
            if not isinstance(tools_list, list):
                tools_list = [str(x) for x in list(tools_list)]

            slug = f"mcp:{server_name}"
            doc_id = f"mcp:{server_name}"
            title = f"MCP Server: {server_name}"
            content = f"Server: {server_name}\nKeywords: {', '.join(keywords)}\nTools: {', '.join(tools_list[:10])}".strip()

            record = {
                "id": doc_id,
                "slug": slug,
                "path": "",
                "scope": "mcp",
                "type": "mcp_server",
                "title": title,
                "content": content,
                "tags": keywords + ["mcp", server_name],
                "updated_at": now,
                "source": "mcp",
            }
            self.sync_record(record, rebuild_fts=False)
            count += 1

        # 2. Fine-grained individual MCP tools (AIMDS, M365, Atlassian, GitHub, etc.)
        for t in detailed_tools:
            s_name = str(t.get("server_name") or "").strip()
            t_name = str(t.get("tool_name") or "").strip()
            reg_name = str(t.get("registered_name") or f"mcp_{s_name}_{t_name}").strip()
            desc = str(t.get("description") or "").strip()

            slug = f"mcp_tool:{reg_name}"
            doc_id = slug
            title = f"MCP Tool: {reg_name} ({s_name})"
            content = f"Tool: {reg_name}\nServer: {s_name}\nName: {t_name}\nDescription: {desc}".strip()

            keywords = [s_name, t_name, "mcp", "tool"]
            record = {
                "id": doc_id,
                "slug": slug,
                "path": "",
                "scope": "mcp",
                "type": "mcp_tool",
                "title": title,
                "content": content,
                "tags": keywords,
                "updated_at": now,
                "source": "mcp",
            }
            self.sync_record(record, rebuild_fts=False)
            count += 1

        return count

    def sync_mirror_store(self, jsonl_path: Optional[Path] = None) -> int:
        """Sync all records from MCP_MIRROR_MEMORY.jsonl, filesystem vault, workspace
        vault notes, skills, and MCP tools into SQLite.

        Rebuilds the doc_fts index exactly once at the end of this pass
        (not once per record -- see sync_record()'s rebuild_fts docstring),
        and only when something actually changed.
        """
        if jsonl_path is None:
            jsonl_path = get_hermes_home() / "memories" / "MCP_MIRROR_MEMORY.jsonl"

        count = self.sync_filesystem_vault()
        try:
            count += self.sync_workspace_vault()
        except Exception:
            pass
        count += self.sync_skills_vault()
        count += self.sync_mcp_tools()

        if jsonl_path.exists():
            try:
                for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            self.sync_record(row, rebuild_fts=False)
                            count += 1
                    except Exception:
                        continue
            except OSError:
                pass

        if count > 0:
            self._rebuild_fts()
        return count

    def hybrid_search(
        self,
        query: str,
        *,
        top_k: int = 5,
        max_chars: int = 1200,
        scope_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Perform hybrid BM25 + Vector similarity search over indexed memory stubs.

        Returns a list of matching records with calculated relevance scores.
        """
        if not str(query or "").strip():
            return []

        query_terms = _tokenize(query)
        if not query_terms:
            return []

        query_vec = _build_term_vector(query)
        scope_f = str(scope_filter or "").strip().lower() if scope_filter else None
        now = int(time.time())

        # Step 1: Retrieve candidate documents from doc_meta
        with self._get_connection() as conn:
            if scope_f:
                rows = conn.execute("SELECT * FROM doc_meta WHERE LOWER(scope) = ?", (scope_f,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM doc_meta").fetchall()

        if not rows:
            return []

        # Step 2: Score candidates using hybrid BM25 / token overlap + Vector Cosine + Recency
        results: List[Tuple[float, Dict[str, Any]]] = []

        for row in rows:
            r_dict = dict(row)
            vec_json = r_dict.get("vector_json") or "{}"
            try:
                doc_vec = json.loads(vec_json)
            except Exception:
                doc_vec = {}

            # Vector similarity
            vec_score = _cosine_similarity(query_vec, doc_vec)

            # BM25 / token overlap score
            doc_text = f"{r_dict.get('title', '')} {r_dict.get('content', '')} {r_dict.get('tags', '')}".lower()
            doc_tokens = set(_tokenize(doc_text))
            overlap = len(set(query_terms) & doc_tokens)
            bm25_score = overlap / max(1, len(query_terms))

            if vec_score <= 0 and bm25_score <= 0:
                continue

            # Recency boost
            updated_at = int(r_dict.get("updated_at") or now)
            age_hours = max(0.0, (now - updated_at) / 3600.0)
            recency_boost = 1.0 / (1.0 + math.log1p(age_hours))

            # Hybrid combined score
            hybrid_score = (0.5 * bm25_score) + (0.4 * vec_score) + (0.1 * recency_boost)
            results.append((hybrid_score, r_dict))

        results.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in results[: max(1, int(top_k or 5))]]

    def build_recall_block(
        self,
        query: str,
        *,
        top_k: int = 5,
        max_chars: int = 1200,
        scope_filter: Optional[str] = None,
    ) -> str:
        """Format hybrid search results into a compact, token-efficient prompt block."""
        records = self.hybrid_search(query, top_k=top_k, max_chars=max_chars, scope_filter=scope_filter)
        if not records:
            return ""

        lines: List[str] = ["Relevant saved memories (hybrid index):"]
        for r in records:
            mem_type = str(r.get("type") or "")
            title = str(r.get("title") or "").strip()
            content = str(r.get("content") or "").strip()
            label = f"[{mem_type}] " if mem_type else ""

            # Truncate content to keep stubs tight
            if len(content) > 200:
                content = content[:197].rstrip() + "…"

            if title and content:
                lines.append(f"- {label}{title}: {content}")
            elif title:
                lines.append(f"- {label}{title}")
            elif content:
                lines.append(f"- {label}{content}")

        block = "\n".join(lines).strip()
        if len(block) > max_chars:
            block = block[:max_chars - 1].rstrip() + "…"
        return block
