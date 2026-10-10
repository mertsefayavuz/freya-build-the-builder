# The builder

```bash
source env.sh
python builder/build.py <brief files...> <tool_api.json>
```

Reads any number of brief documents (clean spec or raw material: mails, policy extracts, memos, code lists) and the bank's
openapi tool file, and creates/updates one Freya workflow. Outputs `out/<slot>.spec.json`, `out/<slot>.workflow.json`,
`out/<slot>.export.json` and the trace `traces/<start>-<slot>.jsonl`. `<slot>` defaults to the last brief's file name.

## How it works

```text
documents + openapi
  extract.py   LLM, 7 small parallel calls -> spec.json (validated; failing sections are repaired with the errors)
  compile.py   no LLM: spec + openapi -> workflow (fixed, tested call graph)
  runtime/     the bank's rules as Python that runs INSIDE the workflow (code steps, code_exec tools)
  freya.py     save + publish in place, dry run, chat smoke test, export
```

Principle: **code decides, the phone agent talks.** The phone agent model is small, so every rule that can be computed is
computed in the workflow: business days and holidays, deadlines per band, weekend/holiday shifts, persuasion attempts and their
offers, floors, callback windows, read-back text, recording with one retry, notes, outcome and reason. The model only talks and
calls tools; tools answer with the exact next instruction. Exits with a fixed outcome close the call without any code step.

The LLM never writes the workflow. It only fills the spec (`spec_sections.py` describes every field). Same spec, same workflow.

## LLM backends (`builder/llm.py`)

| `BUILDER_LLM` | Settings |
|---|---|
| `openrouter` (default when `OPENROUTER_API_KEY` is set) | `BUILDER_MODEL` = comma-separated models, tried in order |
| `openai` | any OpenAI-compatible endpoint: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `BUILDER_MODEL` (e.g. LM Studio) |
| `claude` | Claude through `claude-agent-sdk` |

## Options

- `--spec FILE` compile a given spec (no LLM), `--digest` consolidate the documents first, `--no-smoke` skip the chat smoke test.
- `BUILDER_ID_HOOK=0` drop the identity post-hook and rely on prompt rules only (faster, but the model sometimes ignores a wrong answer).

## Tests

`python -m pytest tests` - runtime rules against the 13 practice cases' expected ledgers, compiler graph checks,
generated code compiles and runs, tool generation follows a changed openapi. `tools/spec_diff.py` compares an extracted
spec with a golden one; `tests/raw_brief/` is a synthetic raw-material brief with a memo that overrides four rules.
