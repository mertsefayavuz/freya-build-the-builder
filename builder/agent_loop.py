"""Agentic builder on any chat-completions model (OpenRouter, LM Studio): the model reads the case folder, builds the Freya phone
agent, tests it with scripted customers, fixes it, exports it. The knowledge is the skill file (generic, nothing of one case).

    python builder/agent_loop.py <case_folder> <mock_url> [--slot final] [--max-minutes 60] [--max-requests 45]

Models: BUILDER_MODEL (comma list, tried in order) on OpenRouter (OPENROUTER_API_KEY); when all fail or the daily quota is used up,
the local fallback OPENAI_BASE_URL / LOCAL_MODEL (LM Studio) is used. Everything is printed with times: pipe into the trace.
"""
import argparse, json, os, pathlib, re, subprocess, sys, time, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "build-phone-agent-from-case" / "SKILL.md"
SECRET = re.compile(r"(fak_|sk-or-v1-|sk-ant-)[A-Za-z0-9_\-]{6,}")
OR_MODELS = [m.strip() for m in os.environ.get("BUILDER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free,nvidia/nemotron-3-super-120b-a12b:free,thinkingmachines/inkling:free,openrouter/free").split(",") if m.strip()]
LOCAL_BASE = os.environ.get("OPENAI_BASE_URL", "http://localhost:1234/v1")
LOCAL_MODEL = os.environ.get("LOCAL_MODEL", "openai/gpt-oss-20b")
STATE = {"requests": 0, "dead": set()}
BASH = next((p for p in ("C:/Program Files/Git/bin/bash.exe", "C:/Program Files/Git/usr/bin/bash.exe") if os.path.exists(p)), "bash")

PROTOCOL = """
You work through actions. Every reply of yours is ONE JSON object and nothing else (no markdown fences):
{"thought": "short plan for this step", "actions": [ACTION, ...]}
You may give several actions in one reply; they run in order and you get all results back. Requests are scarce: batch reads, batch writes.
ACTION is one of:
  {"read": "<absolute or relative path>", "offset": 0, "limit": 40000}      read a text file (characters offset..offset+limit)
  {"write": "<path>", "content": "<full file content>"}                     write a file (creates folders)
  {"bash": "<shell command>"}                                                run a command in the builder folder (bash); output is returned
  {"list": "<folder>"}                                                       list files of a folder, recursively
  {"finalize": "<slot>"}   assemble out/<slot>/ (meta.json + nodes/*.json), validate, publish on Freya and export. Returns workflow_id or the exact errors.
Your shell already starts in the builder folder: never cd, use relative paths (python tools/chat_turn.py ...). Do not search the disk.
  {"finish": "<final one-line report: workflow_id, agent_id, what passed>"}  only when the agent is published AND exported
Use python (not python3) to run scripts. Environment variables for Freya are already set. Keep each reply under 14000 tokens: write large JSON files with a small python script or in several files, not one huge string.
"""


def say(text):
    print(time.strftime("[%H:%M:%S] ") + SECRET.sub(r"\1***", str(text)), flush=True)


def http_chat(base, key, model, messages, extra=None, timeout=int(os.environ.get("MODEL_TIMEOUT", "900"))):
    body = {"model": model, "temperature": 0.2, "messages": messages}
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    headers.update(extra or {})
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s: %s" % (e.code, e.read().decode(errors="replace")[:250])) from None
    if out.get("error"):
        raise RuntimeError("API error: %s" % str(out["error"])[:250])
    text = (out.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    if not text.strip():
        raise RuntimeError("empty reply")
    return text


def chat(messages, purpose):
    errors = []
    plan = [("openrouter", m) for m in OR_MODELS if os.environ.get("OPENROUTER_API_KEY")] + [("local", LOCAL_MODEL)]
    for kind, model in plan:
        if (kind, model) in STATE["dead"]:
            continue
        for attempt in range(2):
            t0 = time.time()
            try:
                if kind == "openrouter":
                    text = http_chat("https://openrouter.ai/api/v1", os.environ["OPENROUTER_API_KEY"], model, messages,
                                     {"HTTP-Referer": "https://github.com/build-the-builder", "X-Title": "build-the-builder"})
                else:
                    text = http_chat(LOCAL_BASE, os.environ.get("OPENAI_API_KEY", "none"), model, messages)
                STATE["requests"] += 1
                say("MODEL CALL #%d: %s (%s), purpose: %s, %.0fs, in %d chars, out %d chars" % (STATE["requests"], model, kind, purpose, time.time() - t0, sum(len(m["content"]) for m in messages), len(text)))
                return text
            except Exception as e:
                err = str(e)[:250]
                errors.append("%s: %s" % (model, err))
                say("MODEL CALL FAILED: %s (%s): %s" % (model, kind, err))
                if "429" in err and ("day" in err.lower() or "quota" in err.lower() or "limit" in err.lower()):
                    STATE["dead"].add((kind, model))
                    break
                if attempt == 0 and any(x in err for x in ("429", "502", "503", "504", "timed out", "empty reply")):
                    time.sleep(8)
                    continue
                if "HTTP 4" in err and "429" not in err:
                    STATE["dead"].add((kind, model))
                break
    raise RuntimeError("all models failed: " + " | ".join(errors)[:600])


def parse_reply(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


def run_action(a):
    try:
        if "read" in a:
            p = pathlib.Path(a["read"])
            p = p if p.is_absolute() else ROOT / p
            t = p.read_text(encoding="utf-8", errors="replace")
            off, lim = int(a.get("offset", 0)), int(a.get("limit", 40000))
            chunk = t[off: off + lim]
            return "READ %s [%d..%d of %d]\n%s" % (a["read"], off, off + len(chunk), len(t), chunk)
        if "write" in a:
            p = pathlib.Path(a["write"])
            p = p if p.is_absolute() else ROOT / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(a.get("content", ""), encoding="utf-8")
            return "WROTE %s (%d chars)" % (a["write"], len(a.get("content", "")))
        if "list" in a:
            p = pathlib.Path(a["list"])
            p = p if p.is_absolute() else ROOT / p
            return "\n".join("%s (%d bytes)" % (f, f.stat().st_size) for f in sorted(p.rglob("*")) if f.is_file())[:6000]
        if "finalize" in a:
            return finalize(a["finalize"] if isinstance(a["finalize"], str) else a.get("slot", SLOT[0]))
        if "bash" in a:
            r = subprocess.run([BASH, "-c", a["bash"]], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            return "EXIT %d\n%s%s" % (r.returncode, r.stdout[-6000:], ("\nSTDERR:\n" + r.stderr[-2500:]) if r.stderr.strip() else "")
        return "unknown action: %s" % str(a)[:100]
    except Exception as e:
        return "ACTION ERROR: %s" % str(e)[:500]


SLOT = ["final"]


def finalize(slot):
    """Deterministic step, no model: assemble out/<slot>/ parts, validate and publish on Freya, export. Returns a report for the model."""
    sys.path.insert(0, str(ROOT))
    import freya
    parts = ROOT / "out" / slot
    wf_path = ROOT / "out" / (slot + ".workflow.json")
    if parts.exists():
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "assemble.py"), str(parts), str(wf_path)], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode:
            return "ASSEMBLE FAILED:\n" + (r.stderr or r.stdout)[-1500:]
    if not wf_path.exists():
        return "no workflow file out/%s.workflow.json and no parts in out/%s/" % (slot, slot)
    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    try:
        v = freya.validate_workflow(slot, wf)
    except Exception as e:
        return "VALIDATION ERROR (fix the node files named in the message, then finalize again):\n" + str(e)[:3500]
    try:
        ids = freya.save_workflow(slot, wf, state_file=str(ROOT / ".freya-agents.json"))
        freya.export_workflow(ids["workflow_id"], str(ROOT / "submission" / (slot + ".workflow.json")))
    except Exception as e:
        return "PUBLISH ERROR:\n" + str(e)[:3500]
    say("PUBLISHED workflow_id=%s agent_id=%s nodes=%d tools=%d" % (ids["workflow_id"], ids["agent_id"], len(wf.get("nodes", {})), len(wf.get("tools", []))))
    return "PUBLISHED and exported to submission/%s.workflow.json: workflow_id=%s agent_id=%s, %d nodes, %d tools. Validation: %s" % (
        slot, ids["workflow_id"], ids["agent_id"], len(wf.get("nodes", {})), len(wf.get("tools", [])), str(v)[:300])


def compact(messages, limit=int(os.environ.get("COMPACT_LIMIT", "70000"))):
    """Keep the context under `limit` chars: shorten the oldest tool results first."""
    total = sum(len(m["content"]) for m in messages)
    i = 2
    while total > limit and i < len(messages) - 2:
        m = messages[i]
        if m["role"] == "user" and len(m["content"]) > 700:
            total -= len(m["content"]) - 700
            m["content"] = m["content"][:700] + "\n...[shortened to save context; read the file again if needed]"
        i += 1


PIN_PATTERNS = ("*contract*", "*scenarios*", "test-customers.md", "BRIEF.md")


def pinned_documents(case, slot):
    """The documents the model must never lose: they stay in the system prompt of every request (never shortened).
    The case's service contract, example scenarios, test-data overview and brief; the rulebook of this slot; the platform format docs."""
    files = []
    for pat in PIN_PATTERNS:
        files += sorted(f for f in case.rglob(pat) if f.is_file() and f.suffix in (".md", ".txt"))
    rb = ROOT / "out" / (slot + ".rulebook.md")
    if rb.exists():
        files.append(rb)
    files += [ROOT / "docs" / "workflow-format.md", ROOT / "docs" / "tools-and-code.md", ROOT / "examples" / "base-agent.json"]
    seen, out = set(), ["\n\n# PINNED DOCUMENTS (already read for you; do not read them again; they are always in your context)\n"]
    for f in files:
        if f in seen or not f.exists():
            continue
        seen.add(f)
        out.append("\n## FILE %s\n%s\n" % (f, f.read_text(encoding="utf-8", errors="replace")))
    say("pinned documents: %s" % ", ".join(f.name for f in seen))
    return "".join(out)


def main(a):
    case = pathlib.Path(a.case_folder).resolve()
    mock = a.mock_url.rstrip("/")
    started = time.time()
    say("RUN START %s | case folder: %s | mock: %s | slot: %s | models: %s then local %s" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), case, mock, a.slot, ",".join(OR_MODELS), LOCAL_MODEL))
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "submission").mkdir(exist_ok=True)
    skill = re.sub(r"^---.*?---\s*", "", SKILL.read_text(encoding="utf-8"), flags=re.S)
    system = skill + "\n" + PROTOCOL + pinned_documents(case, a.slot)
    first = ("Case folder (absolute path): %s\nMock service base URL: %s (the contract's paths go after it, for example %s/voice/v1/<service>).\n"
             "Slot name: %s (workflow file out/%s.workflow.json, export submission/%s.workflow.json). Builder folder (your working directory): %s\n"
             "Budget: at most %d model requests and %d minutes in total; publish an early working version as soon as you have one.\n"
             "Start by listing the case folder and reading the documents (batch the reads)." % (case, mock, mock, a.slot, a.slot, a.slot, ROOT, a.max_requests, a.max_minutes))
    rb = ROOT / "out" / (a.slot + ".rulebook.md")
    if a.reuse_rulebook and rb.exists():
        first += ("\nA rulebook from an earlier run of this builder is in %s (read it first). Skip re-reading all the sources; read only the "
                  "service contract and the files you need to verify a rule, then go straight to building the workflow." % rb)
        say("reusing the rulebook of an earlier run: %s" % rb)
    SLOT[0] = a.slot
    if a.note:
        first += "\nNote for this run: " + a.note
    messages = [{"role": "system", "content": system}, {"role": "user", "content": first}]
    if a.resume and (ROOT / "out" / a.slot).exists():
        say("RESUME: node files of an earlier run found in out/%s/ - finalizing them first" % a.slot)
        rep = finalize(a.slot)
        say("  RESULT: " + rep[:600].replace("\n", " "))
        messages.append({"role": "user", "content": "The workflow parts of an earlier run (out/%s/) already exist. Result of the finalize step:\n%s\n"
                         "If it published, test the agent now with tools/chat_turn.py using the example scenarios and fix problems in the node files "
                         "(then {\"finalize\": \"%s\"} again). If it reported errors, fix exactly those node files and finalize again. Do not rewrite everything." % (a.slot, rep, a.slot)})
    for step in range(1, 200):
        if time.time() - started > a.max_minutes * 60 or STATE["requests"] >= a.max_requests:
            say("BUDGET reached (requests %d, %.0f min): stopping" % (STATE["requests"], (time.time() - started) / 60))
            break
        compact(messages)
        try:
            text = chat(messages, "builder step %d" % step)
        except RuntimeError as e:
            say("STOP: " + str(e))
            break
        messages.append({"role": "assistant", "content": text})
        try:
            reply = parse_reply(text)
        except Exception as e:
            say("reply was not valid JSON (%s)" % str(e)[:100])
            messages.append({"role": "user", "content": "Your reply was not a single valid JSON object. Reply again with only the JSON object."})
            continue
        say("THOUGHT: " + str(reply.get("thought", ""))[:300])
        results, done = [], None
        for act in reply.get("actions", []):
            say("ACTION: " + json.dumps({k: (v if k != "content" else "<%d chars>" % len(v)) for k, v in act.items()}, ensure_ascii=False)[:300])
            if "finish" in act:
                if time.time() - started < a.min_minutes * 60:
                    left = a.min_minutes - (time.time() - started) / 60
                    say("FINISH refused: %.1f minutes of the minimum work time are left" % left)
                    results.append("FINISH REFUSED: you still have %.1f minutes. Your task list is not done (see the note and the skill: conversation hub, week change flow, criteria file, scenario checks read from the call log). Continue with the next open item." % left)
                    break
                done = act["finish"]
                break
            res = run_action(act)
            say("  RESULT: " + res[:300].replace("\n", " "))
            results.append(res)
        if done:
            say("FINISH: " + str(done))
            break
        messages.append({"role": "user", "content": "\n\n".join("--- result %d ---\n%s" % (i + 1, r) for i, r in enumerate(results)) or "No actions were given."})
    say("RUN END %s (%.1f minutes, %d model requests)" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), (time.time() - started) / 60, STATE["requests"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("case_folder")
    ap.add_argument("mock_url")
    ap.add_argument("--slot", default="final")
    ap.add_argument("--max-minutes", type=int, default=60)
    ap.add_argument("--max-requests", type=int, default=45)
    ap.add_argument("--min-minutes", type=int, default=0, help="a finish before this many minutes is refused and the model is told to continue")
    ap.add_argument("--note", default="", help="extra instruction for this run (for example what the previous run left unresolved)")
    ap.add_argument("--resume", action="store_true", help="out/<slot>/ node files of an earlier run exist: finalize them first, then test and fix")
    ap.add_argument("--reuse-rulebook", action="store_true", help="out/<slot>.rulebook.md from an earlier run exists: use it instead of re-reading every source")
    main(ap.parse_args())
