"""Write mock/worker.js from the bank's test-customers.json.   python3 mock/build_worker.py <test-customers.json>"""
import json, pathlib, sys
here = pathlib.Path(__file__).parent
data = json.load(open(sys.argv[1], encoding="utf-8"))
js = (here / "worker.template.js").read_text(encoding="utf-8").replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
(here / "worker.js").write_text(js, encoding="utf-8")
print("worker.js", len(js), "bytes")
