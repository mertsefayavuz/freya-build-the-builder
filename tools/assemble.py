"""Assemble a workflow from parts, so no single file has to be huge.

    python tools/assemble.py <dir> <out.json>

<dir>/meta.json   {"initial_node": "...", "system_prompt": "", "test_variables": {...}, "tools": [...], "global_nodes": {...}}
<dir>/nodes/*.json   one node object per file, the file name (without .json) is the node key unless the node has a "name"
"""
import json, pathlib, sys

d = pathlib.Path(sys.argv[1])
meta = json.loads((d / "meta.json").read_text(encoding="utf-8"), strict=False)
nodes = {}
for f in sorted((d / "nodes").glob("*.json")):
    try:
        n = json.loads(f.read_text(encoding="utf-8"), strict=False)
    except Exception as e:
        sys.exit("node file %s is not valid JSON: %s" % (f.name, e))
    nodes[n.get("name") or f.stem] = n
wf = {"nodes": nodes, "tools": meta.get("tools", []), "global_nodes": meta.get("global_nodes", {}), "initial_node": meta["initial_node"],
      "system_prompt": meta.get("system_prompt", ""), "test_variables": meta.get("test_variables", {})}
pathlib.Path(sys.argv[2]).write_text(json.dumps(wf, ensure_ascii=False, indent=1), encoding="utf-8")
print("assembled %d nodes -> %s" % (len(nodes), sys.argv[2]))
