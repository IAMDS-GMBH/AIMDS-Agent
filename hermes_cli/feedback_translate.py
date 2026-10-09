"""Translate a problem report before it goes to support (AIS-529).

The desktop's "Problem melden" form has a "Translate to English" button that
posts ``{summary, description, target_lang}`` to ``/api/translate``. The
endpoint never existed, so the button always reported "service unavailable".

One tool-less call to the auxiliary model (task ``translation``, configurable
under ``auxiliary.translation``; default: automatic selection) translates both
fields in one go and keeps technical content verbatim.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 500
MAX_DESCRIPTION_CHARS = 8000
_TIMEOUT_SECONDS = 45.0

_LANGUAGE_NAMES = {"en": "English", "de": "German"}

_PROMPT = (
    "Translate the problem report below into {language}. Return only a JSON object "
    '{{"summary": "...", "description": "..."}}. Keep error messages, log lines, '
    "file paths, URLs, IDs, ticket keys, code and product names exactly as written. "
    "Keep line breaks. If a field is already in {language} or empty, return it unchanged.\n\n"
    "{payload}"
)

_JSON_RE = re.compile(r"\{.*\}", re.S)


class TranslationUnavailable(RuntimeError):
    """No model could be reached; ``reason`` is ``no_model`` or ``timeout``."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason


def translate_report(summary: str, description: str, target_lang: str = "en") -> Dict[str, str]:
    """Return ``{"summary", "description"}`` translated into *target_lang*."""
    summary = (summary or "").strip()
    description = (description or "").strip()
    if not summary and not description:
        return {"summary": "", "description": ""}
    language = _LANGUAGE_NAMES.get((target_lang or "en").lower(), "English")
    payload = json.dumps({"summary": summary, "description": description}, ensure_ascii=False)
    try:
        from agent.auxiliary_client import call_llm

        response = call_llm(
            task="translation",
            messages=[{"role": "user", "content": _PROMPT.format(language=language, payload=payload)}],
            max_tokens=4000,
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        reason = "timeout" if "timeout" in text.lower() or "timed out" in text.lower() else "no_model"
        logger.info("feedback translation unavailable (%s): %s", reason, text[:300])
        raise TranslationUnavailable(reason, text) from exc
    raw = response.choices[0].message.content
    raw = raw if isinstance(raw, str) else str(raw or "")
    match = _JSON_RE.search(raw)
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        data = {}
    out_summary = str(data.get("summary") or "").strip() if isinstance(data, dict) else ""
    out_description = str(data.get("description") or "").strip() if isinstance(data, dict) else ""
    if not out_summary and not out_description:
        raise TranslationUnavailable("bad_answer", raw[:300])
    return {
        "summary": out_summary or summary,
        "description": out_description or description,
    }
