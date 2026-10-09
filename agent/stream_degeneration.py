"""Detect a model stream that degenerated into a repetition loop (AIS-524).

SUP-20261007-172008: after a failed booking the model's reasoning turned into
an ever-growing number sequence until the user cancelled minutes later. Nothing
in the streaming path noticed. :class:`DegenerationDetector` watches one
channel (reasoning or content) incrementally and names the pattern once the
tail of the stream is clearly stuck:

* ``exact_repeat`` — the tail is one block repeated verbatim (a phrase, a
  paragraph, a single character);
* ``counting`` — the tail is mostly numbers that keep growing by a constant
  step (1, 2, 3, … or 100, 105, 110, …);
* ``digit_run`` — one unbroken run of hundreds of digits.

Legitimate output (tables, CSV, numbered lists, code) varies from row to row,
so none of the rules fire on it. The checks run every few hundred characters
over a bounded window, so the hot path stays cheap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_WINDOW_CHARS = 4000
_CHECK_EVERY_CHARS = 256

# exact_repeat: the last ``period`` characters repeat at least this often and
# cover at least this many characters.
_REPEAT_MIN_COUNT = 5
_REPEAT_MIN_SPAN = 1500
_REPEAT_MAX_PERIOD = 800
_REPEAT_PROBE = 48

# counting: enough numbers, nearly all with the same positive step, and the
# text is mostly numbers (a numbered list with prose between the items is not).
_COUNTING_MIN_NUMBERS = {"reasoning": 150, "content": 300}
_COUNTING_STEP_SHARE = 0.9
_COUNTING_DIGIT_SHARE = 0.6

_DIGIT_RUN_MIN = 400

_INT_RE = re.compile(r"\d+")
_DIGIT_RUN_RE = re.compile(r"\d{%d,}" % _DIGIT_RUN_MIN)
_NUMBERISH = frozenset("0123456789,.;:-+ \n\t")


@dataclass(frozen=True)
class Degeneration:
    """What the detector found: the rule and the channel it fired on."""

    reason: str
    channel: str
    chars: int

    def describe(self) -> str:
        return f"{self.channel} {self.reason} after {self.chars} chars"


class DegenerateStreamError(RuntimeError):
    """Raised from the stream consumer to abort a degenerated response."""

    def __init__(self, finding: Degeneration):
        super().__init__(f"Model output degenerated ({finding.describe()})")
        self.finding = finding


class DegenerationDetector:
    """Incremental repetition-loop detector for one stream channel."""

    def __init__(self, channel: str = "content"):
        self.channel = channel
        self._tail = ""
        self._total = 0
        self._since_check = 0

    def feed(self, text: str) -> Optional[Degeneration]:
        """Add streamed text; returns a finding once the stream is stuck."""
        if not text:
            return None
        self._tail = (self._tail + text)[-_WINDOW_CHARS:]
        self._total += len(text)
        self._since_check += len(text)
        if self._since_check < _CHECK_EVERY_CHARS:
            return None
        self._since_check = 0
        reason = detect(self._tail, channel=self.channel)
        if reason is None:
            return None
        return Degeneration(reason=reason, channel=self.channel, chars=self._total)


def detect(tail: str, *, channel: str = "content") -> Optional[str]:
    """Name the degeneration pattern at the end of ``tail``, or ``None``."""
    if len(tail) < _REPEAT_MIN_SPAN and not _DIGIT_RUN_RE.search(tail):
        return None
    if _exact_repeat(tail):
        return "exact_repeat"
    if _DIGIT_RUN_RE.search(tail):
        return "digit_run"
    if _counting(tail, min_numbers=_COUNTING_MIN_NUMBERS.get(channel, 300)):
        return "counting"
    return None


def _exact_repeat(tail: str) -> bool:
    if len(tail) < _REPEAT_MIN_SPAN:
        return False
    probe = tail[-_REPEAT_PROBE:]
    previous = tail.rfind(probe, 0, len(tail) - 1)
    if previous < 0:
        return False
    period = len(tail) - _REPEAT_PROBE - previous
    if period <= 0 or period > _REPEAT_MAX_PERIOD:
        return False
    count = max(_REPEAT_MIN_COUNT, -(-_REPEAT_MIN_SPAN // period))
    span = period * count
    if span > len(tail):
        return False
    unit = tail[-period:]
    return tail[-span:] == unit * count


def _counting(tail: str, *, min_numbers: int) -> bool:
    numbers = [int(m) for m in _INT_RE.findall(tail) if len(m) <= 18]
    if len(numbers) < min_numbers:
        return False
    if sum(c in _NUMBERISH for c in tail) / len(tail) < _COUNTING_DIGIT_SHARE:
        return False
    steps = [b - a for a, b in zip(numbers, numbers[1:])]
    same = sum(1 for a, b in zip(steps, steps[1:]) if a == b and a > 0)
    return same / max(1, len(steps) - 1) >= _COUNTING_STEP_SHARE
