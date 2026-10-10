"""Talk to a workflow turn by turn as a practice customer, then print the bank's ledger.

    python tools/chat_case.py <workflow_id> <case_id> "first message" "second message" ...

Uses a fresh practice session of the case (its customer_id and call variables), so the ledger is real.
"""
import json, pathlib, sys, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import freya
from score import http


def run(workflow_id, case_id, messages, verbose=True):
    variables = http("/v1/practice/sessions", {"case": case_id})["variables"]
    call, replies = None, []
    for msg in messages:
        body = {"workflowId": workflow_id, "message": msg, "customerVariables": variables}
        if call:
            body["callId"] = call
        for attempt in range(2):
            try:
                r = freya.api("POST", "/api/v2/chat/message", body)
                break
            except RuntimeError as e:
                if "504" in str(e):          # the turn is still running on the platform; never resend it
                    time.sleep(8)
                r = {"response": "<error %s>" % str(e)[:200]}
                break
        call = r.get("callId") or call
        replies.append(r.get("response"))
        if verbose:
            print("USER :", msg); print("AGENT:", r.get("response"), flush=True)
    ledger = http("/v1/practice/ledger/" + variables["customer_id"])
    if verbose:
        print("\nLEDGER tools:", " > ".join(c["tool"] for c in ledger.get("calls", [])))
        print("outcome:", ledger.get("outcome"), ledger.get("reason"), "promises:", ledger.get("promises"), "notes:", ledger.get("notes"),
              "callbacks:", ledger.get("callbacks"), "transfers:", ledger.get("transfers"))
        print("call_id:", call)
    return call, replies, ledger


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], sys.argv[3:])
