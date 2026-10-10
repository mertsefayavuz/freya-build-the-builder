"""Brief documents -> validated spec (LLM), in small focused calls.

1. (optional) digest: all documents -> one consolidated rulebook. Helps weak models with raw material.
2. sections: one call per spec section (money, people, closing, texts, rules), run in parallel.
3. validate the merged spec; repair only the sections that have errors, with the errors.
"""
import concurrent.futures as cf
import json, os

from . import llm, spec_schema, spec_sections, trace

READING_RULES = """How to read the documents:
- They may be a clean specification or raw bank material (mails, policy extracts, memos, code lists).
- When a later memo or mail changes an earlier rule, the later one wins. Use code lists to map names to the exact values the tools use.
- Take tool field names and allowed values from the TOOL API part exactly.
- Never invent values. If the documents say nothing, keep the default."""

DIGEST_SYSTEM = """You are a senior analyst preparing a bank's phone-collections requirements for an engineering team.
Write ONE consolidated rulebook that an engineer can implement without reading the originals.
- Keep every rule, number, limit, date, holiday, time window, list, outcome/reason code and every sentence the agent must say word for word (quote it).
- When a later memo or mail changes an earlier rule, apply the change and keep only the final rule.
- Resolve code lists to the exact values the bank's tool API uses.
- Do not invent rules. Do not summarise away details. Plain text, numbered rules."""


def _docs_block(docs):
    return "\n\n".join("=== DOCUMENT: %s ===\n%s" % (name, text) for name, text in docs)


def _api_block(api, tools=None):
    if tools:
        paths = {p: v for p, v in api.get("paths", {}).items() if p.strip("/").split("/")[-1] in tools}
        api = dict(api, paths=paths)
    return "=== TOOL API (openapi) ===\n" + json.dumps(api, separators=(",", ":"))


def _section_system(name):
    sec = spec_sections.SECTIONS[name]
    return ("You fill one section of a JSON spec for a bank's outbound phone-collections agent.\n" + READING_RULES + "\n\n" +
            spec_sections.COMMON + "\n\nFIELDS:\n" + sec["doc"].strip() + "\n\nSKELETON (shape only, replace every value):\n" +
            json.dumps(sec["skeleton"]))


def _parse(text):
    try:
        obj = llm.extract_json(text)
        return (obj, None) if isinstance(obj, dict) else (None, "the reply was not a JSON object")
    except Exception as e:
        return None, "the reply was not valid JSON: %r" % e


def digest(docs, api):
    user = _docs_block(docs) + "\n\n" + _api_block(api) + "\n\nWrite the consolidated rulebook now."
    return llm.complete(DIGEST_SYSTEM, user, "digest brief documents into one rulebook")


def _material(docs, rulebook):
    return ("=== CONSOLIDATED RULEBOOK ===\n" + rulebook + "\n\n" if rulebook else "") + _docs_block(docs)


def run_section(name, docs, api, rulebook, prev=None, errors=None):
    sec = spec_sections.SECTIONS[name]
    user = _material(docs, rulebook) + "\n\n" + _api_block(api, sec["tools"] or None)
    if prev is not None:
        user += "\n\n=== YOUR PREVIOUS ANSWER ===\n" + json.dumps(prev) + "\n\n=== PROBLEMS TO FIX ===\n- " + "\n- ".join(errors)
        purpose = "repair spec section '%s'" % name
    else:
        purpose = "extract spec section '%s'" % name
    user += "\n\nReturn the JSON object for this section now."
    for attempt in range(2):
        text = llm.complete(_section_system(name), user, purpose)
        obj, err = _parse(text)
        if obj is not None:
            missing = [k for k in sec["skeleton"] if k not in obj]
            if not missing:
                return obj
            err = "missing keys: %s" % missing
        trace.log("section_parse_error", section=name, attempt=attempt, error=err)
        user += "\n\nYour last reply had a problem: %s. Return ONLY the complete JSON object." % err
    raise RuntimeError("section %s: no usable JSON" % name)


def build_spec(docs, api, endpoints, use_digest=False, max_repairs=2):
    rulebook = digest(docs, api) if use_digest else ""
    parallel = int(os.environ.get("BUILDER_PARALLEL", "5" if llm.BACKEND != "openai" else "1"))
    names = list(spec_sections.SECTIONS)
    parts = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        futs = {ex.submit(run_section, n, docs, api, rulebook): n for n in names}
        for f in cf.as_completed(futs):
            parts[futs[f]] = f.result()

    def merged():
        spec = {}
        for n in names:
            spec.update(parts[n])
        return spec

    for rnd in range(max_repairs + 1):
        spec = spec_schema.apply_defaults(merged())
        errs = spec_schema.validate_spec(spec, endpoints)
        trace.log("spec_validation", round=rnd, errors=errs)
        if not errs or rnd == max_repairs:
            break
        by_sec = {}
        for e in errs:
            by_sec.setdefault(spec_sections.section_of_error(e), []).append(e)
        for n, es in by_sec.items():
            parts[n] = run_section(n, docs, api, rulebook, prev=parts[n], errors=es)
    return spec, rulebook, errs
