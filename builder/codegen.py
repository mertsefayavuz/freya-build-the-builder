"""Turn runtime entry points into Python snippets the Freya sandbox runs.

A full snippet = the runtime source and the spec, both zlib+base64 packed, + one guarded call.
A light snippet = two tiny state helpers + one guarded call (for hooks that need no rules).
No snippet may contain a double brace: the platform may template it.
"""
import base64, json, pathlib, re, zlib

RUNTIME = pathlib.Path(__file__).resolve().parent / "runtime" / "core.py"
NL = "\n"

LIGHT_HELPERS = NL.join([
    "def load_state(vars):",
    "    try:",
    "        st = json.loads(vars.get('st') or '0')",
    "        return st if isinstance(st, dict) else dict()",
    "    except Exception:",
    "        return dict()",
    "def save_state(vars, st):",
    "    vars['st'] = json.dumps(st)",
    "",
])

RUNTIME_KEYS_SKIP = ("rules", "transfer_queues", "confirm_phrases", "tool_map", "extra_rules")

HEAD = NL.join(["if not isinstance(globals().get('vars'), dict):", "    vars = dict()", ""])


def runtime_source():
    out = []
    for line in RUNTIME.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(line.rstrip())
    src = NL.join(out)
    src = re.sub(r'\n(\s*)"""[^"]*"""', "", src)        # docstrings
    return src + NL


def _pack(text):
    return base64.b64encode(zlib.compress(text.encode("utf-8"), 9)).decode()


def _spec_line(spec):
    slim = {k: v for k, v in spec.items() if k not in RUNTIME_KEYS_SKIP}
    return "SPEC = json.loads(_z.decompress(_b.b64decode('%s')).decode())" % _pack(json.dumps(slim, separators=(",", ":"))) + NL


def _guard(call, err_var):
    body = ("    " + call.replace(NL, NL + "    "))
    return NL.join(["try:", body, "except Exception as _e:", "    vars['%s'] = repr(_e)[:300]" % err_var, ""])


def snippet(spec, call, err_var="rt_error", light=False):
    if light:
        code = HEAD + "import json" + NL + LIGHT_HELPERS + _guard(call, err_var)
    else:
        load = "import zlib as _z, base64 as _b" + NL + "exec(_z.decompress(_b.b64decode('%s')).decode(), globals())" % _pack(runtime_source()) + NL
        code = HEAD + load + _spec_line(spec) + _guard(call, err_var)
    assert "{" + "{" not in code and "}" + "}" not in code, "double brace in generated code"
    return code


def node_code(spec, call):
    """For a code node: `vars` is the variable table."""
    return snippet(spec, call)


def tool_code(spec, call):
    """For a code_exec tool: the model's arguments are in the global `params`."""
    return snippet(spec, "params = globals().get('params') or dict()" + NL + call)


def pre_hook(spec, call, light=False):
    """api_call pre_process_code: `vars` and `config` are available."""
    return snippet(spec, call, "hook_error", light)


def post_hook(spec, call, light=False):
    """api_call post_process_code: `result` (and `vars`) are available."""
    return snippet(spec, call, "hook_error", light)
