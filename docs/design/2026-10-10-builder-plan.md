# Builder Implementation Plan

> Execution: native (implemented in this session), TDD for all pure-Python logic.

**Goal:** `python builder/build.py <brief...> <tool_api.json>` produces and publishes a Freya workflow that passes the practice cases 3/3.

**Architecture:** LLM extracts `spec.json` from the brief; a deterministic compiler emits the workflow; rule logic runs as generated Python inside the workflow (code nodes, `code_exec` tools, hooks), sourced from one runtime module that is unit-tested locally.

**Tech stack:** Python 3.10+ stdlib for builder core and runtime; `claude-agent-sdk` (optional) or OpenAI-compatible HTTP for the LLM; pytest.

**Spec:** `docs/design/2026-10-10-builder-design.md`

## Global constraints

- Phone agent model fixed (`freya.AGENT_LLM`); never send `agent_config`.
- Generated code: no `{{` / `}}`, never raises, reads/writes only `vars` (and `params` in code_exec tools). Embedded data is base64 JSON.
- No value from any brief in `builder/` source. Brief A/B values only in `tests/fixtures/`.
- Update workflows in place (`freya.save_workflow(slot, ...)`), one slot per brief name.
- Build run ≤ 30 min; target ≤ 8 min.

## Review focus (failure modes no single task covers)

1. Customer says a relative date ("next Friday", "the week after next") on a `now` that is a Friday / before a holiday -> calendar tests with several `now` values.
2. Tool API file with renamed/added fields or enum values -> tool generator test with a mutated openapi.
3. Multi-product: one product disputed, one paid, one promised -> outcome/notes test.
4. create_promise fails once, then twice -> runtime record-loop tests (retry once, service_error).
5. Raw-material brief where a memo overrides a number -> extraction test on a synthetic raw brief.

## File map

| Path | Responsibility |
|---|---|
| `builder/build.py` | CLI entry: normalize -> extract -> compile -> deploy -> smoke, writes trace |
| `builder/trace.py` | JSONL trace writer (start/end, model calls, ids, tests), key redaction |
| `builder/llm.py` | `complete_json(system, user, schema) -> dict` over Claude SDK or OpenAI-compatible endpoint |
| `builder/spec_schema.py` | Spec structure, defaults, `validate_spec(spec) -> list[str]` |
| `builder/extract.py` | `normalize(texts) -> str`, `extract_spec(text, openapi) -> dict` (focused LLM calls + validation loop) |
| `builder/runtime/core.py` | Pure logic run INSIDE the workflow: calendar, products, offers, attempts, records, outcome |
| `builder/codegen.py` | Wraps runtime source + spec into code snippets (`code_node`, `code_tool`, hooks) |
| `builder/tools_gen.py` | openapi -> Freya tool function JSON for each of the seven tools |
| `builder/compile.py` | spec + openapi -> workflow JSON (graph in design doc) |
| `builder/smoke.py` | chat-API smoke run against a practice session |
| `tests/` | pytest: runtime, codegen, tools_gen, compile, spec validation |
| `tests/fixtures/spec_a.json`, `spec_b.json` | hand-written golden specs for Brief A / A+B |

## Tasks

### Task 1: Repo + runtime calendar
- git init in kit root, `.gitignore` (env.sh, traces with keys, results, .freya-agents.json kept).
- `runtime/core.py`: `parse_now`, `is_business_day`, `add_business_days(n)`, `next_business_day_on_or_after`, `last_business_day_of_week`, `resolve_date_expr(expr, ctx)`, `calendar_block(now, days=35)`.
- Tests: Brief A on now=2026-10-14: late deadline 2026-10-23, attempt-1 date 2026-10-16, 26 Oct -> 27 Oct; holiday-aware; now on a Friday -> last business day of next week.

### Task 2: Runtime products, offers, attempts, records, outcome
- `prepare_products(products, spec, now) -> state` (spoken name, band, deadline, floor, attempts).
- `check_offer(state, pid, date, amount) -> verdict dict {ok, say}`; `customer_refused(state, pid)`; `set_result(state, pid, result, date, amount)`; `pending_records(state)`; `apply_record_result(state, pid, ok)`; `notes_to_write(state)`; `final_outcome(state, spec)`.
- Tests: all 13 public cases' expected promises/notes/outcomes reproduced from scripted decisions.

### Task 3: Codegen
- `code_node_snippet(entry, spec)`, `code_tool_snippet(entry, spec)`, `hook_snippet(entry, spec)`: runtime source + base64 spec + `vars` state (JSON string in `vars['st']`).
- Tests: generated snippet contains no `{{`/`}}`; exec it locally with fake `vars`/`params`; exceptions swallowed and fallbacks written.

### Task 4: Tool generator
- From openapi: url, body leaves with type/enum/description, `customer_id` baked, model-filled vs baked fields per call site.
- Tests: seven tools from kit openapi; mutated openapi (extra enum value, renamed field) propagates.

### Task 5: Spec schema + golden specs
- `spec_schema.py` with validation; `tests/fixtures/spec_a.json`, `spec_b.json` written by hand from the briefs.

### Task 6: Compiler
- Graph from design doc; every passthrough ends with `always`; max_node_turns + max_turn_transition on every conversation node.
- Tests: structure checks; `freya.validate_workflow` dry run (integration, marked).

### Task 7: Deploy + smoke + first simulation
- `smoke.py` via practice session + chat API. Run `run_cases.py --brief A` with the compiled golden spec. Iterate on prompts/graph until stable.

### Task 8: LLM layer + extractor + build.py + trace
- Extract spec from brief A/B; diff against golden specs; validation-retry loop.
- `build.py` end to end, trace file per run.

### Task 9: Hardening
- `--brief B`, `--style` variants, `--reps 3`; synthetic raw-material brief; second calendar day (`now` edits in a copied case file).
