"""AIS-524: detector for model streams stuck in a repetition loop."""

from __future__ import annotations

import random

import pytest

from agent.stream_degeneration import DegenerationDetector, detect


def _stream(text: str, channel: str, chunk: int = 37):
    detector = DegenerationDetector(channel)
    for i in range(0, len(text), chunk):
        finding = detector.feed(text[i:i + chunk])
        if finding is not None:
            return finding
    return None


LOOPS = {
    "exact_repeat": "Ich muss die Zeit für den 6. Oktober buchen. " * 200,
    "counting": ", ".join(str(i) for i in range(1, 3000)),
    "counting_step": " ".join(str(i) for i in range(1000, 40000, 5)),
    "digit_run": "".join(str(i) for i in range(1, 600)),
    "single_char": "Die Summe ist " + "1" * 3000,
}

_rng = random.Random(524)
LEGIT = {
    "prose": (
        "Die Zeitbuchung ist fehlgeschlagen, weil die Tätigkeit nicht gefunden wurde. "
        "Ich habe die gültigen Tätigkeiten abgefragt und schlage Development vor. "
    ) + " ".join(f"Satz {i} mit Inhalt {_rng.random():.3f}." for i in range(400)),
    "number_table": "\n".join(
        f"| {i} | {_rng.randint(1000, 99999)} | {_rng.random():.4f} | {_rng.randint(1, 99)} |"
        for i in range(600)
    ),
    "csv": "\n".join(",".join(str(_rng.randint(0, 9999)) for _ in range(8)) for _ in range(600)),
    "ticket_table": "\n".join(f"| AIS-{500 + i} | Backlog | Normal | Johannes Huchler |" for i in range(400)),
    "numbered_list": "\n".join(
        f"{i}. Punkt {i}: Buchung für Ticket {_rng.randint(1, 999)} prüfen" for i in range(1, 400)
    ),
    "json_rows": str([{"id": i, "status": "Done", "subject": f"Ticket {i}"} for i in range(400)]),
}


@pytest.mark.parametrize("name", sorted(LOOPS))
@pytest.mark.parametrize("channel", ["reasoning", "content"])
def test_loops_are_detected(name, channel):
    finding = _stream(LOOPS[name], channel)
    assert finding is not None, name
    assert finding.channel == channel
    assert finding.chars < 5000


@pytest.mark.parametrize("name", sorted(LEGIT))
@pytest.mark.parametrize("channel", ["reasoning", "content"])
def test_legitimate_output_passes(name, channel):
    assert _stream(LEGIT[name], channel) is None


def test_short_output_is_never_judged():
    assert detect("1, 2, 3, 4, 5", channel="reasoning") is None
    assert detect("ok " * 100, channel="content") is None
