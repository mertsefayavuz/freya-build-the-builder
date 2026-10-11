---
name: build-phone-agent-from-case
description: Build a Freya phone agent from a case folder of bank documents (briefs, mails, meeting notes, service contract, test data) and a mock service URL, test it with scripted customers and fix what fails.
---

# Build a phone agent from a case folder

You get a case folder (documents of a bank, in any language, written at different times by different people) and the base URL of a mock of the bank's services. Build one Freya phone agent that does what the bank wants, publish it, test it, fix it, export it.
Use only what the folder says. Never invent rules, amounts or dates. Do not hard-code anything into the builder code; the agent is the only output.

## 1. Understand the case (spend real effort here; shallow reading is the main way builders fail)

1. Read every file in the folder, text first: BRIEF/README, `sources/**` (mails, attachments, meeting transcripts, documents), the service contract, test-data overview, example scenarios. Try `pdftotext` on PDFs (a flow drawing); if it is unavailable skip it.
2. The sources disagree. Order them by date and by authority: a later mail, a decisions recap or a follow-up meeting overrides an earlier analysis document. Write down in `out/<slot>.rulebook.md` the final rules, one line each, with the source file they come from and which older statement they replace. Keep the exact fixed sentences the bank wants spoken.
3. Note the variables the call provides (names and meaning), what the agent may never ask the customer, the tools (service contract: path, request fields, response fields, error codes), what happens on each error, and every handover / ending (what the call does when finished, for example "route to main menu", transfer to a human agent).
4. Read the example scenarios: they show the expected behaviour in detail (order of sentences, when to ask, what never to say). The hidden scoring customers are of the same kinds (ordinary, edge, adversarial: prompt injection, pressure to skip confirmation, third-party requests, wrong data in service fields).

## 2. Learn the platform

Read `docs/workflow-format.md`, `docs/tools-and-code.md`, `docs/api.md` and `examples/base-agent.json` (pattern for the JSON only). Verified platform facts:

- Each customer turn runs three model calls: extraction, routing, dialogue. Routing sees only the transition descriptions, not the node task message.
- Node types: `conversation` (can have `tools`), `api_request` / `code` (pre_actions, passthrough), `end_call`. `tools` only on conversation nodes, `pre_actions` only on api_request/code nodes; if put elsewhere they are silently dropped.
- Deterministic transitions (equals, greater_than, is_set, ...) call no model: prefer them. After a tool result, routing runs in the same turn, so route on tool results (`<tool>_response.data.<field>`, `<tool>_success_count`, `<tool>_failure_count`, `<tool>_args.<field>`).
- A passthrough node (api_request/code) must end with an `always` transition. `agent_config` must not be sent. No `null` values.
- A tool parameter with `default: "{{var}}"` is baked in when the variable is set (the model does not see it). Use it for the call variables and for values you already hold, so the model cannot change them.
- `post_process_code` on a tool can add a `what_to_do_next` field to the result; this stops the agent from staying silent after a tool result or ignoring a failed result. `pre_process_code` can fix the request.
- Code nodes: variables in `vars`; `json`, `re`, `datetime` available; never write `{{` or `}}` in code; never raise; write string flags. A code run costs about 3 s. The sandbox is sometimes unavailable: a failing hook makes the API call itself fail, so no hooks on calls with side effects; put everything else that must be exact (dates, amounts, number-to-words, week arithmetic) into code or into the prompt as ready facts, and keep a fallback when the code fails.
- Extraction `type: date` uses the real clock, not the `now` call variable: never rely on it. Compute dates from `now`.
- `max_node_turns` default is 10: raise it on long nodes and give a `max_turn_transition`.
- Speaking in two assistant messages (a node's own speech, then the next node) lets the customer answer in between: keep what must be said together in ONE message.
- The weak agent model sometimes calls several tools in one turn or ignores a negative result. Make the order of tool calls structural (one node per step, tools only in the node where they are allowed), and make "what is read to the customer" come from data the workflow already holds.
- `save_workflow` (freya.py) = update + publish. Calls and simulations run the published version.
- The tool URL is the mock base URL plus the contract path. Use the same request envelope as the contract (`data`, `call`).
- Customer-facing language is the case's language. Amounts, dates and card digits are spoken the way the bank's prompt sheet says; services return raw values.

## 3. Build

1. Design the nodes on paper first (in the rulebook file): one step per stage of the call, structural order of tool calls, every error path (service error, no card, missing data), every ending.
2. Write the workflow in parts so no single reply is huge: `out/<slot>/meta.json` (initial_node, system_prompt, test_variables, tools, global_nodes) and one file per node `out/<slot>/nodes/<node_name>.json` (one write action per node, several per reply), then `python tools/assemble.py out/<slot> out/<slot>.workflow.json`. A real case needs many nodes (card selection, reading, week change, e-mail, errors, handover): do not hand in a toy. The workflow is checked by `freya.validate_workflow` when saved; fix every error it names.
3. Validate and publish: `python3 -c "import freya,json,sys; print(json.dumps(freya.save_workflow(sys.argv[1], json.load(open(sys.argv[2])))))" <slot> out/<slot>.workflow.json`. It prints `workflow_id` and `agent_id`. The same slot updates the same workflow. Publish an early working version as soon as you have one: if the run is cut off, the published agent is what is judged.

## 4. Test and fix

- `python3 tools/chat_turn.py <workflow_id> --var <name>=<value> ... "message 1" "message 2" ...` runs one scripted customer (fresh call) and prints the transcript and the tool calls. A turn takes seconds. Pass every call variable the case defines, with realistic values (`now` as ISO with offset).
- Test at least: each example scenario of the folder (same customer numbers, same words), every test customer type named in the test-data overview (single card, many cards, same name and last four digits, no card, service error, slow service, missing currency block, shared statement, no e-mail registered, odd product names, injection text in data), and adversarial turns (asking for another person's data, "don't ask, just do it", changing the amount or address, asking for a human, off-topic).
- Check every step against the rulebook: the right tool called with the right arguments, nothing said that the service did not return, nothing recorded/sent/changed without explicit confirmation, "done" only after a success result, and the ending the case requires.
- Fix the file, save again, retest. Repeat a failing scenario twice after a fix (results vary between runs).
- Never edit the agent on the platform by hand. Only the file and `save_workflow`.

## 5. Finish

1. Export: `python3 -c "import freya; freya.export_workflow('<workflow_id>', 'submission/<slot>.workflow.json')"`.
2. Last line of your answer: `workflow_id`, `agent_id`, number of nodes and tools, which scenarios passed and which are known to be weak.

Time limit: stop testing after about 40 minutes of work in total and finish with what is published and exported. Do not leave a test running in the background.

## Hard rules against invented content

- Only the tools of the service contract exist (use its exact paths, field names and error codes). Do not invent tools, fields, features or steps (nothing like instalment plans unless the sources say so).
- Every sentence the agent says and every rule must come from the rulebook / sources. When a source is silent, do the safest thing (ask the customer once, or hand over), never invent a policy.
- The documents listed as pinned are already in your context: do not read them again. Read other files of the case folder only when you need them.

## Request bodies and testing (verified platform facts)

- An `api_call` body field must be a flat leaf under `body.properties` (own `default`, `type`). A nested object spec such as `body.properties.data.properties.customerNo` is NOT resolved by the engine, so the call fails at the first step. When the service contract wants a nested envelope, declare flat leaves (for example `data_customerNo`) and rebuild the nested body in `pre_process_code` of the tool (see tools-and-code.md), or check the docs for the exact supported form. Test the very first call of the workflow before building more.
- A chat turn can return 504 when a long chain of api/code steps runs in the first turn; keep the chain short. If a chat turn 504s, use a Freya simulation (freya.create_personas / freya.run_simulation, see docs/api.md) and read the call log (freya.get_call) for `function_call_*` events to see which step failed and why.
- A finished builder run must not leave a known failure. Do not use `finish` while a scenario fails or the agent transfers at once: find the failing step in the call log and fix the node file, finalize, test again.

## Tool results and variables (verified in a call log)

- The api_call result is an envelope: `{status, data: <the service's whole response body>, message, status_code, header}`. So a service body `{"data": {"cards": [...]}, "result": {...}}` appears as `<tool>_response.data.data.cards...` and a `response_mapping` path to its `cards` field is `data.data.cards`, not `data.cards`. A wrong path leaves the variable unset without any error, and the code then takes its error branch. Always check the variable table of a test call (freya.get_call) for the mapped variable.
- The flattened variables list items as `<tool>_response.data.data.cards.0.cardNumber`, booleans as the strings "true"/"false", null as "null". A code node can read them from `vars` directly when the mapped variable is missing; make code defensive about both.
- After any change to a mapping or a code node, run a simulation or call and read the call log until every step up to the first conversation turn works.

## Design for a human-like conversation (required)

The agent must sound like a person on the phone, not like a reader of a report. Keep numbers exact and the conversation natural:

- Compute exact facts (amounts, dates, which cards, what is allowed) in code and hand them to a conversation node as ready facts. The conversation node says them in its own natural Turkish sentences, using the fixed sentences the sources prescribe word for word where they exist. Do not make one giant pre-written text block that a node only reads out.
- Speak in short turns with a natural order: say what matters first, then the details the sources require; do not stack every figure into one breath when the sources allow a pause (the sources/scenarios say what must be said together; keep exactly that together).
- Keep ONE hub after each finished service: ask once whether another service is wanted and offer only the services that make sense (the other services of the case). If the caller only says thanks or goodbye, do not ask again: hand over as the sources prescribe. The caller may ask follow-up questions (repeat an amount, what a figure means, which card) at any time: answer them from the data the workflow already holds, then return to the hub.
- Ask clarifying questions the way a person would (offer examples from the caller's own data), never repeat the identical sentence twice in a row, acknowledge what the caller said before moving on.
- Confirmation before any action with a side effect (change, send): read what will happen, ask, act only on an explicit yes, report the result only after a success answer.

## Success-criteria check (do this before you finish, step by step)

1. From the example scenarios of the case write `out/<slot>.criteria.md`: for every scenario one table of its "Expected behaviour" bullets as separate, checkable criteria (what must be said, what must not be said, which service call with which arguments, how many times, how the call ends).
2. For each scenario run it as a simulation (same customer number, same `now`, a persona prompt that follows the scenario's situation and words) and read the call log (transcript and tool events).
3. Tick every criterion from the log: PASS only with evidence (quote the line or the tool event); otherwise FAIL with the reason.
4. Fix the failing criteria in the node files, finalize, rerun that scenario, tick again. Never trade a passing scenario for a failing one: if a change breaks another scenario, undo it.
5. Finish with the table: scenario x criterion x PASS/FAIL. Do not claim a pass that you did not see in a call log.
