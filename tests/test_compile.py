import copy, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import compile as C, tools_gen, spec_schema, codegen

SPEC = spec_schema.apply_defaults(json.loads((ROOT / "tests/fixtures/spec_a.json").read_text()))
API = tools_gen.load(ROOT / "tools/openapi.json")


def all_targets(wf):
    for name, n in wf["nodes"].items():
        for t in n.get("transitions", []):
            yield name, t["target_node"]
        if n.get("max_turn_transition"):
            yield name, n["max_turn_transition"]


def test_graph_is_closed_and_passthroughs_end_with_always():
    wf = C.compile_workflow(SPEC, API)
    for src, dst in all_targets(wf):
        assert dst in wf["nodes"], (src, dst)
    for name, n in wf["nodes"].items():
        if n.get("passthrough"):
            assert n["transitions"][-1]["condition"] == {"operator": "always"}, name
        if n["node_type"] == "conversation" and not n.get("passthrough"):
            assert n.get("max_turn_transition") in wf["nodes"], name
        assert "pre_actions" not in n or n["node_type"] != "conversation", name
    assert wf["initial_node"] == "contact"
    assert sum(1 for n in wf["nodes"].values() if n["node_type"] == "end_call") == 1


def test_every_path_reaches_close_before_end():
    wf = C.compile_workflow(SPEC, API)
    assert [t["target_node"] for t in wf["nodes"]["close"]["transitions"]] == ["end"]
    assert wf["nodes"]["farewell"]["transitions"][0]["target_node"] == "close"


def test_no_null_and_no_unsafe_code():
    wf = C.compile_workflow(SPEC, API)
    s = json.dumps(wf)
    assert "null" not in s

    def codes(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("pre_process_code", "post_process_code") and isinstance(v, str):
                    yield v
                if k == "code" and isinstance(v, dict) and isinstance(v.get("default"), str):
                    yield v["default"]
                yield from codes(v)
        elif isinstance(o, list):
            for x in o:
                yield from codes(x)
    n = 0
    for c in codes(wf):
        n += 1
        assert "{" + "{" not in c and "}" + "}" not in c
        compile(c, "<snippet>", "exec")
    assert n > 10


def test_tools_follow_a_changed_openapi():
    api = copy.deepcopy(API)
    p = api["paths"]["/create_promise"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    p["properties"]["promised_amount"] = p["properties"].pop("amount")
    p["required"] = ["customer_id", "product_id", "promised_amount", "date"]
    q = api["paths"]["/transfer_to_agent"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["queue"]
    q["enum"] = q["enum"] + ["vip"]
    wf = C.compile_workflow(SPEC, api)
    body = wf["nodes"]["record_promise"]["pre_actions"][0]["function"]["parameters"]["properties"]["body"]["properties"]
    assert "promised_amount" in body and "amount" not in body
    tt = [t for t in wf["nodes"]["negotiate"]["tools"] if t["function"]["name"] == "transfer_to_agent"][0]
    assert "vip" in tt["function"]["parameters"]["properties"]["body"]["properties"]["queue"]["enum"]


def test_validate_spec_catches_bad_values():
    bad = copy.deepcopy(SPEC)
    bad["bands"][0]["attempts"][0]["date"] = "sometime"
    bad["identity"]["questions"][0]["field"] = "dob"
    del bad["closing"]["map"]["unclear"]
    errs = spec_schema.validate_spec(bad, tools_gen.endpoints(API))
    assert any("attempt date" in e for e in errs)
    assert any("not in the verify tool enum" in e for e in errs)
    assert any("unclear" in e for e in errs)
    assert spec_schema.validate_spec(SPEC, tools_gen.endpoints(API)) == []


def test_generated_tool_snippet_runs_locally():
    code = codegen.tool_code(SPEC, "tool_check_callback(vars, params, SPEC)")
    g = {"vars": {"now": "2026-10-14T11:00"}, "params": {"date": "2026-10-15", "time": "11:00"}}
    exec(code, g)
    assert g["vars"]["cb_ok"] == "yes" and "rt_error" not in g["vars"]


def test_record_loop_is_guarded_against_failed_code_steps():
    wf = C.compile_workflow(SPEC, API)
    for node in ("after_record", "after_note", "exit_stuck", "exit_open"):
        first = wf["nodes"][node]["transitions"][0]
        assert first["target_node"] == "rescue"
        names = [c["variable"] for c in first["condition"]["conditions"]]
        assert node + "_failure_count" in names and "record_promise_success_count" in names
    assert wf["nodes"]["close_rescue_promise"]["pre_actions"][0]["function"]["parameters"]["properties"]["body"]["properties"]["outcome"]["default"] == "promise"


def test_generated_code_does_not_shadow_sandbox_globals():
    """The Freya sandbox wraps snippets with its own globals (seen in a spike); redefining them breaks every code step."""
    import re as _re
    reserved = {"_json", "_sys", "_result", "_tools", "_traceback", "_ToolSuspend", "params", "aes_gcm_decrypt", "aes_gcm_encrypt"}
    src = codegen.runtime_source()
    defined = set(_re.findall(r"^def (\w+)", src, _re.M)) | set(_re.findall(r"^(\w+)\s*=", src, _re.M)) | set(_re.findall(r"^import \w+ as (\w+)", src, _re.M))
    assert not (defined & reserved), defined & reserved
