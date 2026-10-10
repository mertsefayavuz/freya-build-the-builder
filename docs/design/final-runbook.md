# Final day runbook (Sunday 11 Oct 2026)

Hidden briefs open 17:00 UTC (20:00 Istanbul). Submission closes 20:00 UTC (23:00 Istanbul).
Rule: after 17:00 UTC only crash fixes in the builder. Nothing from a hidden brief goes into code or prompts.

## Before 17:00 UTC
1. `git pull` / `git status` clean; `python -m pytest tests` green.
2. `source env.sh`; check OpenRouter quota:
   `curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $OPENROUTER_API_KEY"` (free_model_daily_requests.remaining, resets 00:00 UTC).
3. LM Studio fallback ready: `openai/gpt-oss-20b`, context >= 32000, Max Concurrent Predictions = 1, GPU offload max.
4. Platform health: one chat turn on any workflow; bank mock `POST /v1/get_account` answers JSON (not error 1101).

## At 17:00 UTC
Put each hidden brief's files in its own folder, e.g. `final/brief-1/` (all documents + its tool API json).

```bash
source env.sh
python builder/build.py final/brief-1/* --slot final-1
python builder/build.py final/brief-2/* --slot final-2
```

If OpenRouter fails or is slow: `BUILDER_LLM=openai OPENAI_BASE_URL=http://localhost:1234/v1 BUILDER_MODEL=openai/gpt-oss-20b python builder/build.py ...`

Each run writes `submission/<slot>/` with the exported workflow, the spec and the trace.

## Check (read-only, no hand edits)
- `out/<slot>.spec.json`: holidays, bands, deadlines, identity fields, outcome map look like the brief.
- Optional: one chat test with `tools/chat_case.py` is not possible (no practice sessions for hidden briefs); use `--no-smoke` off (default) smoke test result in the trace.
- If something is wrong in the spec, the fix is a builder change that is generic (allowed only for crashes) or a rerun.

## Submit
```bash
git add submission/ && git commit -m "Final: workflows and traces" && git push
```
Form: team name, repository, commit, run command `python builder/build.py <brief files> <tool_api.json>`,
workflow files `submission/<slot>/<slot>.workflow.json`, trace files `submission/<slot>/*.jsonl`.
