"""Freya tool functions generated from the bank's openapi file.

Field names and enum values come from the file, so a renamed field or a new allowed value needs no code change.
Roles (which body field is the product id, the amount, ...) are matched by name; spec['tool_map'] can override.
"""
import json, re

ROLE_HINTS = {
    "customer_id": [r"^customer_id$", r"customer"],
    "product_id": [r"^product_id$", r"product", r"account_id", r"policy"],
    "amount": [r"^amount$", r"amount", r"sum"],
    "date": [r"^date$", r"date", r"day"],
    "time": [r"^time$", r"time", r"hour"],
    "field": [r"^field$", r"question", r"field"],
    "answer": [r"^answer$", r"answer", r"value"],
    "reason": [r"^reason$", r"reason", r"code"],
    "text": [r"^text$", r"text", r"comment", r"note"],
    "queue": [r"^queue$", r"queue", r"team", r"department"],
    "outcome": [r"^outcome$", r"outcome", r"result", r"status"],
}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def base_url(api):
    servers = api.get("servers") or [{"url": ""}]
    return servers[0]["url"].rstrip("/")


def endpoints(api):
    """name -> {url, method, props, required}"""
    out = {}
    for path, ops in api.get("paths", {}).items():
        for method, op in ops.items():
            name = path.strip("/").split("/")[-1]
            schema = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
            out[name] = {"url": base_url(api) + path, "method": method.upper(), "props": schema.get("properties", {}),
                         "required": schema.get("required", []), "summary": op.get("summary", "")}
    return out


def role_field(ep, role, spec=None, tool=None):
    """Body field name that plays `role` in endpoint `ep`."""
    override = ((spec or {}).get("tool_map", {}).get(tool or "", {}) or {}).get(role)
    if override and override in ep["props"]:
        return override
    for pat in ROLE_HINTS.get(role, [re.escape(role)]):
        for name in ep["props"]:
            if re.search(pat, name, re.I):
                return name
    return None


def enum_of(ep, field):
    p = ep["props"].get(field) or {}
    return p.get("enum") or (p.get("items") or {}).get("enum") or []


def _leaf(prop, default, description=None):
    t = prop.get("type", "string")
    leaf = {"type": "number" if t in ("number", "integer") else ("boolean" if t == "boolean" else "string"), "default": default}
    desc = description or prop.get("description")
    if prop.get("enum"):
        leaf["enum"] = prop["enum"]
        desc = (desc + " " if desc else "") + "One of: " + ", ".join(map(str, prop["enum"])) + "."
    if desc:
        leaf["description"] = desc
    return leaf


def api_tool(ep, name, baked, model_fields=(), descriptions=None, description="", max_retries=0,
             timeout_ms=12000, pre=None, post=None, response_mapping=None):
    """baked: field -> '{{var}}' template. model_fields: fields the model fills (default '{{<tool>__<field>}}', unset)."""
    props, required = {}, []
    for field, prop in ep["props"].items():
        if field in baked:
            props[field] = _leaf(prop, baked[field])
        elif field in model_fields:
            props[field] = _leaf(prop, "{{%s__%s}}" % (name, field), (descriptions or {}).get(field))
            if field in ep["required"]:
                required.append(field)
    tool = {
        "type": "function",
        "config": {"timeoutMs": timeout_ms, "maxRetries": max_retries},
        "function": {
            "name": name, "handler": "__function__:api_call", "description": description or ep["summary"],
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "default": ep["url"]},
                "method": {"type": "string", "default": ep["method"]},
                "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                "body": {"type": "object", "properties": props, "required": required}}}},
    }
    if pre:
        tool["pre_process_code"] = pre
    if post:
        tool["post_process_code"] = post
    if response_mapping:
        tool["response_mapping"] = response_mapping
    return tool


def code_tool(name, description, params, code, required=None):
    """A code_exec tool the model calls. params: name -> json-schema leaf (no default: the model fills it)."""
    props = dict(params)
    props["code"] = {"type": "string", "default": code}
    return {"type": "function", "config": {"timeoutMs": 10000},
            "function": {"name": name, "handler": "__function__:code_exec", "description": description,
                         "parameters": {"type": "object", "properties": props, "required": required or list(params)}}}
