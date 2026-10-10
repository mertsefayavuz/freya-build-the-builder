"""Run trace: one JSON object per line in traces/<start>-<slot>.jsonl. API keys are never written."""
import json, os, pathlib, re, time

_path = None
_t0 = None
SECRET = re.compile(r"(fak_[A-Za-z0-9_\-]+|sk-[A-Za-z0-9_\-]{10,})")


def start(slot, root):
    global _path, _t0
    _t0 = time.time()
    d = pathlib.Path(root) / "traces"
    d.mkdir(exist_ok=True)
    _path = d / ("%s-%s.jsonl" % (time.strftime("%Y%m%d-%H%M%S"), slot))
    log("start", slot=slot, at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    return _path


def log(event, **data):
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "elapsed_s": round(time.time() - _t0, 1) if _t0 else None, "event": event}
    rec.update(data)
    line = SECRET.sub("<redacted>", json.dumps(rec, default=str))
    print("[trace] " + line[:300], flush=True)
    if _path:
        with open(_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def end(**data):
    log("end", at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), total_s=round(time.time() - _t0, 1) if _t0 else None, **data)
