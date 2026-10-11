"""Talk to a workflow turn by turn as a scripted customer; print the transcript and the tools the agent called.

    python tools/chat_turn.py <workflow_id> --var customerNumber=95004361 --var now=2026-10-12T10:30:00+03:00 "Merhaba" "Evet" ...

Every --var is a call variable the workflow receives (the case says which ones). Each run is a fresh call.
After the last message the tool calls (name, arguments, result) of the call are listed from the call log.
"""
import argparse, json, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import freya


def run(workflow_id, variables, messages, show_tools=True):
    call, out = None, []
    for msg in messages:
        body = {"workflowId": workflow_id, "message": msg, "customerVariables": variables}
        if call:
            body["callId"] = call
        try:
            r = freya.api("POST", "/api/v2/chat/message", body)
        except RuntimeError as e:
            r = {"response": "<error %s>" % str(e)[:300]}
        call = r.get("callId") or call
        out.append((msg, r.get("response")))
        print("CUSTOMER:", msg); print("AGENT   :", r.get("response"), flush=True)
    if show_tools and call:
        time.sleep(1)
        try:
            for m in freya.get_call(call).get("messages", []):
                if m.get("role") != "system":
                    continue
                try:
                    e = json.loads(m.get("message") or "{}")
                except Exception:
                    continue
                ev = str(e.get("event", ""))
                if ev.startswith("function_call") or ev in ("error", "route_evaluated", "node_transition"):
                    print("  [%s] %s" % (ev, json.dumps({k: v for k, v in e.items() if k not in ("event", "variable_table")}, ensure_ascii=False)[:400]))
        except Exception as e:
            print("(call log not readable: %s)" % str(e)[:200])
        print("call_id:", call)
    return call, out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow_id")
    ap.add_argument("--var", action="append", default=[], help="name=value call variable")
    ap.add_argument("messages", nargs="+")
    a = ap.parse_args()
    run(a.workflow_id, dict(v.split("=", 1) for v in a.var), a.messages)
