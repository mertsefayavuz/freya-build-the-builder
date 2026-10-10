import sys, json
sys.path.insert(0, r"C:\Users\merts\Desktop\MAIN\job\freya\kit\build-the-builder")
import freya
cid = sys.argv[1] if len(sys.argv) > 1 else json.load(open("spike1_last.json"))["call_id"]
c = freya.get_call(cid)
print("ended:", c.get("endedReason"))
last_vt = None
for m in c["messages"]:
    role = m.get("role"); msg = m.get("message")
    if role == "system":
        try: e = json.loads(msg)
        except Exception: print("SYS", str(msg)[:300]); continue
        ev = e.get("event")
        if ev == "route_evaluated":
            last_vt = e.get("variable_table")
            print(f"  [route] {e.get('from_node')} -> {e.get('target')} matched={e.get('matched')} reason={str(e.get('reason'))[:120]}")
        elif ev in ("function_call_start", "function_call_complete"):
            print(f"  [{ev}] {json.dumps({k: v for k, v in e.items() if k != 'event'})[:700]}")
        else:
            print(f"  [{ev}] {json.dumps(e)[:400]}")
    else:
        print(f"{role.upper()}: {msg}")
        snap = m.get("workflowSnapshot")
        if snap: last_vt = snap.get("variableTable") or last_vt
if last_vt:
    print("\nVARIABLES:")
    for k, v in sorted(last_vt.items()):
        if k.startswith("system") : continue
        vv = v.get("value") if isinstance(v, dict) else v
        print(" ", k, "=", str(vv)[:150])
