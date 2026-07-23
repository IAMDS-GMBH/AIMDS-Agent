"""Deterministic inbox workflow for dictation/message ingestion.

Pipeline:
classify -> existing-check -> extend/create -> auto-link -> confirm
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
import os
import re
import logging
from typing import Iterable, Optional

from agent.topic_capture import capture_durable_topic

logger = logging.getLogger(__name__)


_DICTATION_ROUTE_HINTS = ("dictation", "voice-note", "inbound-message", "voice note", "diktat")
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
    text = (transcript or "").strip()
    if not text:
        return InboxWorkflowResult(
            success=False,
            stage="classify",
            message="No dictation text available after transcription.",
            action="failed",
            target_path="",
            classification="",
            links=[],
        )

    root = Path(workspace_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return InboxWorkflowResult(
            success=False,
            stage="existing-check",
            message=f"Workspace root is invalid: {root}",
            action="failed",
            target_path="",
            classification="",
            links=[],
        )

    routing_error = _validate_inbox_route_from_agents(root)
    if routing_error is not None:
        return InboxWorkflowResult(
            success=False,
            stage="classify",
            message=routing_error,
            action="failed",
            target_path="",
            classification="",
            links=[],
        )

    keywords = _keywords(text)
    classification = _classify_topic(keywords)
    title = _title_from_text(text, keywords)

    match = _find_duplicate(root, text, keywords)
    if match is None:
        path = _create_entry(
            root=root,
            title=title,
            classification=classification,
            transcript=text,
            source_platform=source_platform,
            source_chat_id=source_chat_id,
            source_thread_id=source_thread_id,
            source_message_id=source_message_id,
            source_user=source_user,
        )
        action = "created"
    else:
        path = _extend_entry(path=match, transcript=text)
        action = "extended"

    links = _find_links(root, text, keywords, exclude={path})
    no_link = not links
    _append_links(path, links)
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
        message="Inbox workflow completed.",
        action=action,
        target_path=str(path),
        classification=classification,
        links=[str(p) for p in links],
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
    except Exception:
        target_disp = str(target)

    lines = [
        "📥 Inbox workflow completed.",
        f"- classification: {result.classification}",
        f"- action: {result.action}",
        f"- file: {target_disp}",
    ]
    if result.links:
        first = Path(result.links[0])
        try:
            first_disp = str(first.relative_to(root))
        except Exception:
            first_disp = str(first)
        lines.append(f"- links: {len(result.links)} (top: {first_disp})")
    else:
        lines.append("- links: 0 (no relevant link found)")
    return "\n".join(lines)


def _validate_inbox_route_from_agents(workspace_root: Path) -> Optional[str]:
    agents_path = workspace_root / "AGENTS.md"
    if not agents_path.exists():
        return f"Routing table file not found: {agents_path}"

    try:
        content = agents_path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Could not read routing table: {exc}"

    for line in content.splitlines():
        if not line.strip().startswith("|"):
            continue
        parts = [p.strip().lower() for p in line.split("|")[1:-1]]
        if len(parts) < 2:
            continue
        task_col, skill_col = parts[0], parts[1]
        if skill_col != "inbox":
            continue
        if any(hint in task_col for hint in _DICTATION_ROUTE_HINTS):
            return None
    return "No dictation/message -> inbox route found in AGENTS.md routing table."


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


def _inbox_files(root: Path) -> list[Path]:
    base = root / "Inbox"
    if not base.exists():
        return []
    files = [p for p in base.rglob("*.md") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _find_duplicate(root: Path, text: str, keywords: list[str]) -> Optional[Path]:
    if not text.strip():
        return None
    best_score = 0.0
    best_path: Optional[Path] = None
    now = datetime.now()
    for path in _inbox_files(root):
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if now - mtime > timedelta(days=21):
            continue
        try:
            candidate = path.read_text(encoding="utf-8", errors="ignore")[:6000]
        except Exception:
            continue
        sim = _similarity(text, candidate, keywords)
        if sim > best_score:
            best_score = sim
            best_path = path
    return best_path if best_score >= 0.35 else None


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
) -> Path:
    now = datetime.now()
    rel_dir = Path("Inbox") / now.strftime("%Y") / now.strftime("%m")
    out_dir = root / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = _slug(title)
    stem = f"{now.strftime('%Y%m%d-%H%M%S')}-{slug}"
    path = out_dir / f"{stem}.md"

    content = (
        "---\n"
        f"title: {title}\n"
        f"classification: {classification}\n"
        f"created_at: {now.isoformat(timespec='seconds')}\n"
        f"updated_at: {now.isoformat(timespec='seconds')}\n"
        f"status: new\n"
        f"source_platform: {source_platform}\n"
        f"source_chat_id: {source_chat_id}\n"
        f"source_thread_id: {source_thread_id or '-'}\n"
        f"source_message_id: {source_message_id or '-'}\n"
        f"source_user: {source_user or '-'}\n"
        "---\n\n"
        "## Transcript\n\n"
        f"{transcript.strip()}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


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


def _find_links(root: Path, text: str, keywords: list[str], exclude: set[Path]) -> list[Path]:
    candidates: list[tuple[float, Path]] = []
    for path in _iter_workspace_markdown(root):
        if path in exclude:
            continue
        if "/Inbox/" in str(path).replace("\\", "/"):
            continue
        try:
            snippet = path.read_text(encoding="utf-8", errors="ignore")[:2500]
        except Exception:
            continue
        score = _similarity(text, f"{path.name}\n{snippet}", keywords)
        if score >= 0.18:
            candidates.append((score, path))
    candidates.sort(key=lambda it: it[0], reverse=True)
    if not candidates:
        return []
    return [candidates[0][1]]


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


def _append_links(path: Path, links: list[Path]) -> None:
    if not links:
        return
    current = path.read_text(encoding="utf-8", errors="ignore").rstrip()
    if "\n## Links\n" not in current:
        current += "\n\n## Links\n"
    rel_links = [f"- [[{p.stem}]] ({p.as_posix()})" for p in links]
    current += "\n" + "\n".join(rel_links) + "\n"
    path.write_text(current, encoding="utf-8")


def _slug(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return s[:48] or "dictation"
