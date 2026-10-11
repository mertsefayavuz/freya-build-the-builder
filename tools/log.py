import sys; sys.path.insert(0, ".")
import freya, json, re, sys
sim, it = sys.argv[1], sys.argv[2]
iter_ = freya.get_iteration(sim, it)
s = json.dumps(iter_, ensure_ascii=False, default=str)
ids = []
for m in re.finditer(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', s):
    if m.group(0) not in ids and m.group(0) not in (sim, it):
        ids.append(m.group(0))
print('CANDIDATE IDS', ids[:8])
for cid in ids[:8]:
    try:
        c = freya.get_call(cid)
    except Exception as e:
        continue
    cs = json.dumps(c, ensure_ascii=False, default=str)
    if 'endedReason' not in cs:
        continue
    print('CALL', cid)
    print('ENDED', c.get('endedReason') if isinstance(c, dict) else '')
    t = c.get('transcript') or c.get('messages') or []
    if isinstance(t, str):
        print(t[:2500])
    else:
        for msg in t[-25:]:
            print(str(msg)[:300])
    ev = c.get('events') or c.get('logs') or []
    for e in ev:
        es = json.dumps(e, ensure_ascii=False, default=str)
        if 'function' in es or 'error' in es.lower() or 'transition' in es:
            print('EV', es[:300])
    break
