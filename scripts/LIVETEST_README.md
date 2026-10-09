# Tool Search live test harness

Runs the scenarios below against a real model on an OpenAI-compatible endpoint
(default: the IAMDS vLLM at `https://vllm.iamds.com`) to verify that the
bridge tools work end-to-end. Records transcripts in `scripts/out/`.

## Running

```bash
cd <repo root>
python3 scripts/tool_search_livetest.py        # runs all 5 scenarios x 2 modes
python3 scripts/analyze_livetest.py            # side-by-side report
```

### Target model

| Env | Default | Meaning |
|-----|---------|---------|
| `LIVETEST_BASE_URL` | `VLLM_IAMDS_BASE_URL`, then `https://vllm.iamds.com` | OpenAI-compatible base URL (`/v1` appended if missing) |
| `LIVETEST_MODEL` | first id from `GET <base_url>/models` | model id to test |
| `LIVETEST_API_KEY` | `VLLM_IAMDS_API_KEY` | bearer key (for an openrouter.ai base URL `OPENROUTER_API_KEY` is also accepted) |

Keys may also live in `~/.hermes/.env`; the harness loads it before resolving
the target. The key is passed to the agent directly and redacted from every
transcript.

```bash
# IAMDS vLLM (default)
VLLM_IAMDS_API_KEY=... python3 scripts/tool_search_livetest.py

# OpenRouter, as in the 2026-05 baseline
LIVETEST_BASE_URL=https://openrouter.ai/api/v1 \
LIVETEST_MODEL=anthropic/claude-haiku-4.5 \
OPENROUTER_API_KEY=... python3 scripts/tool_search_livetest.py
```

These are the same names the test suite's `live_llm` fixture reads when it
is opted into a real vLLM locally (`HERMES_LIVE_TESTS=1`; CI always uses the
loopback mock, AIS-532). The harness is not wired into CI.

## What it verifies

| Scenario | Tests |
|----------|-------|
| A obvious_single | BM25 retrieval on an obvious tool name (github_create_issue) |
| B vague_paraphrased | Retrieval when the model has to paraphrase ("schedule meeting" → evt_create) |
| C multi_tool_chain | Multi-step task chaining two deferred tools (GitHub + Slack) |
| D core_plus_deferred | Mixed: core tool (read_file) called directly, deferred tool (Slack) via bridge |
| E no_tool_needed | Pure-knowledge prompt; verify no spurious tool_search invocations |
| F search_then_direct_call | tool_search surfaces a deferred tool, then the model calls it by name (no tool_call wrapper) — `direct_deferred_calls` must be non-empty in the enabled run |

Each scenario runs with `tool_search.enabled = on` and again with `off` for an
A/B baseline. The harness records:

- bridge_calls (the tool_search / tool_describe / tool_call sequence the model emitted)
- underlying_tool_calls (what actually ran through the registry dispatcher)
- final_response, iteration count, elapsed time, any errors

## Output structure

```
scripts/out/
  <scenario>__enabled.json    # tool_search ON
  <scenario>__disabled.json   # tool_search OFF
  _summary.json               # one-line summary across all runs
```

The 2026-05 baseline run is checked in for reference. Re-running may produce
slightly different transcripts (the model is non-deterministic) but the
expected_underlying_tools assertions should remain satisfied.
