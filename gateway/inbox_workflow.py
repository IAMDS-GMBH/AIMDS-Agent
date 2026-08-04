"""Deterministic inbox workflow for dictation/message ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
import os
import re
import logging
from typing import Iterable, Optional

from agent.topic_capture import capture_durable_topic

logger = logging.getLogger(__name__)


_STOP_WORDS = {
    "the", "and", "for", "that", "with", "this", "from", "have", "will", "your", "you",
    "und", "der", "die", "das", "mit", "eine", "einer", "ist", "ich", "wir", "sie",
}


@dataclass
class InboxWorkflowResult:
    success: bool
    stage: str
    message: str
    action: str  # created | extended | failed
    target_path: str
    classification: str
    links: list[str]
    no_link_found: bool = False
    target_paths: list[str] = field(default_factory=list)


@dataclass
class FilingRoute:
    category: str
    destination: str
    example: str


@dataclass
class RoutingConfig:
    inbox_route_enabled: bool
    filing_routes: list[FilingRoute]


def process_inbox_dictation(
    *,
    transcript: str,
    workspace_root: str,
    source_platform: str,
    source_chat_id: str,
    source_thread_id: str = "",
    source_message_id: str = "",
    source_user: str = "",
) -> InboxWorkflowResult:
    def _fail(stage: str, message: str) -> InboxWorkflowResult:
        return InboxWorkflowResult(
            success=False,
            stage=stage,
            message=message,
            action="failed",
            target_path="",
            classification="",
            links=[],
        )

    text = (transcript or "").strip()
    if not text:
        return _fail("classify", "No dictation text available after transcription.")

    root = Path(workspace_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return _fail("existing-check", f"Workspace root is invalid: {root}")

    routing_error, routing = _load_routing_from_agents(root)
    if routing_error is not None:
        return _fail("classify", routing_error)

    items = _split_items(text)
    if not items:
        return _fail("classify", "No classifiable dictation items found.")

    all_paths: list[Path] = []
    all_links: list[Path] = []
    actions: list[str] = []
    classifications: list[str] = []
    triage_count = 0

    for item in items:
        keywords = _keywords(item)
        title = _title_from_text(item, keywords)
        route = _classify_route(item, keywords, routing.filing_routes)

        if route is None:
            if not routing.filing_routes:
                classification = _classify_topic(keywords)
                try:
                    match = _find_duplicate(
                        root=root,
                        text=item,
                        keywords=keywords,
                        source_platform=source_platform,
                        source_chat_id=source_chat_id,
                        source_thread_id=source_thread_id,
                        scope_dir=root / "Inbox",
                    )
                except OSError as exc:
                    return _fail("existing-check", f"Could not inspect existing workspace entries: {exc}")
                if match is None:
                    try:
                        path = _create_entry(
                            root=root,
                            title=title,
                            classification=classification,
                            transcript=item,
                            source_platform=source_platform,
                            source_chat_id=source_chat_id,
                            source_thread_id=source_thread_id,
                            source_message_id=source_message_id,
                            source_user=source_user,
                            route_destination="Inbox/<date>-<topic>.md",
                            route_category="inbox",
                            status="new",
                        )
                    except OSError as exc:
                        return _fail("extend-create", f"Could not create inbox entry: {exc}")
                    action = "created"
                else:
                    try:
                        path = _extend_entry(path=match, transcript=item)
                    except OSError as exc:
                        return _fail("extend-create", f"Could not extend duplicate inbox entry: {exc}")
                    action = "extended"
            else:
                classification = "needs-triage"
                route_destination = "_inbox/needs-triage/<date>-<topic>.md"
                triage_reason = "Ambiguous category; needs triage question before final filing."
                try:
                    path = _create_entry(
                        root=root,
                        title=title,
                        classification=classification,
                        transcript=item,
                        source_platform=source_platform,
                        source_chat_id=source_chat_id,
                        source_thread_id=source_thread_id,
                        source_message_id=source_message_id,
                        source_user=source_user,
                        route_destination=route_destination,
                        route_category="needs-triage",
                        status="needs-triage",
                        triage_reason=triage_reason,
                    )
                except OSError as exc:
                    return _fail("extend-create", f"Could not create triage inbox entry: {exc}")
                action = "created"
                triage_count += 1
        else:
            classification = _slug(route.category) or _classify_topic(keywords)
            scope_dir = _resolve_route_scope_dir(root, route.destination)
            if _is_company_kb_route(route):
                try:
                    path = _create_entry(
                        root=root,
                        title=title,
                        classification="kb-curator-handoff",
                        transcript=item,
                        source_platform=source_platform,
                        source_chat_id=source_chat_id,
                        source_thread_id=source_thread_id,
                        source_message_id=source_message_id,
                        source_user=source_user,
                        route_destination="_inbox/needs-triage/<date>-<topic>.md",
                        route_category=route.category,
                        status="needs-triage",
                        triage_reason="Routed to company knowledge; hand off to KB curator.",
                    )
                except OSError as exc:
                    return _fail("extend-create", f"Could not create KB handoff triage entry: {exc}")
                action = "created"
                triage_count += 1
            else:
                try:
                    match = _find_duplicate(
                        root=root,
                        text=item,
                        keywords=keywords,
                        source_platform=source_platform,
                        source_chat_id=source_chat_id,
                        source_thread_id=source_thread_id,
                        scope_dir=scope_dir,
                    )
                except OSError as exc:
                    return _fail("existing-check", f"Could not inspect existing workspace entries: {exc}")

                if match is None:
                    try:
                        path = _create_entry(
                            root=root,
                            title=title,
                            classification=classification,
                            transcript=item,
                            source_platform=source_platform,
                            source_chat_id=source_chat_id,
                            source_thread_id=source_thread_id,
                            source_message_id=source_message_id,
                            source_user=source_user,
                            route_destination=route.destination,
                            route_category=route.category,
                            status="new",
                        )
                    except OSError as exc:
                        return _fail("extend-create", f"Could not create inbox entry: {exc}")
                    action = "created"
                else:
                    try:
                        path = _extend_entry(path=match, transcript=item)
                    except OSError as exc:
                        return _fail("extend-create", f"Could not extend duplicate inbox entry: {exc}")
                    action = "extended"

        if not path.exists():
            return _fail("extend-create", f"Inbox entry path was not created: {path}")

        try:
            links, candidates = _find_links(root, item, keywords, exclude=set(all_paths + [path]))
            added_links = _append_links(path, links, root=root)
        except OSError as exc:
            return _fail("link", f"Could not link inbox entry to workspace files: {exc}")
        if candidates and not links:
            return _fail(
                "link",
                "Workspace search found link candidates, but no relevant link could be selected.",
            )
        if links and added_links == 0:
            logger.debug("Inbox entry already contained selected links: %s", path)

        all_paths.append(path)
        all_links.extend(links)
        actions.append(action)
        classifications.append(classification)

    if not all_paths:
        return _fail("confirm", "No workspace entries were persisted.")

    unique_links = _dedupe_paths(all_links)
    unique_paths = _dedupe_paths(all_paths)
    final_action = "extended" if actions and all(a == "extended" for a in actions) else "created"
    no_link = not unique_links
    split_count = len(unique_paths)
    try:
        capture_durable_topic(
            source="inbox-dictation",
            title=title,
            content=text,
            confidence=0.9,
            memory_type="notes",
            scope="project",
            tags=[classification, "inbox", action],
            session_id=source_chat_id,
        )
    except Exception as exc:
        logger.warning("Inbox durable topic capture failed: %s", exc, exc_info=True)

    return InboxWorkflowResult(
        success=True,
        stage="confirm",
        message=(
            f"Inbox workflow completed ({final_action}); items_filed={split_count}, "
            f"triaged={triage_count}, links_set={len(unique_links)}."
        ),
        action=final_action,
        target_path=str(unique_paths[0]),
        target_paths=[str(p) for p in unique_paths],
        classification=classifications[0] if classifications else "",
        links=[str(p) for p in unique_links],
        no_link_found=no_link,
    )


def format_inbox_confirmation(result: InboxWorkflowResult, workspace_root: str) -> str:
    if not result.success:
        return (
            "📥 Inbox workflow failed.\n"
            f"- stage: {result.stage}\n"
            f"- reason: {result.message}"
        )

    root = Path(workspace_root).expanduser().resolve()
    target = Path(result.target_path)
    try:
        target_disp = str(target.relative_to(root))
    except ValueError:
        target_disp = str(target)

    filed_count = len(result.target_paths) if result.target_paths else 1
    lines = [
        "📥 Inbox workflow completed.",
        "- workflow: classify -> existing-check -> extend/create -> link -> confirm",
        f"- classification: {result.classification}",
        f"- action: {result.action}",
        f"- filed_items: {filed_count}",
        f"- file: {target_disp}",
    ]
    if result.links:
        first = Path(result.links[0])
        try:
            first_disp = str(first.relative_to(root))
        except ValueError:
            first_disp = str(first)
        lines.append(f"- links: {len(result.links)} (top: {first_disp})")
    else:
        lines.append("- links: 0 (no relevant link found)")
    return "\n".join(lines)


def _load_routing_from_agents(workspace_root: Path) -> tuple[Optional[str], Optional[RoutingConfig]]:
    agents_path = workspace_root / "AGENTS.md"
    if not agents_path.exists():
        return f"Routing table file not found: {agents_path}", None

    try:
        content = agents_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Could not read routing table: {exc}", None

    tables = _parse_markdown_tables(content)
    inbox_route_enabled = _has_inbox_skill_route(tables)
    filing_routes = _parse_filing_routes(tables)

    if not inbox_route_enabled and not filing_routes:
        return "No inbox route found in AGENTS.md routing table.", None

    return None, RoutingConfig(inbox_route_enabled=inbox_route_enabled, filing_routes=filing_routes)


def _parse_markdown_tables(content: str) -> list[list[list[str]]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            current.append(stripped)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    tables: list[list[list[str]]] = []
    for group in groups:
        rows: list[list[str]] = []
        for row in group:
            cells = [cell.strip() for cell in row.split("|")[1:-1]]
            if len(cells) >= 2:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _has_inbox_skill_route(tables: list[list[list[str]]]) -> bool:
    for rows in tables:
        header = rows[0]
        norm_header = [_normalize_routing_cell(c) for c in header]
        if not any("skill" in c for c in norm_header):
            continue
        skill_idx = next((idx for idx, c in enumerate(norm_header) if "skill" in c), 1)
        for row in rows[1:]:
            if _is_markdown_separator_row(row) or len(row) <= skill_idx:
                continue
            if _normalize_routing_cell(row[skill_idx]) == "inbox":
                return True
    return False


def _parse_filing_routes(tables: list[list[list[str]]]) -> list[FilingRoute]:
    routes: list[FilingRoute] = []
    for rows in tables:
        header = [_normalize_routing_cell(c) for c in rows[0]]
        if "category" not in header or "goes to" not in header:
            continue
        category_idx = header.index("category")
        destination_idx = header.index("goes to")
        example_idx = header.index("example") if "example" in header else -1
        for row in rows[1:]:
            if _is_markdown_separator_row(row):
                continue
            if len(row) <= max(category_idx, destination_idx):
                continue
            category = row[category_idx].strip()
            destination = row[destination_idx].strip()
            example = row[example_idx].strip() if example_idx >= 0 and len(row) > example_idx else ""
            if not category or not destination:
                continue
            routes.append(FilingRoute(category=category, destination=destination, example=example))
    return routes


def _is_markdown_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r"[:\-\s]+", cell) is not None for cell in cells)


def _normalize_routing_cell(value: str) -> str:
    return value.replace("`", "").strip().lower()


def _split_items(text: str) -> list[str]:
    base_parts = [p.strip() for p in re.split(r"[\n;]+", text) if p.strip()]
    if len(base_parts) > 1:
        return base_parts
    sentence_parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    if len(sentence_parts) > 1:
        return sentence_parts
    return [text.strip()]


def _classify_route(text: str, keywords: list[str], filing_routes: list[FilingRoute]) -> Optional[FilingRoute]:
    if not filing_routes:
        return None
    scored: list[tuple[float, FilingRoute]] = []
    for route in filing_routes:
        route_text = f"{route.category} {route.destination} {route.example}".lower()
        route_keywords = set(_keywords(route_text))
        overlap = len(set(keywords) & route_keywords)
        overlap_ratio = overlap / max(1, len(set(keywords)))
        sim = _similarity(text, route_text, keywords)
        score = 0.6 * overlap_ratio + 0.4 * sim
        scored.append((score, route))
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_route = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < 0.14:
        return None
    if (best_score - second_score) < 0.04:
        return None
    return best_route


def _is_company_kb_route(route: FilingRoute) -> bool:
    category = _normalize_routing_cell(route.category)
    destination = _normalize_routing_cell(route.destination)
    return "knowledge (company)" in category or "kb curator" in destination


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9_\-]{4,}", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if w in _STOP_WORDS:
            continue
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:12]]


def _classify_topic(keywords: list[str]) -> str:
    if not keywords:
        return "general"
    return keywords[0]


def _title_from_text(text: str, keywords: list[str]) -> str:
    first_sentence = re.split(r"[.!?\n]", text, maxsplit=1)[0].strip()
    if first_sentence:
        words = first_sentence.split()
        return " ".join(words[:10]).strip() or "Dictation"
    if keywords:
        return " ".join(keywords[:6]).title()
    return "Dictation"


def _iter_candidate_files(root: Path, *, scope_dir: Optional[Path] = None) -> Iterable[Path]:
    if scope_dir is not None:
        if not scope_dir.exists():
            return
        for path in scope_dir.rglob("*.md"):
            if path.is_file():
                yield path
        return

    inbox = root / "Inbox"
    if inbox.exists():
        for path in inbox.rglob("*.md"):
            if path.is_file():
                yield path


def _find_duplicate(
    *,
    root: Path,
    text: str,
    keywords: list[str],
    source_platform: str,
    source_chat_id: str,
    source_thread_id: str,
    scope_dir: Optional[Path],
) -> Optional[Path]:
    if not text.strip():
        return None
    best_score = 0.0
    best_path: Optional[Path] = None
    now = datetime.now()
    for path in _iter_candidate_files(root, scope_dir=scope_dir):
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if now - mtime > timedelta(days=21):
            continue
        candidate = path.read_text(encoding="utf-8", errors="ignore")[:6000]
        sim = _similarity(text, candidate, keywords)
        metadata = _read_frontmatter(candidate)
        same_source = (
            _norm(metadata.get("source_platform", "")) == _norm(source_platform)
            and _norm(metadata.get("source_chat_id", "")) == _norm(source_chat_id)
            and (
                not source_thread_id.strip()
                or _norm(metadata.get("source_thread_id", "")) in {"-", _norm(source_thread_id)}
            )
        )
        if same_source and _norm(text) in _norm(candidate):
            sim = max(sim, 0.92)
        elif same_source:
            sim = min(1.0, sim + 0.1)
        if sim > best_score:
            best_score = sim
            best_path = path
    return best_path if best_score >= 0.3 else None


def _similarity(text: str, candidate: str, keywords: list[str]) -> float:
    text_norm = _norm(text)
    cand_norm = _norm(candidate)
    seq = SequenceMatcher(None, text_norm, cand_norm).ratio()
    cand_words = set(_keywords(candidate))
    if keywords:
        overlap = len(set(keywords) & cand_words) / max(1, len(set(keywords)))
    else:
        overlap = 0.0
    return 0.65 * seq + 0.35 * overlap


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _read_frontmatter(content: str) -> dict[str, str]:
    if not content.startswith("---\n"):
        return {}
    end = content.find("\n---\n", 4)
    if end == -1:
        return {}
    result: dict[str, str] = {}
    for line in content[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def _create_entry(
    *,
    root: Path,
    title: str,
    classification: str,
    transcript: str,
    source_platform: str,
    source_chat_id: str,
    source_thread_id: str,
    source_message_id: str,
    source_user: str,
    route_destination: str,
    route_category: str,
    status: str,
    triage_reason: str = "",
) -> Path:
    now = datetime.now()
    path = _resolve_entry_path(root, route_destination, title, now)
    path.parent.mkdir(parents=True, exist_ok=True)

    content = (
        "---\n"
        f"title: {title}\n"
        f"classification: {classification}\n"
        f"route_category: {route_category}\n"
        f"route_destination: {route_destination}\n"
        f"created_at: {now.isoformat(timespec='seconds')}\n"
        f"updated_at: {now.isoformat(timespec='seconds')}\n"
        f"status: {status}\n"
        f"source_platform: {source_platform}\n"
        f"source_chat_id: {source_chat_id}\n"
        f"source_thread_id: {source_thread_id or '-'}\n"
        f"source_message_id: {source_message_id or '-'}\n"
        f"source_user: {source_user or '-'}\n"
        f"triage_reason: {triage_reason or '-'}\n"
        "---\n\n"
        "## Transcript\n\n"
        f"{transcript.strip()}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def _resolve_route_scope_dir(root: Path, destination: str) -> Optional[Path]:
    destination_norm = destination.replace("\\", "/").strip().lower()
    for base in ("contacts", "tasks", "notes", "ideas", "decisions", "projects", "knowledge", "_inbox"):
        if base in destination_norm:
            return root / base
    return None


def _resolve_entry_path(root: Path, destination: str, title: str, now: datetime) -> Path:
    destination_norm = destination.replace("\\", "/").strip()
    if not destination_norm:
        raise OSError("Route destination is empty.")

    if "<name>" in destination_norm:
        destination_norm = destination_norm.replace("<name>", _slug(title))
    if "<topic>" in destination_norm:
        destination_norm = destination_norm.replace("<topic>", _slug(title))
    if "<date>" in destination_norm:
        destination_norm = destination_norm.replace("<date>", now.strftime("%Y%m%d"))

    if destination_norm.endswith(".md"):
        rel = Path(destination_norm)
        if rel.name in {"<name>.md", "<topic>.md", "<date>.md"}:
            raise OSError(f"Route destination could not be resolved: {destination}")
        if str(rel.parent) in {".", ""}:
            rel = Path("Inbox") / now.strftime("%Y") / now.strftime("%m") / rel.name
        return root / rel

    scope = _resolve_route_scope_dir(root, destination_norm)
    if scope is None:
        raise OSError(f"Unsupported route destination in AGENTS.md: {destination}")

    if scope.name in {"contacts", "projects"}:
        return scope / f"{_slug(title)}.md"
    stem = f"{now.strftime('%Y%m%d-%H%M%S')}-{_slug(title)}"
    return scope / f"{stem}.md"


def _extend_entry(*, path: Path, transcript: str) -> Path:
    now = datetime.now().isoformat(timespec="seconds")
    original = path.read_text(encoding="utf-8", errors="ignore")
    updated = _replace_frontmatter_field(original, "updated_at", now)
    updated = _replace_frontmatter_field(updated, "status", "extended")
    append = f"\n\n## Update {now}\n\n{transcript.strip()}\n"
    path.write_text(updated.rstrip() + append, encoding="utf-8")
    return path


def _replace_frontmatter_field(content: str, key: str, value: str) -> str:
    if not content.startswith("---\n"):
        return content
    end = content.find("\n---\n", 4)
    if end == -1:
        return content
    fm = content[4:end]
    body = content[end + 5:]
    lines = fm.splitlines()
    replaced = False
    for idx, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            lines[idx] = f"{key}: {value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def _find_links(
    root: Path,
    text: str,
    keywords: list[str],
    exclude: set[Path],
) -> tuple[list[Path], list[Path]]:
    exclude_resolved = {p.resolve() for p in exclude}
    candidates: list[tuple[float, int, Path]] = []
    for path in _iter_workspace_markdown(root):
        if path.resolve() in exclude_resolved:
            continue
        path_norm = str(path).replace("\\", "/")
        if "/Inbox/" in path_norm or "/_inbox/" in path_norm:
            continue
        snippet = path.read_text(encoding="utf-8", errors="ignore")[:2500]
        candidate_text = f"{path.name}\n{snippet}"
        candidate_keywords = set(_keywords(candidate_text))
        overlap_count = len(set(keywords) & candidate_keywords)
        if overlap_count == 0:
            continue
        overlap_ratio = overlap_count / max(1, len(set(keywords)))
        score = 0.6 * overlap_ratio + 0.4 * _similarity(text, candidate_text, keywords)
        candidates.append((score, overlap_count, path))
    candidates.sort(key=lambda it: (it[0], it[1]), reverse=True)
    if not candidates:
        return [], []
    selected = [entry[2] for entry in candidates[:3]]
    return selected, [entry[2] for entry in candidates]


def _iter_workspace_markdown(root: Path) -> Iterable[Path]:
    for current, dirs, files in os.walk(root):
        dirs[:] = [
            d for d in dirs
            if d not in {".git", ".venv", "venv", "node_modules", ".mcp-backup", ".mcp-delete", "__pycache__"}
        ]
        for fname in files:
            if not fname.endswith((".md", ".txt")):
                continue
            yield Path(current) / fname


def _append_links(path: Path, links: list[Path], *, root: Path) -> int:
    if not links:
        return 0
    current = path.read_text(encoding="utf-8", errors="ignore").rstrip()
    existing_targets = set(re.findall(r"\(([^)\n]+)\)", current))
    new_lines: list[str] = []
    root_resolved = root.resolve()
    for link in links:
        link_resolved = link.resolve()
        try:
            target = link_resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            target = link_resolved.as_posix()
        if target in existing_targets:
            continue
        new_lines.append(f"- [[{link.stem}]] ({target})")
        existing_targets.add(target)

    if not new_lines:
        return 0
    if "\n## Links\n" not in current:
        current += "\n\n## Links\n"
    current += "\n" + "\n".join(new_lines) + "\n"
    path.write_text(current, encoding="utf-8")
    return len(new_lines)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _slug(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return s[:48] or "dictation"
