"""Build a Freya phone agent from a bank brief.

    python builder/build.py <brief files...> <tool_api.json> [--slot NAME] [--spec SPEC.json] [--no-review] [--no-smoke]

Every brief file (md, txt, json, csv, eml...) is read as text. The last .json file whose content is an openapi document
is the tool API. Output: out/<slot>.spec.json, out/<slot>.workflow.json, out/<slot>.export.json, traces/<ts>-<slot>.jsonl.
"""
import argparse, json, os, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import freya
from builder import compile as compiler, extract, spec_schema, tools_gen, trace


def read_inputs(paths):
    docs, api = [], None
    for p in paths:
        path = pathlib.Path(p)
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".json":
            try:
                obj = json.loads(text)
                if isinstance(obj, dict) and "paths" in obj and ("openapi" in obj or "swagger" in obj):
                    api = obj
                    continue
            except Exception:
                pass
        docs.append((path.name, text))
    if api is None:
        raise SystemExit("no openapi tool API file among the inputs")
    return docs, api


def smoke(workflow_id, spec):
    """One chat turn: the workflow must start and answer. A bare 500 means it cannot start a call."""
    try:
        r = freya.api("POST", "/api/v2/chat/message", {"workflowId": workflow_id, "message": "Hello?",
                      "customerVariables": {"customer_id": "c_smoke", "first_name": "Alex", "last_name": "Smith", "title": "Mr",
                                            "now": time.strftime("%Y-%m-%dT%H:%M")}})
        reply = r.get("response") or ""
        if r.get("callId"):
            try:
                freya.api("POST", "/api/v2/chat/end", {"callId": r["callId"]})
            except Exception:
                pass
        return {"ok": bool(reply), "reply": reply[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--slot")
    ap.add_argument("--spec", help="use this spec instead of extracting one (development)")
    ap.add_argument("--digest", action="store_true", help="first consolidate the documents into one rulebook (slower)")
    ap.add_argument("--no-smoke", action="store_true")
    a = ap.parse_args()
    docs, api = read_inputs(a.inputs)
    slot = a.slot or pathlib.Path([p for p in a.inputs if not p.endswith(".json")][-1] if docs else a.inputs[0]).stem
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    trace.start(slot, ROOT)
    trace.log("inputs", files=[str(p) for p in a.inputs], docs=[n for n, _ in docs], tools=sorted(tools_gen.endpoints(api)))
    eps = tools_gen.endpoints(api)

    if a.spec:
        spec = spec_schema.apply_defaults(json.load(open(a.spec, encoding="utf-8")))
        trace.log("spec_loaded", path=a.spec)
    else:
        spec, rulebook, _ = extract.build_spec(docs, api, eps, use_digest=a.digest)
        if rulebook:
            (out / (slot + ".rulebook.md")).write_text(rulebook, encoding="utf-8")
    errs = spec_schema.validate_spec(spec, eps)
    (out / (slot + ".spec.json")).write_text(json.dumps(spec, indent=1), encoding="utf-8")
    trace.log("spec_ready", errors=errs, bands=[b.get("name") for b in spec.get("bands", [])],
              holidays=spec.get("calendar", {}).get("holidays"), questions=[q.get("field") for q in spec.get("identity", {}).get("questions", [])])

    wf = compiler.compile_workflow(spec, api)
    (out / (slot + ".workflow.json")).write_text(json.dumps(wf, indent=1), encoding="utf-8")
    trace.log("compiled", nodes=len(wf["nodes"]), bytes=len(json.dumps(wf)))
    v = freya.validate_workflow(slot, wf)
    trace.log("dry_run", result=v)
    ids = freya.save_workflow(slot, wf, state_file=str(ROOT / ".freya-agents.json"))
    trace.log("deployed", workflow_id=ids["workflow_id"], agent_id=ids["agent_id"], version=ids.get("version"))
    if not a.no_smoke:
        s = smoke(ids["workflow_id"], spec)
        trace.log("self_test", kind="chat smoke: one turn on the published workflow", **s)
    freya.export_workflow(ids["workflow_id"], str(out / (slot + ".export.json")))
    trace.log("exported", path=str(out / (slot + ".export.json")))
    trace.end(workflow_id=ids["workflow_id"], agent_id=ids["agent_id"])
    # everything a submission needs, in one folder: the exported phone agent, the spec it came from and this run's trace
    sub = ROOT / "submission" / slot
    sub.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy(out / (slot + ".export.json"), sub / (slot + ".workflow.json"))
    shutil.copy(out / (slot + ".spec.json"), sub / (slot + ".spec.json"))
    shutil.copy(trace._path, sub / pathlib.Path(trace._path).name)
    print(json.dumps(ids))


if __name__ == "__main__":
    main()
