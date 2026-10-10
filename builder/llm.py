"""The builder's LLM. Two backends, chosen by env:

  BUILDER_LLM=claude   (default) Claude through claude-agent-sdk (Claude login or ANTHROPIC_API_KEY). BUILDER_MODEL picks the model.
  BUILDER_LLM=openai   any OpenAI-compatible /chat/completions endpoint: OPENAI_BASE_URL, OPENAI_API_KEY, BUILDER_MODEL
                       (works with LM Studio, e.g. OPENAI_BASE_URL=http://localhost:1234/v1).

complete(system, user, purpose) -> str. Every call is written to the trace with model, purpose and duration.
"""
import asyncio, json, os, re, time, urllib.request

from . import trace

BACKEND = os.environ.get("BUILDER_LLM", "claude")
MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5" if BACKEND == "claude" else "local-model")


def _claude(system, user):
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
    opts = ClaudeAgentOptions(model=MODEL, system_prompt=system, allowed_tools=[], max_turns=1,
                              setting_sources=[], permission_mode="dontAsk")

    async def run():
        out = []
        async for m in query(prompt=user, options=opts):
            if isinstance(m, AssistantMessage):
                out += [b.text for b in m.content if isinstance(b, TextBlock)]
        return "".join(out)
    return asyncio.run(run())


def _openai(system, user):
    base = os.environ.get("OPENAI_BASE_URL", "http://localhost:1234/v1").rstrip("/")
    body = {"model": MODEL, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "none")})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def complete(system, user, purpose):
    t0 = time.time()
    err = None
    try:
        text = _claude(system, user) if BACKEND == "claude" else _openai(system, user)
    except Exception as e:
        err, text = repr(e)[:500], ""
    trace.log("model_call", backend=BACKEND, model=MODEL, purpose=purpose, seconds=round(time.time() - t0, 1),
              chars_in=len(system) + len(user), chars_out=len(text), error=err)
    if err:
        raise RuntimeError("LLM call failed (%s): %s" % (purpose, err))
    return text


def extract_json(text):
    """The first JSON object in a model reply (handles ```json fences)."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)
