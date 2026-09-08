"""Workspace (vault) adapter: cheap local signals, no MCP (AIS-305).

Reads ``tasks/thisweek.md``, ``_open-questions.md``, ``_findings.md`` and the
"preview" section of the previous brief. Everything is clipped and redacted.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from cron.brief_sources.base import BriefItem, SourceAdapter, SourceContext, SourceStatus, Window, clip

PREVIEW_HEADING_RE = re.compile(r"^#{1,4}\s*.*\b(preview|vorschau|tomorrow|morgen|next working day|nächster arbeitstag)\b", re.I)


def _redact(text: str) -> str:
    try:
        from agent.redact import redact_sensitive_text

        return redact_sensitive_text(text)
    except Exception:
        return text


_KV_RE = re.compile(r"(?:^|\|)\s*(summary|needed|question|finding|next)=(.*?)(?=\s*\|\s*\w+=|$)", re.I)


def _essence(line: str) -> str:
    """Reduce a ``ts=… | source=… | summary=…`` log line to its payload."""
    hits = {k.lower(): v.strip() for k, v in _KV_RE.findall(line)}
    for key in ("summary", "needed", "question", "finding"):
        if hits.get(key):
            return hits[key]
    return line


def _read(path: Path, limit: int = 20000) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return None


class WorkspaceAdapter(SourceAdapter):
    name = "workspace"
    server = None
    kinds = ("morning-brief", "weekly-review")

    def availability(self, ctx: SourceContext) -> Optional[SourceStatus]:
        if ctx.vault_root is None or not Path(ctx.vault_root).is_dir():
            return SourceStatus(self.name, "skipped", "no workspace")
        return None

    def fetch(self, window: Window, ctx: SourceContext) -> List[BriefItem]:
        root = Path(ctx.vault_root)
        items: List[BriefItem] = []
        self.parts: List[str] = []

        text = _read(root / "tasks" / "thisweek.md")
        if text:
            open_lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- [ ]")]
            for n, line in enumerate(open_lines[:12]):
                items.append(BriefItem(source="workspace", kind="task", key=f"thisweek:{n}:{clip(line, 40)}",
                                       title=clip(_redact(line[5:].strip()), 100), status="open",
                                       extra={"file": "tasks/thisweek.md"}))
            self.parts.append("tasks")

        text = _read(root / "_open-questions.md")
        if text:
            bullets = [ln.strip() for ln in text.splitlines() if ln.strip().startswith(("- ", "* "))]
            for n, line in enumerate(bullets[-5:]):
                items.append(BriefItem(source="workspace", kind="note", key=f"open-question:{clip(line, 50)}",
                                       title=clip(_redact(_essence(line[2:])), 120), status="open",
                                       extra={"file": "_open-questions.md"}))
            self.parts.append("open-questions")

        text = _read(root / "_findings.md")
        if text:
            bullets = [ln.strip() for ln in text.splitlines() if ln.strip().startswith(("- ", "* "))]
            for n, line in enumerate(bullets[-5:]):
                items.append(BriefItem(source="workspace", kind="note", key=f"finding:{clip(line, 50)}",
                                       title=clip(_redact(_essence(line[2:])), 120), status="reference",
                                       extra={"file": "_findings.md"}))
            self.parts.append("findings")

        prev = root / "journal" / f"{window.last_working_day.isoformat()}-morning-brief.md"
        text = _read(prev)
        if text:
            section = self._preview_section(text)
            if section:
                items.append(BriefItem(source="workspace", kind="note", key=f"prev-preview:{window.last_working_day.isoformat()}",
                                       title=clip(_redact(section), 800), status="reference",
                                       extra={"file": f"journal/{prev.name}"}))
                self.parts.append("prev-brief")
        return items

    @staticmethod
    def _preview_section(text: str) -> str:
        lines = text.splitlines()
        start = None
        level = 0
        for i, ln in enumerate(lines):
            if PREVIEW_HEADING_RE.match(ln):
                start = i + 1
                level = len(ln) - len(ln.lstrip("#"))
                break
        if start is None:
            return ""
        out: List[str] = []
        for ln in lines[start:]:
            if ln.startswith("#") and (len(ln) - len(ln.lstrip("#"))) <= level:
                break
            if ln.strip().upper().startswith(("FINDING:", "NEXT:", "OPEN_QUESTION:")):
                break
            out.append(ln.rstrip())
        return "\n".join(out).strip()
