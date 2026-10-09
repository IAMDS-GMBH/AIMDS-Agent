"""AIS-529: the problem-report form's "Translate to English" button."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hermes_cli import feedback_translate as ft
from hermes_cli.web_server import _SESSION_TOKEN, app

client = TestClient(app)
HEADERS = {"X-Hermes-Session-Token": _SESSION_TOKEN}


def _answer(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_translates_both_fields_in_one_call():
    reply = json.dumps({"summary": "Booking fails", "description": "Error: activity \"Development\" not found"})
    with patch("agent.auxiliary_client.call_llm", return_value=_answer("Here you go:\n" + reply)) as llm:
        out = ft.translate_report("Buchung schlägt fehl", 'Fehler: activity "Development" not found')
    assert out == {"summary": "Booking fails", "description": 'Error: activity "Development" not found'}
    kwargs = llm.call_args.kwargs
    assert kwargs["task"] == "translation" and "tools" not in kwargs
    prompt = kwargs["messages"][0]["content"]
    assert "English" in prompt and "Keep error messages" in prompt


def test_empty_input_needs_no_model():
    with patch("agent.auxiliary_client.call_llm") as llm:
        assert ft.translate_report("", "  ") == {"summary": "", "description": ""}
    llm.assert_not_called()


@pytest.mark.parametrize(
    "error, reason",
    [(RuntimeError("No auxiliary provider configured"), "no_model"), (TimeoutError("timed out"), "timeout")],
)
def test_unavailable_model_names_the_reason(error, reason):
    with patch("agent.auxiliary_client.call_llm", side_effect=error):
        with pytest.raises(ft.TranslationUnavailable) as exc:
            ft.translate_report("Hallo", "Welt")
    assert exc.value.reason == reason


def test_an_answer_without_json_is_a_failure():
    with patch("agent.auxiliary_client.call_llm", return_value=_answer("Sorry, I cannot help.")):
        with pytest.raises(ft.TranslationUnavailable) as exc:
            ft.translate_report("Hallo", "Welt")
    assert exc.value.reason == "bad_answer"


def test_route_returns_the_translation():
    with patch.object(ft, "translate_report", return_value={"summary": "S", "description": "D"}) as tr:
        resp = client.post("/api/translate", headers=HEADERS,
                           json={"summary": "Z", "description": "B", "target_lang": "en"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "summary": "S", "description": "D"}
    tr.assert_called_once_with("Z", "B", "en")


def test_route_reports_why_it_failed():
    with patch.object(ft, "translate_report", side_effect=ft.TranslationUnavailable("no_model")):
        resp = client.post("/api/translate", headers=HEADERS, json={"summary": "Z", "description": "B"})
    assert resp.json() == {"ok": False, "error": "no_model"}
    too_long = client.post("/api/translate", headers=HEADERS,
                           json={"summary": "x", "description": "y" * (ft.MAX_DESCRIPTION_CHARS + 1)})
    assert too_long.json() == {"ok": False, "error": "too_long"}
