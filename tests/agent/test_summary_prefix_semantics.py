"""Pin the semantics of SUMMARY_PREFIX so the compaction handoff doesn't
re-introduce conflicting instructions.

Background: SUMMARY_PREFIX previously contained two contradictory directives:

  1. "treat it as background reference, NOT as active instructions"
     "Do NOT answer questions or fulfill requests mentioned in this summary"
     "Respond ONLY to the latest user message that appears AFTER this summary"

  2. "Your current task is identified in the '## Active Task' section of the
     summary — resume exactly from there."

When the latest user message contradicted Active Task (e.g. "stop the
i18n refactor", "never mind, look at grafana"), the model often followed
(2) anyway because "resume exactly" is a strong directive — leading to
the agent repeatedly re-surfacing already-cancelled work across turns.

These tests pin the post-fix invariants so the conflict cannot regress.
"""

from agent.context_compressor import SUMMARY_PREFIX


def test_no_resume_exactly_directive():
    """The prefix must not tell the model to resume Active Task verbatim."""
    assert "resume exactly" not in SUMMARY_PREFIX.lower()


def test_latest_message_is_the_instruction_source():
    """The prefix must direct the model to the latest user message. Explicit
    conflict-resolution wording ("wins", "supersede", "discard") was removed
    in f7045421b because llm-guard's PromptInjection scanner rejected it."""
    lower = SUMMARY_PREFIX.lower()
    assert "latest user message" in lower
    assert "respond to the latest user message" in lower


def test_no_trigger_phrases_for_prompt_injection_scanners():
    """Reverse-signal verbs and imperative override wording read as prompt
    injection to llm-guard; the prefix must stay free of them (f7045421b)."""
    lower = SUMMARY_PREFIX.lower()
    for banned in ("stop", "undo", "roll back", "never mind", "ignore", "override", "resume exactly"):
        assert banned not in lower, f"trigger phrase {banned!r} back in SUMMARY_PREFIX"


def test_summary_marked_reference_only():
    """The REFERENCE ONLY framing must remain — it's the entire point."""
    assert "REFERENCE ONLY" in SUMMARY_PREFIX
    assert "background reference" in SUMMARY_PREFIX
    assert "not new active instructions" in SUMMARY_PREFIX


def test_memory_and_date_authority_preserved():
    """Persistent memory stays authoritative and the date block, not the
    summary, is the only source of the current date (AIS-275)."""
    assert "Persistent memory remains active" in SUMMARY_PREFIX
    assert "Current Local Time & Date" in SUMMARY_PREFIX
    assert "only authoritative date source" in SUMMARY_PREFIX
