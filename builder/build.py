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


def expand_inputs(paths):
    """A folder stands for the files in it (recursively, sorted); an http(s) URL is the mock service base (returned apart)."""
    files, mock_url = [], None
    for p in paths:
        if p.startswith(("http://", "https://")):
            mock_url = p.rstrip("/")
        elif pathlib.Path(p).is_dir():
            files += sorted(str(f) for f in pathlib.Path(p).rglob("*") if f.is_file() and not f.name.startswith("."))
        else:
            files.append(p)
    return files, mock_url


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
    """Two chat turns: the workflow must start and answer, and its first code step must run without an error
    (a broken generated snippet would break every call). A bare 500 means it cannot start a call."""
    cv = {"customer_id": "c_smoke", "first_name": "Alex", "last_name": "Smith", "title": "Mr", "now": time.strftime("%Y-%m-%dT%H:%M")}
    call, replies = None, []
    try:
        for msg in ("Hello?", "Yes, speaking."):
            body = {"workflowId": workflow_id, "message": msg, "customerVariables": cv}
            if call:
                body["callId"] = call
            r = freya.api("POST", "/api/v2/chat/message", body)
            call = r.get("callId") or call
            replies.append((r.get("response") or "")[:200])
        errors = []
        if call:
            for m in freya.get_call(call).get("messages", []):
                if m.get("role") != "system":
                    continue
                try:
                    e = json.loads(m.get("message") or "{}")
                except Exception:
                    continue
                if e.get("event") in ("function_call_error", "error"):
                    errors.append(str(e.get("error") or e.get("message"))[:300])
                rt_err = (e.get("variable_table") or {}).get("rt_error")
                if rt_err:
                    errors.append("rt_error: " + str(rt_err.get("value") if isinstance(rt_err, dict) else rt_err)[:300])
            try:
                freya.api("POST", "/api/v2/chat/end", {"callId": call})
            except Exception:
                pass
        return {"ok": bool(replies and replies[0]) and not errors, "replies": replies, "code_errors": errors[:3]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "replies": replies}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--slot")
    ap.add_argument("--spec", help="use this spec instead of extracting one (development)")
    ap.add_argument("--digest", action="store_true", help="first consolidate the documents into one rulebook (slower)")
    ap.add_argument("--no-smoke", action="store_true")
    ap.add_argument("--no-deploy", action="store_true", help="stop after compiling (offline check)")
    a = ap.parse_args()
    a.inputs, mock_url = expand_inputs(a.inputs)
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
    if a.no_deploy:
        trace.end(deployed=False)
        return
    v = freya.validate_workflow(slot, wf)
    trace.log("dry_run", result=v)
    state_file = ROOT / ".freya-agents.json"
    try:
        ids = freya.save_workflow(slot, wf, state_file=str(state_file))
    except RuntimeError as e:
        # The slot points to a workflow this workspace does not have (another workspace's state file, or it was deleted):
        # forget it and create a new workflow.
        trace.log("deploy_retry", reason=str(e)[:200])
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        state.pop(slot, None)
        state_file.write_text(json.dumps(state, indent=1))
        ids = freya.save_workflow(slot, wf, state_file=str(state_file))
    trace.log("deployed", workflow_id=ids["workflow_id"], agent_id=ids["agent_id"], version=ids.get("version"))
    if not a.no_smoke:
        s = smoke(ids["workflow_id"], spec)
        trace.log("self_test", kind="chat smoke: one turn on the published workflow", **s)
    freya.export_workflow(ids["workflow_id"], str(out / (slot + ".export.json")))
    trace.log("exported", path=str(out / (slot + ".export.json")))
    trace.end(workflow_id=ids["workflow_id"], agent_id=ids["agent_id"])
    # What a submission needs (hackathon page): submission/<brief>.workflow.json (the export) and traces/<run>.jsonl.
    # The spec the phone agent was compiled from goes next to the export, for reviewers.
    sub = ROOT / "submission"
    sub.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy(out / (slot + ".export.json"), sub / (slot + ".workflow.json"))
    shutil.copy(out / (slot + ".spec.json"), sub / (slot + ".spec.json"))
    print("submission files: submission/%s.workflow.json, submission/%s.spec.json, trace: %s" % (
        slot, slot, pathlib.Path(trace._path).relative_to(ROOT)))
    print(json.dumps(ids))


if __name__ == "__main__":
    main()
