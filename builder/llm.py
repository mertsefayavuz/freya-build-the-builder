"""The builder's LLM. Backends, chosen by env BUILDER_LLM:

  openrouter  OpenRouter (OpenAI-compatible). OPENROUTER_API_KEY. BUILDER_MODEL = comma-separated list, tried in order.
  openai      any OpenAI-compatible /chat/completions endpoint: OPENAI_BASE_URL, OPENAI_API_KEY, BUILDER_MODEL
              (works with LM Studio, e.g. OPENAI_BASE_URL=http://localhost:1234/v1).
  claude      Claude through claude-agent-sdk (Claude login or ANTHROPIC_API_KEY).

complete(system, user, purpose) -> str. Every call is written to the trace with model, purpose and duration.
"""
import asyncio, json, os, re, time, urllib.error, urllib.request

from . import trace

BACKEND = os.environ.get("BUILDER_LLM", "openrouter" if os.environ.get("OPENROUTER_API_KEY") else "claude")
DEFAULT_MODELS = {
    "openrouter": "nvidia/nemotron-3-ultra-550b-a55b:free,thinkingmachines/inkling:free,nvidia/nemotron-3-super-120b-a12b:free,openrouter/free",
    "openai": "local-model",
    "claude": "claude-opus-5-5",
}
MODELS = [m.strip() for m in os.environ.get("BUILDER_MODEL", DEFAULT_MODELS.get(BACKEND, "")).split(",") if m.strip()]
MODEL = MODELS[0] if MODELS else ""


def _claude(model, system, user):
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
    opts = ClaudeAgentOptions(model=model, system_prompt=system, allowed_tools=[], max_turns=1,
                              setting_sources=[], permission_mode="dontAsk")

    async def run():
        out = []
        async for m in query(prompt=user, options=opts):
            if isinstance(m, AssistantMessage):
                out += [b.text for b in m.content if isinstance(b, TextBlock)]
        return "".join(out)
    return asyncio.run(run())


def _chat_completions(base, key, model, system, user, extra_headers=None):
    body = {"model": model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    headers.update(extra_headers or {})
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s: %s" % (e.code, e.read().decode(errors="replace")[:300])) from None
    if out.get("error"):
        raise RuntimeError("API error: %s" % str(out["error"])[:300])
    text = (out.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    if not text.strip():
        raise RuntimeError("empty reply")
    return text


def _call(model, system, user):
    if BACKEND == "claude":
        return _claude(model, system, user)
    if BACKEND == "openrouter":
        return _chat_completions("https://openrouter.ai/api/v1", os.environ.get("OPENROUTER_API_KEY", ""), model, system, user,
                                 {"HTTP-Referer": "https://github.com/build-the-builder", "X-Title": "build-the-builder"})
    return _chat_completions(os.environ.get("OPENAI_BASE_URL", "http://localhost:1234/v1"), os.environ.get("OPENAI_API_KEY", "none"),
                             model, system, user)


def complete(system, user, purpose):
    """Try each model in MODELS; retry a model once after a short wait on rate limits / server errors."""
    errors = []
    for model in MODELS:
        for attempt in range(2):
            t0 = time.time()
            try:
                text = _call(model, system, user)
                trace.log("model_call", backend=BACKEND, model=model, purpose=purpose, seconds=round(time.time() - t0, 1),
                          chars_in=len(system) + len(user), chars_out=len(text))
                return text
            except Exception as e:
                err = repr(e)[:300]
                errors.append("%s: %s" % (model, err))
                trace.log("model_call", backend=BACKEND, model=model, purpose=purpose, seconds=round(time.time() - t0, 1),
                          chars_in=len(system) + len(user), chars_out=0, error=err)
                if attempt == 0 and any(x in err for x in ("429", "502", "503", "504", "timed out", "empty reply")):
                    time.sleep(10)
                    continue
                break
    raise RuntimeError("LLM call failed (%s): %s" % (purpose, " | ".join(errors)))


def extract_json(text):
    """The first JSON object in a model reply (handles ```json fences and <think> blocks)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)
