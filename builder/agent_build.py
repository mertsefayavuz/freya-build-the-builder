"""Agentic builder for a case folder: Claude reads the bank's documents, builds the Freya phone agent, tests it, fixes it.

    python3 builder/agent_build.py <case_folder> <mock_url> [--slot final] [--model claude-opus-5-5] [--max-minutes 45]

The knowledge is in `.claude/skills/build-phone-agent-from-case/SKILL.md` (generic: nothing of any one case).
Needs FREYA_API_KEY (env.sh) and a Claude login or ANTHROPIC_API_KEY.
Everything the run does is printed with times; pipe it into the trace:  ... 2>&1 | tee traces/final.log
"""
import argparse, asyncio, os, pathlib, re, sys, time

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock,
                              UserMessage, query)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECRET = re.compile(r"(fak_|sk-or-|sk-ant-)[A-Za-z0-9_\-]{6,}")


def say(text):
    print(time.strftime("[%H:%M:%S] ") + SECRET.sub(r"\1***", str(text)), flush=True)


async def main(a):
    case = pathlib.Path(a.case_folder).resolve()
    mock = a.mock_url.rstrip("/")
    slot = a.slot
    started = time.time()
    say("RUN START %s | case folder: %s | mock: %s | slot: %s | model: %s" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), case, mock, slot, a.model))
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "submission").mkdir(exist_ok=True)
    options = ClaudeAgentOptions(
        cwd=ROOT, add_dirs=[str(case)], model=a.model, skills=["build-phone-agent-from-case"], setting_sources=["project"],
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"], permission_mode="dontAsk", max_turns=400,
        env={"BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "1200000", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
    )
    prompt = ("Use the build-phone-agent-from-case skill. Case folder (absolute path): %s\nMock service base URL: %s "
              "(the contract's paths go after it, for example %s/voice/v1/<service>).\nSlot name: %s (workflow file out/%s.workflow.json, "
              "export submission/%s.workflow.json).\nFinish within %d minutes of work." % (case, mock, mock, slot, slot, slot, a.max_minutes))
    say("MODEL CALL (agent session): model=%s, purpose: build the phone agent from the case folder, test it with scripted customers, fix and export" % a.model)
    async for m in query(prompt=prompt, options=options):
        if isinstance(m, AssistantMessage):
            for b in m.content:
                if isinstance(b, TextBlock):
                    say("AGENT: " + b.text)
                elif isinstance(b, ToolUseBlock):
                    say("TOOL %s: %s" % (b.name, str(b.input)[:300].replace("\n", " ")))
        elif isinstance(m, UserMessage) and isinstance(m.content, list):
            for b in m.content:
                if isinstance(b, ToolResultBlock):
                    c = b.content if isinstance(b.content, str) else str(b.content)
                    say("  RESULT: " + c[:400].replace("\n", " "))
        elif isinstance(m, ResultMessage):
            say("RESULT: %s turns, %.0f s" % (m.num_turns, m.duration_ms / 1000))
        if time.time() - started > (a.max_minutes + 10) * 60:
            say("TIME LIMIT reached, stopping the session")
            break
    say("RUN END %s (%.1f minutes)" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), (time.time() - started) / 60))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("case_folder")
    ap.add_argument("mock_url")
    ap.add_argument("--slot", default="final")
    ap.add_argument("--model", default=os.environ.get("BUILDER_MODEL", "claude-opus-5-5"))
    ap.add_argument("--max-minutes", type=int, default=45)
    asyncio.run(main(ap.parse_args()))
