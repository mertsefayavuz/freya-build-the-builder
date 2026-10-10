"""Brief documents -> validated spec (LLM). Three steps:

1. digest   all documents -> one consolidated rulebook (later memos override earlier text, codes resolved).
2. spec     rulebook + documents + tool API -> spec JSON (schema in spec_schema.SCHEMA_DOC), validated, repaired with the errors.
3. review   documents + spec -> corrected spec (catches skipped rules and wrong numbers).
"""
import json

from . import llm, spec_schema, trace

DIGEST_SYSTEM = """You are a senior analyst preparing a bank's phone-collections requirements for an engineering team.
You receive the bank's material: a clean specification, or raw material such as mails, policy extracts, memos and code lists.
Write ONE consolidated rulebook that an engineer can implement without reading the originals.

Rules:
- Keep every rule, number, limit, date, holiday, time window, list, outcome/reason code and every sentence the agent must say word for word (quote it).
- When a later memo or mail changes an earlier rule, apply the change and keep only the final rule. Note in brackets which document won.
- Resolve code lists (e.g. outcome codes, reason codes, queue names) to the exact values the bank's tool API uses.
- Keep the structure of a call: opening and who is on the line; someone else answers; identity check; stating the debt; dates and deadlines;
  amounts and floors; getting the promise and persuasion attempts; confirming and recording; special situations; multiple products; closing and outcomes.
- If something is ambiguous, state the most literal reading and mark it [ambiguous].
- Do not invent rules. Do not summarise away details. Plain text, numbered rules."""

SPEC_SYSTEM = """You turn a bank's phone-collections rulebook into a JSON spec that a compiler turns into a phone agent.
The compiler already implements this call structure: opening and confirming the person; third party with callback booking;
identity questions through the verify tool; reading the account; stating each late product; negotiating per product with code that
applies date/amount/attempt rules; one read-back and confirmation; recording promises (with retry); notes; closing with exactly one
close tool call. Your job is to fill the spec with the bank's values and rules exactly.

Return ONLY the JSON object, no prose.

SPEC FORMAT:
""" + spec_schema.SCHEMA_DOC

REVIEW_SYSTEM = """You audit a JSON spec for a bank's phone agent against the bank's documents.
Check every rule, number, date, holiday, time window, band, deadline, attempt, floor, identity question, exact sentence, outcome/reason code
and special case. Fix anything wrong or missing in the spec. Keep the spec format exactly (described below).
Return ONLY the full corrected JSON object.

SPEC FORMAT:
""" + spec_schema.SCHEMA_DOC


def _docs_block(docs):
    return "\n\n".join("=== DOCUMENT: %s ===\n%s" % (name, text) for name, text in docs)


def _api_block(api):
    return "=== TOOL API (openapi) ===\n" + json.dumps(api, indent=1)


def digest(docs, api):
    user = _docs_block(docs) + "\n\n" + _api_block(api) + "\n\nWrite the consolidated rulebook now."
    return llm.complete(DIGEST_SYSTEM, user, "digest brief documents into one rulebook")


def _parse(text):
    try:
        return llm.extract_json(text), None
    except Exception as e:
        return None, "the reply was not valid JSON: %r" % e


def build_spec(docs, api, endpoints, rulebook, review=True, max_repairs=2):
    user = ("=== RULEBOOK ===\n" + rulebook + "\n\n" + _docs_block(docs) + "\n\n" + _api_block(api) +
            "\n\nReturn the spec JSON now.")
    text = llm.complete(SPEC_SYSTEM, user, "rulebook -> spec JSON")
    spec, err = _parse(text)
    for i in range(max_repairs + 1):
        errs = [err] if err else spec_schema.validate_spec(spec_schema.apply_defaults(json.loads(json.dumps(spec))), endpoints)
        trace.log("spec_validation", round=i, errors=errs)
        if not errs:
            break
        if i == max_repairs:
            raise RuntimeError("spec still invalid: %s" % errs)
        fix = (user + "\n\n=== YOUR PREVIOUS SPEC ===\n" + (text if err else json.dumps(spec, indent=1)) +
               "\n\n=== PROBLEMS ===\n- " + "\n- ".join(errs) + "\n\nReturn the full corrected spec JSON.")
        text = llm.complete(SPEC_SYSTEM, fix, "repair spec JSON (round %d)" % (i + 1))
        spec, err = _parse(text)
    if review:
        rtext = llm.complete(REVIEW_SYSTEM, _docs_block(docs) + "\n\n" + _api_block(api) + "\n\n=== SPEC TO AUDIT ===\n" +
                             json.dumps(spec, indent=1), "audit spec against the documents")
        rspec, rerr = _parse(rtext)
        if rspec is not None:
            rerrs = spec_schema.validate_spec(spec_schema.apply_defaults(json.loads(json.dumps(rspec))), endpoints)
            trace.log("spec_review", errors=rerrs, accepted=not rerrs)
            if not rerrs:
                spec = rspec
        else:
            trace.log("spec_review", errors=[rerr], accepted=False)
    return spec_schema.apply_defaults(spec)
