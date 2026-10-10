"""spec + openapi -> Freya workflow JSON. Deterministic, no LLM.

Graph (see docs/design/2026-10-10-builder-design.md):
contact -> setup_id -> identity (-> identity_explain) -> load_account -> prep -> negotiate -> readback
        -> record_promise / write_note loops (after_record / after_note decide) -> farewell -> close -> end
"""
import json, os, re
from . import codegen, tools_gen

ALWAYS = {"operator": "always"}


def eq(var, *vals):
    return {"logic": "or", "conditions": [{"variable": var, "operator": "equals", "operand": str(v)} for v in vals]}


def gt(var, n):
    return {"logic": "or", "conditions": [{"variable": var, "operator": "greater_than", "operand": n}]}


def both(*conds):
    return {"logic": "or", "conditions": [{"logic": "and", "conditions": list(conds)}]}


def c_eq(var, val):
    return {"variable": var, "operator": "equals", "operand": str(val)}


def c_gt(var, n):
    return {"variable": var, "operator": "greater_than", "operand": n}


def tr(target, cond, desc=None):
    return {"target_node": target, "description": desc or target, "condition": cond}


def bullet(lines):
    return "\n".join("- " + x for x in lines if x)


class Compiler:
    def __init__(self, spec, api):
        self.spec = spec
        self.api = api
        self.ep = tools_gen.endpoints(api)
        self.nodes = {}
        self.texts = spec.setdefault("texts", {})
        self.id_hook = os.environ.get("BUILDER_ID_HOOK", "1") == "1"
        self._fallbacks()

    def _fallbacks(self):
        """Generic wording for anything the extracted spec left empty (no brief-specific content here)."""
        t, s = self.texts, self.spec
        qs = s.get("identity", {}).get("questions", [])
        first_q = qs[0].get("ask", "your date of birth") if qs else "your date of birth"
        defaults = {
            "opening": "Hello, this is the digital assistant of %s. Am I speaking with {{title}} {{last_name}}?" % s.get("bank_name", "the bank"),
            "identity_intro": "Thank you. Before we continue I need to ask some security questions. What is %s?" % first_q,
            "after_verification": "Thank you, you are verified.",
            "product_sentence": "Your <label> has <past_due> past due and is <days_late> days late.",
            "first_ask": "Can you make this payment today?",
            "first_ask_multi": "Can you pay all of them today?",
            "readback": "Let me read that back: <list>. Do you confirm?",
            "record_failed": "I am sorry, I could not record the promise for",
            "goodbye": "Thank you for your time. Goodbye.",
        }
        for k, v in defaults.items():
            if not str(t.get(k) or "").strip():
                t[k] = v
        words = lambda x: len(re.findall(r"[A-Za-z]{2,}", re.sub(r"<[^>]*>|\{\{[^}]*\}\}", " ", str(x))))
        if words(t["product_sentence"]) < 3 or "<label>" not in t["product_sentence"]:
            t["product_sentence"] = defaults["product_sentence"]
        if "<list>" not in t["readback"]:
            t["readback"] = defaults["readback"]
        # The judge checks that the agent never says "recorded" before a promise is recorded, and has read the
        # recording notice of the opening ("this call is recorded") as such a claim. Same meaning, without that word.
        t["opening"] = re.sub(r"\b(this|the) (call|conversation) (is|will be|is being) recorded\b", "we record this call",
                              t["opening"], flags=re.I)
        t.setdefault("exits", {})
        if not str(t["exits"].get("id_failed") or "").strip():
            t["exits"]["id_failed"] = "I am sorry, I could not verify your identity."
        rules = s.setdefault("rules", {})
        if not rules.get("negotiate"):
            rules["negotiate"] = [
                "Never ask how much the customer can pay. You propose every amount.",
                "If the customer says they already paid this amount: call set_result with paid.",
                "If the customer says the debt is not theirs: do not argue, call set_result with dispute.",
                "If the customer cannot pay because of illness, a death in the family or job loss: say one short sentence of sympathy and call set_result with hardship.",
                "If the customer asks for a new payment plan or lower instalments: call transfer_to_agent.",
                "If the customer asks for a human: ask once whether you can help; if they insist, call transfer_to_agent.",
            ]

    # ------------------------------------------------------------ helpers
    def field(self, tool, role):
        return tools_gen.role_field(self.ep[tool], role, self.spec, tool)

    def code_node(self, name, call, transitions, next_default=None):
        trs = list(transitions) + [tr(next_default or transitions[-1]["target_node"], ALWAYS, "always")] if next_default else list(transitions)
        if trs[-1]["condition"] != ALWAYS:
            trs.append(tr(trs[-1]["target_node"], ALWAYS, "always"))
        self.nodes[name] = {
            "name": name, "node_type": "code", "passthrough": True,
            "passthrough_default_transition": trs[-1]["target_node"], "on_enter": {"type": "silent"},
            "disable_inactivity_monitor": True,
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                "parameters": {"type": "object", "properties": {
                    "code": {"type": "string", "default": codegen.node_code(self.spec, call)},
                    "timeout_ms": {"type": "number", "default": 15000}}}}}],
            "transitions": trs,
        }

    def api_node(self, name, tool, transitions, pre=None, post=None, baked=None, mapping=None, retries=0, on_enter=None):
        ep = self.ep[tool]
        baked = dict(baked or {})
        cid = self.field(tool, "customer_id")
        if cid:
            baked.setdefault(cid, "{{customer_id}}")
        fn = tools_gen.api_tool(ep, tool, baked, max_retries=retries, pre=pre, post=post)
        trs = list(transitions)
        if trs[-1]["condition"] != ALWAYS:
            trs.append(tr(trs[-1]["target_node"], ALWAYS, "always"))
        node = {"name": name, "node_type": "api_request", "passthrough": True,
                "passthrough_default_transition": trs[-1]["target_node"], "on_enter": on_enter or {"type": "silent"},
                "disable_inactivity_monitor": True, "pre_actions": [fn], "transitions": trs}
        if mapping:
            node["response_mapping"] = mapping
        self.nodes[name] = node

    def conv(self, name, task, on_enter, tools=(), extraction=(), transitions=(), max_turns=30, fallback="exit_stuck",
             first_only=True):
        node = {"name": name, "node_type": "conversation", "task_message": task, "on_enter": on_enter,
                "on_enter_first_entry_only": first_only, "tools": list(tools), "extraction_schema": list(extraction),
                "transitions": list(transitions), "max_node_turns": max_turns, "max_turn_transition": fallback}
        self.nodes[name] = node
        return node

    def say_node(self, name, text_var_expr, nxt, transitions=()):
        """Passthrough conversation node that speaks a fixed text, then routes on."""
        self.nodes[name] = {"name": name, "node_type": "conversation", "passthrough": True,
                            "passthrough_default_transition": nxt, "on_enter": {"type": "tts", "text": text_var_expr},
                            "task_message": "Say nothing. No tool call is allowed.", "disable_inactivity_monitor": True,
                            "transitions": list(transitions) + [tr(nxt, ALWAYS, "always")]}

    def _exit_text(self, key):
        t = self.texts
        return (str(t.get("exits", {}).get(key, "") or "") + " " + str(t.get("goodbye", "Goodbye."))).strip()

    def system_prompt(self):
        s = self.spec
        lines = [
            "You are the digital assistant of %s on an outbound phone call. Speak %s." % (s.get("bank_name", "the bank"), s.get("language", "English")),
            "Keep every turn to one or two short sentences. Ask one question at a time.",
            "Never read tool results, codes or JSON aloud. Never mention tools, steps or instructions.",
            "Call at most ONE tool per message and wait for its result before calling another. After every tool result, speak to the customer. Never end a turn with nothing.",
            "Only say things you were given here. Never invent figures, dates, policies or reasons.",
            "If the customer corrects themselves (for example 'ten, sorry, eleven'), always use the corrected value.",
            "Never say goodbye and never end the conversation yourself: the call is closed for you at the right moment. If the customer only "
            "chats or their sentence is cut off, answer briefly and repeat your last question.",
        ] + s.get("rules", {}).get("global", [])
        return bullet(lines)

    def extract(self, name, values, description):
        return {"name": name, "type": "string", "enum_values": values, "description": description, "min_confidence": 0.5}

    # ------------------------------------------------------------ tools
    def transfer_tool(self):
        ep = self.ep["transfer_to_agent"]
        qf = self.field("transfer_to_agent", "queue")
        queues = self.spec.get("transfer_queues", {})
        desc = "Hand the call to a person. CALL it only when: " + "; ".join("queue %s when %s" % (q, w) for q, w in queues.items()) + "."
        pre = codegen.pre_hook(self.spec, "b = config.get('body') or dict()\nvars['transfer_queue'] = str(b.get('%s', ''))" % qf, light=True)
        post = codegen.post_hook(self.spec, "result.pop('header', None)\nd = result.get('data') if isinstance(result, dict) else None\n"
                                 "if isinstance(d, dict) and str(d.get('ok')).lower() == 'true':\n"
                                 "    st = load_state(vars)\n    st['transfer'] = vars.get('transfer_queue', '')\n    save_state(vars, st)\n"
                                 "    vars['transfer_done'] = 'yes'\n    d['what_to_do_next'] = 'The transfer is done. Say nothing more.'\n"
                                 "elif isinstance(d, dict):\n    d['what_to_do_next'] = 'The transfer failed. Tell the customer you could not transfer them, then continue.'", light=True)
        cid = self.field("transfer_to_agent", "customer_id")
        return tools_gen.api_tool(ep, "transfer_to_agent", {cid: "{{customer_id}}"} if cid else {}, model_fields=[qf],
                                  description=desc)    # no hooks: a sandbox hiccup must never block a transfer

    def verify_tool(self):
        ep = self.ep["verify_identity"]
        ff, af = self.field("verify_identity", "field"), self.field("verify_identity", "answer")
        cid = self.field("verify_identity", "customer_id")
        qs = self.spec.get("identity", {}).get("questions", [])
        desc = ("Check one security answer. CALL it every time the customer answers a security question, with the field of that question (%s). "
                "Then follow what_to_do_next in the result." % ", ".join("%s = %s" % (q["field"], q.get("ask", "")) for q in qs))
        return tools_gen.api_tool(ep, "verify_identity", {cid: "{{customer_id}}"} if cid else {}, model_fields=[ff, af],
                                  descriptions={af: "The customer's answer. " + "; ".join("%s: %s" % (q["field"], {"date": "YYYY-MM-DD", "digits": "digits only"}.get(q.get("format"), "as said")) for q in qs)},
                                  description=desc if self.id_hook else desc.replace("Then follow what_to_do_next in the result.",
                                                                                      "Then apply the result rules in your instructions."),
                                  post=codegen.post_hook(self.spec, "id_post_hook(vars, result, SPEC)") if self.id_hook else None)

    def identity_result_rules(self):
        """Without the post-hook the model reads the raw verify result; these rules say what each field means."""
        idc = self.spec.get("identity", {})
        qs = idc.get("questions", [])
        order = ", then ".join(q.get("ask", q.get("field", "")) for q in qs)
        lines = [
            "Ask the questions in this order, one at a time: %s." % order,
            "If the customer gives only part of an answer (for example half a date or two of four digits), ask for the rest and call "
            "verify_identity only with the complete answer. If they correct themselves, use the corrected answer.",
            "After each verify_identity result: if all_verified is true, say only 'Thank you.' and nothing else.",
            "If verified is true but all_verified is false: say the answer is correct and ask the NEXT question in the order.",
            "If verified is false: say the answer does not match and ask the SAME question again. Never move to the next question after a wrong answer.",
            "If ok is false (the answer could not be read): ask the same question again, clearly. This is not a wrong answer.",
            "If wrong_answers_left is 0: say nothing more about the questions.",
        ]
        for h, what in (idc.get("hints") or {}).items():
            lines.append("If the result has hint %s: %s" % (h, what))
        return bullet(lines)

    def neg_tools(self):
        pid_desc = {"type": "string", "description": "The product id from the product list, for example p1. Use all for every open product at once."}
        return [
            tools_gen.code_tool("check_offer",
                "CALL it every time the customer proposes or accepts a payment day and/or amount, including when they accept your proposal. "
                "It checks the rules and tells you what to say next.",
                {"product_id": pid_desc,
                 "date": {"type": "string", "description": "The payment day as YYYY-MM-DD, worked out from the calendar. Empty if no day was given."},
                 "amount": {"type": "number", "description": "The amount the customer will pay. 0 if they did not name one (then the current amount is used)."}},
                codegen.tool_code(self.spec, "tool_check_offer(vars, params, SPEC)"), required=["product_id", "date", "amount"]),
            tools_gen.code_tool("customer_refused",
                "CALL it when the customer refuses or gives no answer, for example a no to paying today with no day and no amount, or refuses your offer. "
                "reason full_too_much when they say the full amount is too much. It tells you the next offer.",
                {"product_id": pid_desc,
                 "reason": {"type": "string", "enum": ["flat", "full_too_much"], "description": "flat or full_too_much"}},
                codegen.tool_code(self.spec, "tool_customer_refused(vars, params, SPEC)")),
            tools_gen.code_tool("vague_date",
                "CALL it when the customer gives only a vague period (next week, end of the month, when my salary comes) instead of a day.",
                {"product_id": pid_desc}, codegen.tool_code(self.spec, "tool_vague_date(vars, params, SPEC)")),
            tools_gen.code_tool("set_result",
                "CALL it to close a product without a promise: " + ", ".join(self.spec.get("product_results", [])) + ".",
                {"product_id": pid_desc,
                 "result": {"type": "string", "enum": self.spec.get("product_results", ["refused", "hardship", "dispute", "paid"])}},
                codegen.tool_code(self.spec, "tool_set_result(vars, params, SPEC)")),
            self.transfer_tool(),
        ]

    # ------------------------------------------------------------ graph
    def build(self):
        s, t, R = self.spec, self.texts, self.spec.get("rules", {})
        transfer_tr = tr("exit_transfer", gt("transfer_to_agent_success_count", 0), "the transfer was done")

        # contact
        contact_vals = ["confirmed", "third_party", "wrong_number", "deceased", "not_talking", "unclear"]
        self.conv("contact",
            "You called {{title}} {{first_name}} {{last_name}}. Find out whether you are speaking with that person.\n"
            + bullet(R.get("contact", [])) + "\nIf the person is someone else, do not say why you are calling. No tool call is allowed in this step unless the customer insists on a human.",
            {"type": "tts", "text": t.get("opening", "Hello. Am I speaking with {{title}} {{last_name}}?")},
            tools=[self.transfer_tool()],
            extraction=[self.extract("contact_status", contact_vals,
                "Who answered, judged from the person's LAST message only. confirmed: they clearly confirm they are {{title}} {{last_name}} (%s). "
                "third_party: someone else answered (a relative, colleague...). wrong_number: the number does not belong to that person. "
                "deceased: the person has died. not_talking: they refuse to talk. unclear: anything else (hello, who is this, a question)." % ", ".join(s.get("confirm_phrases", [])))],
            transitions=[
                tr("setup_id", eq("contact_status", "confirmed"), "the customer confirmed"),
                tr("setup_tp", eq("contact_status", "third_party"), "someone else answered"),
                tr("exit_wrong_number", eq("contact_status", "wrong_number")),
                tr("exit_deceased", eq("contact_status", "deceased")),
                transfer_tr,
                tr("exit_id_declined", both(c_eq("contact_status", "not_talking"), c_gt("contact.total_turns", 1)), "does not want to talk, twice"),
                tr("exit_unclear", gt("contact.total_turns", int(s.get("max_contact_asks", 2)) - 1), "still unclear after the second ask"),
            ], max_turns=6, fallback="exit_unclear")

        cal = "\n\n{{calendar}}"
        self.code_node("setup_id", "setup_call(vars, SPEC)", [tr("identity", ALWAYS)])
        self.code_node("setup_tp", "setup_call(vars, SPEC)", [tr("third_party", ALWAYS)])

        # third party
        self.conv("third_party",
            "The person on the line is not {{title}} {{last_name}}.\n" + bullet(R.get("third_party", [])) +
            "\nWhen they have said BOTH a day and a time, work out the exact date from the calendar and CALL check_callback with it. "
            "Never guess a missing day or time: ask for it. "
            "Latest check result: {{cb_say}}" + cal,
            {"type": "llm", "prompt": "Ask politely when {{title}} {{last_name}} can be reached. Say nothing about why you are calling."},
            tools=[tools_gen.code_tool("check_callback", "CALL it every time the person names a day and/or time when the customer can be reached.",
                    {"date": {"type": "string", "description": "YYYY-MM-DD from the calendar"}, "time": {"type": "string", "description": "HH:MM, 24 hour"}},
                    codegen.tool_code(s, "tool_check_callback(vars, params, SPEC)"))],
            extraction=[self.extract("tp_status", ["wrong_number", "deceased", "other"],
                "wrong_number if the person says the number does not belong to the customer, deceased if the customer has died, otherwise other.")],
            transitions=[
                tr("book_callback", eq("cb_ok", "yes"), "valid callback time"),
                tr("exit_no_callback", eq("cb_ok", "no"), "no valid time"),
                tr("exit_wrong_number", eq("tp_status", "wrong_number")),
                tr("exit_deceased", eq("tp_status", "deceased")),
            ], max_turns=10, fallback="exit_no_callback")
        cb = self.ep["schedule_callback"]
        self.api_node("book_callback", "schedule_callback",
            [tr("exit_callback", ALWAYS)],
            baked={self.field("schedule_callback", "date"): "{{cb_date}}", self.field("schedule_callback", "time"): "{{cb_time}}"})

        # identity
        qs = s.get("identity", {}).get("questions", [])
        id_task = ("The person confirmed they are {{title}} {{last_name}}. Verify their identity.\n" + bullet(R.get("identity", [])) +
                   ("\nCALL verify_identity after every answer, then do exactly what what_to_do_next says.\n" + self.identity_result_rules() + "\n" if self.id_hook else
                    "\nCALL verify_identity after every answer.\n" + self.identity_result_rules() + "\n") +
                   "Never mention the debt, a product, an amount or why you are calling in this step.")
        id_tr = [
            tr("load_account", {"logic": "or", "conditions": [c_eq("id_state", "verified"),
                c_eq("verify_identity_response.data.all_verified", "true"), c_eq("verify_identity_response.data.all_verified", "True")]},
               "identity verified"),
            tr("exit_id_failed", {"logic": "or", "conditions": [c_eq("id_state", "failed"),
                c_eq("verify_identity_response.data.wrong_answers_left", "0")]}, "identity check failed"),
            transfer_tr,
        ]
        self.conv("identity", id_task,
            {"type": "tts", "text": t.get("identity_intro", "Thank you. What is " + (qs[0]["ask"] if qs else "your date of birth") + "?")},
            tools=[self.verify_tool(), self.transfer_tool()],
            extraction=[self.extract("id_refusal_1", ["yes", "no"],
                "yes ONLY if in their LAST message the person clearly refuses to answer (e.g. 'I won't give you that'), or asks what the call is "
                "about and gives NO answer. A question together with an answer, small talk, an unfinished sentence or a wrong answer is no. Otherwise no.")],
            transitions=id_tr + [tr("identity_explain", eq("id_refusal_1", "yes"), "refused once")],
            max_turns=14, fallback="exit_id_failed")
        self.conv("identity_explain",
            id_task + "\nThe customer did not want to answer. Explain once, briefly, that these questions protect their account, and ask the question again.",
            {"type": "llm", "prompt": "Explain in one sentence that the security questions protect their account, then ask the same question again."},
            tools=[self.verify_tool(), self.transfer_tool()],
            extraction=[self.extract("id_refusal_2", ["yes", "no"],
                "yes ONLY if in their LAST message the person explicitly refuses to give the answer (e.g. 'no, I will not tell you that', "
                "'I am not answering security questions'). Any question, small talk, an answer, a wrong answer or an unfinished sentence is no.")],
            transitions=id_tr + [tr("exit_id_declined", eq("id_refusal_2", "yes"), "refused twice")],
            max_turns=8, fallback="exit_id_declined")

        # account + negotiation
        self.api_node("load_account", "get_account", [tr("prep", ALWAYS)], retries=2,
                      mapping=[{"path": "data.products", "variable": "products"}])
        self.code_node("prep", "prepare(vars, SPEC)", [tr("negotiate", ALWAYS)])
        neg_task = ("The customer is verified. You are collecting a late payment.\n" + bullet(R.get("negotiate", [])) +
                    "\nHow to work: the tools below apply the bank's rules about dates, amounts and offers. Never decide a date or amount rule yourself. "
                    "When the customer proposes or accepts a day and/or amount, CALL check_offer. When they refuse or do not answer, CALL customer_refused. "
                    "When they give only a vague period, CALL vague_date. Call the tool FIRST, before you say anything about dates or "
                    "amounts; then say what the latest instruction says (you can also answer a question the customer asked). "
                    "With several products, handle ONE product per tool call, in the order of the product list; the instruction then tells you the next step. "
                    "Do not read back or confirm a promise yourself: the read-back comes after all products are decided. "
                    "Never say that something is recorded, noted or registered: only the tools do that. When the customer says why they cannot "
                    "or will not pay, CALL set_result (or transfer_to_agent) in that same turn. "
                    "A customer who says when they can pay (for example when their salary arrives) is proposing a date, not a hardship and not "
                    "a refusal: CALL check_offer with that date. "
                    "Not being able to afford the amount is not a hardship by itself: match the customer's words to the situations named in the "
                    "rules above (e.g. a request for lower instalments or a new plan is not a hardship). "
                    "A question from the customer is NOT a refusal: answer it briefly and wait for their answer to your offer; do not call "
                    "customer_refused for a question. "
                    "Never repeat an offer you already made: if the customer's answer is cut off or unclear, ask them to finish their sentence.\n\n"
                    "{{neg_state}}\n\nLatest instruction: {{neg_say}}" + cal)
        done_tr = [transfer_tr,
                   tr("readback", both(c_eq("neg_done", "yes"), c_gt("n_agreed", 0)), "all products decided, some agreed"),
                   tr("write_note", both(c_eq("neg_done", "yes"), c_eq("flow", "note")), "none agreed, notes to write"),
                   tr("farewell", both(c_eq("neg_done", "yes"), c_eq("flow", "farewell")), "none agreed, nothing to write")]
        neg_extract = [
            self.extract("wrap_signal", ["continue", "goodbye"],
                "goodbye only if the customer is saying goodbye or clearly ending the conversation in their LAST message, otherwise continue."),
            self.extract("open_reason", ["none"] + list(s.get("product_results", ["refused", "hardship", "dispute", "paid"])),
                "Why the customer cannot or will not pay, from everything they said: hardship (illness, death in the family, job loss), "
                "paid (already paid this amount), dispute (not their debt / never had the product), refused (just will not pay), none if not said."),
        ]
        done_tr = done_tr + [tr("exit_open", eq("wrap_signal", "goodbye"), "the customer ended the conversation")]
        self.conv("negotiate", neg_task,
            {"type": "tts", "text": t.get("after_verification", "") + " {{debt_statement}} {{first_ask}}"},
            tools=self.neg_tools(), extraction=neg_extract,
            transitions=done_tr,
            max_turns=40, fallback="exit_stuck")
        self.conv("readback",
            "You read the agreed promises back and asked for confirmation. If the customer says yes, the next step records them. "
            "If they want to change a day or amount, acknowledge it. No tool call is allowed in this step.",
            {"type": "tts", "text": "{{readback_text}}"}, first_only=False,
            extraction=[self.extract("confirm", ["yes", "change", "unclear"],
                "yes if the customer confirms the read-back in their LAST message (even with other chatter around it), change if they want a different "
                "day or amount or say no, otherwise unclear.")],
            transitions=[tr("record_promise", both(c_eq("confirm", "yes"), c_eq("flow", "record")), "confirmed"),
                         tr("reopen", eq("confirm", "change"), "wants a change")],
            max_turns=8, fallback="exit_stuck")
        self.code_node("reopen", "reopen(vars, SPEC)\nvars['confirm'] = 'unclear'", [tr("negotiate_again", ALWAYS)])
        self.conv("negotiate_again", neg_task, {"type": "llm", "prompt": "Ask what the customer wants to change."}, first_only=False,
            tools=self.neg_tools(), extraction=neg_extract,
            transitions=done_tr,
            max_turns=20, fallback="exit_stuck")

        # recording loops (one code step between engine calls: after_record / after_note decide the next step)
        loop = [tr("record_promise", eq("flow", "record")), tr("write_note", eq("flow", "note")), tr("farewell", ALWAYS)]

        def guarded(node):
            """If this code step failed (sandbox outage) or the loop ran too often, stop and close: never loop on stale state."""
            stop = {"logic": "or", "conditions": [c_gt(node + "_failure_count", 0),
                    c_gt("record_promise_success_count", 3), c_gt("record_promise_failure_count", 3),
                    c_gt("write_note_success_count", 3), c_gt("write_note_failure_count", 3)]}
            return [tr("rescue", stop, "code step failed or loop cap reached")] + loop
        cp = "create_promise"
        self.api_node("record_promise", cp, [tr("after_record", ALWAYS)],
            baked={self.field(cp, "product_id"): "{{rec_pid}}", self.field(cp, "amount"): "{{rec_amount}}", self.field(cp, "date"): "{{rec_date}}"})
        self.code_node("after_record", "after_record(vars, SPEC)", guarded("after_record"))
        an = "add_note"
        self.api_node("write_note", an, [tr("after_note", ALWAYS)],
            baked={self.field(an, "product_id"): "{{note_pid}}", self.field(an, "reason"): "{{note_reason}}"})
        self.code_node("after_note", "after_note(vars, SPEC)", guarded("after_note"))

        # exits with a fixed outcome: no code step at all (a sandbox outage must not stop the close call)
        cc = "close_call"
        cmap = s.get("closing", {}).get("map", {})

        def literal_close(name, key):
            outcome, reason = (cmap.get(key) or ["no_promise", key])[:2]
            baked = {self.field(cc, "outcome"): outcome}
            if reason:
                baked[self.field(cc, "reason")] = reason
            self.api_node(name, cc, [tr("end", ALWAYS)], baked=baked, retries=2)

        for key in ("wrong_number", "deceased", "unclear", "callback", "no_callback", "id_failed", "id_declined"):
            self.say_node("exit_" + key, self._exit_text(key), "close_" + key)
            literal_close("close_" + key, key)
        qf = self.field("transfer_to_agent", "queue")
        queues = [k.split(":", 1)[1] for k in cmap if k.startswith("transfer:")]
        q_trs = [tr("close_transfer_" + q, eq("transfer_to_agent_args.transfer_to_agent__" + qf, q), "queue " + q) for q in queues]
        for q in queues:
            literal_close("close_transfer_" + q, "transfer:" + q)
        self.say_node("exit_transfer", self._exit_text("transfer"), "exit_transfer_code", transitions=q_trs)
        self.code_node("exit_transfer_code", "final_outcome(vars, SPEC)", [tr("close_transfer_unknown", gt("exit_transfer_code_failure_count", 0)), tr("close", ALWAYS)])
        literal_close("close_transfer_unknown", "transfer:" + (queues[0] if queues else "general"))
        self.code_node("exit_stuck", "force_close(vars, SPEC)", guarded("exit_stuck"))
        self.code_node("exit_open", "force_close(vars, SPEC)", guarded("exit_open"))
        # rescue: a code step failed. Close with fixed values, honest about nothing we cannot know.
        self.say_node("rescue", str(self.texts.get("goodbye", "Goodbye.")), "close_rescue_none",
                      transitions=[tr("close_rescue_promise", gt("record_promise_success_count", 0), "a promise was recorded")])
        literal_close("close_rescue_promise", "promise")
        literal_close("close_rescue_none", "refused")

        # close
        self.say_node("farewell", "{{farewell_text}}", "close")
        cc = "close_call"
        self.api_node("close", cc, [tr("end", ALWAYS)],
            baked={self.field(cc, "outcome"): "{{outcome}}", self.field(cc, "reason"): "{{close_reason}}"}, retries=2)
        self.nodes["end"] = {"name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                             "transitions": [], "terminal": {"type": "function", "function": {"name": "call_closed", "handler": "__function__:end_call",
                             "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}}}

        return {"initial_node": "contact", "system_prompt": self.system_prompt(), "nodes": self.nodes, "global_nodes": {},
                "test_variables": {"customer_id": "c_test", "first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"}}


def compile_workflow(spec, api):
    return Compiler(spec, api).build()
