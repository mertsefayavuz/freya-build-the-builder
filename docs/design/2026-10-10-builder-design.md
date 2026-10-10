# Builder design (2026-10-10)

## Goal

`python builder/build.py <brief...> <tool_api.json>` turns a bank brief (clean spec or raw material) into a Freya
workflow that passes every simulated customer 3 runs out of 3. Secondary goals: short build time and a full trace.

## Principle

The phone agent model (`experimental-5`) is weak and non-deterministic. So:

- **Code decides, the model talks.** Every rule that can be computed (dates, deadlines, floors, attempt
  schedule, callback window, outcome/reason, notes, honesty about tool results) is computed in Python inside the
  workflow (code nodes, `code_exec` tools, pre/post hooks). The model gets finished facts and one instruction.
- **The builder LLM only reads.** An LLM turns the brief into a validated `spec.json`. A deterministic compiler
  (no LLM) turns spec + openapi into the workflow. Same spec, same workflow.

## Pipeline

```text
brief(s) + tool_api.json
  [1] normalize   LLM   raw material (mail, memo, code list) -> one clean rule text, memo overrides applied
  [2] extract     LLM   clean text -> spec.json (several focused calls, JSON schema, validated, retried with errors)
  [3] compile     code  spec + openapi -> workflow.json (node templates + generated Python)
  [4] deploy      code  freya.save_workflow (update in place + publish)
  [5] smoke       code  chat API: workflow starts, opening line correct, identity step reached
  -> traces/<ts>.jsonl, out/<slot>.spec.json, out/<slot>.workflow.json
```

LLM access is behind `builder/llm.py` with two backends: Claude via `claude-agent-sdk` (subscription login,
default) and any OpenAI-compatible endpoint (LM Studio or API). Selected by env vars.

## Spec (what the extractor must fill)

All values come from the brief. Nothing brief-specific lives in builder code.

- `calendar`: working weekdays, holidays (ISO dates), timezone note.
- `opening`: first sentence facts (bank name, assistant identity, recorded), confirm question template,
  words forbidden before verification, accepted confirmations, max asks before `unclear`.
- `third_party`: what may be said, callback window (days rule, start/end time, inclusive, later than now),
  re-ask count, outcomes for callback / no_callback / wrong_number / deceased (+ exact phrase).
- `identity`: ordered questions (`field` value for the tool, how to ask, answer format, date order rule), max wrong
  answers total, hint values and what to do, decline handling (explain once), outcomes failed / declined, phrase on failure.
- `debt_statement`: what to say after verification (team name, product naming per type, amount, days late).
- `products`: per type: spoken name template, floor field.
- `bands`: list of `{min_days, max_days, deadline: {kind: nth_business_day, n}, attempts: [offer...]}` where an offer
  is `{date: expr, amount: expr}`; date exprs: `today`, `last_business_day_this_week_or_next`, `deadline`,
  `nth_business_day:N`; amount exprs: `full`, `floor`, `floor_if_full_refused`.
- `promise_rules`: first ask phrase, date rules (business day, not before today, not after deadline, weekend/holiday
  -> next business day, vague -> ask once then deadline, after deadline -> offer deadline), amount rules (full first,
  floor once, accept between floor and full, below floor -> floor once), never ask "how much".
- `confirmation`: read-back phrase, confirm question, retry count on failure, phrase after success, failure outcome.
- `special_cases`: list of `{id, trigger, say, action: {tool, args}, outcome, reason, scope: call|product,
  allowed_before_verification}` (paid, dispute, hardship, restructuring, human, scam, robot, not_talking, ...).
- `multi_product`: order, single read-back, notes rules, transfer-means-no-notes, closing priority.
- `closing`: outcome table (outcome, reason, when), rule for no-promise reason, farewell phrase, close tool last.
- `extra_rules`: anything that does not fit, as text, tagged with the stage it belongs to.

## Phone agent graph (compiled)

```text
contact(conv, initial) --confirmed--> setup(code) --> identity(conv) --all_verified--> load_account(api) --> prep(code)
   |third_party -> setup -> third_party(conv) -> [check_callback tool] -> book_callback(api) | no_callback
   |wrong_number / deceased / unclear / declined -> set_outcome
prep --> negotiate(conv) --all products decided--> readback(conv) --yes--> record(api create_promise loop, retry)
     --> notes(api add_note loop) --> wrap(conv, passthrough, fixed text built by code) --> set_outcome
negotiate --transfer ok--> set_outcome
set_outcome(code) --> farewell(conv, passthrough, tts) --> close(api close_call) --> end(end_call, silent)
```

- **contact**: fixed TTS opening from spec. Extraction enum `contact_status` (confirmed, third_party, wrong_number,
  deceased, not_talking, unclear). Deterministic transitions; `contact.current_turns` caps the asks (unclear).
- **setup** runs after the first user turn (the chat API skips an initial code node). It writes a calendar block
  (`today`, next ~30 days with weekday and business/holiday flags) used by all later prompts.
- **identity**: `verify_identity` tool generated from openapi; a post-hook adds `what_to_do_next` (next question,
  re-ask, month-as-word on hint, stop). Exits on `verify_identity_response.data.all_verified`, on
  `wrong_answers_left == 0`, or on a second refusal (extraction enum).
- **prep**: from `get_account` products computes per product: spoken name, band, deadline, floor, attempt schedule
  with concrete dates/amounts; writes `debt_statement` and `negotiation_state` text.
- **negotiate**: model talks; `code_exec` tools decide:
  `check_offer(product_id, date, amount)` -> verdict and the exact next sentence;
  `customer_refused(product_id)` -> next attempt offer or "attempts used up";
  `set_result(product_id, result)` for agreed / refused / hardship / dispute / paid.
  `transfer_to_agent` (api) for restructuring / human / support. Code re-renders `negotiation_state` after every tool.
- **record / notes**: engine-run api nodes looping over pending items, retry once on failure, honest per-item status.
- **wrap**: TTS text built by code from real results (recorded / not recorded + SMS phrase). Honesty by construction.
- **set_outcome**: outcome and reason from the spec's closing table and the call state. close_call runs exactly once,
  after the farewell, as the last tool call.

## Verified platform facts (spikes, 10 Oct)

- Extraction `type: date` resolves relative dates on the **real clock**, not `now`. Do not use it for relative dates;
  the model converts with the calendar block and code validates.
- A `code_exec` tool gets the model's arguments in the global `params` (no lag). Its result to the model is always
  "Code executed successfully"; values reach the model through `vars` and `{{var}}` in the prompt, same turn.
  `_result` assignment has no effect. Tell the model never to read tool results aloud.
- Chat API skips an initial code node, but runs later code / api nodes.
- `conversation` node with `passthrough: true` and TTS on_enter speaks, then routes: farewell before close_call works.
- Routing on the first user turn is suppressed ("Missing prior assistant message") when the node spoke via TTS.

## Testing

- Unit tests (pytest) for compiler, calendar maths and generated Python (run the generated snippets locally with a fake `vars`).
- Chat-API scenario tests against practice sessions (`/v1/practice/sessions`) for fast iteration.
- `tools/run_cases.py` simulations on Brief A and B, plus `--style` variants, `--reps 3` before the final.
- A synthetic raw-material version of Brief A/B to test normalize + extract.

## Risks

- A hidden brief with a structure outside the spec: covered by `extra_rules` text and generic special cases.
- Whether `code_exec` tools count as allowed "code steps": rules allow code steps and hooks; asked organisers.
- Extraction enums on the weak model: keep enums small, descriptions explicit, `min_confidence` ~0.5.
