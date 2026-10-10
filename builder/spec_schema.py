"""The spec: everything brief-specific the compiler needs. The LLM fills it; validate_spec checks it.

SCHEMA_DOC is shown to the LLM. EXAMPLE is a made-up bank (not any real brief) that shows the shape.
"""
import json, re

SCHEMA_DOC = r"""
The spec is one JSON object. Every value must come from the brief documents. If the brief does not say something,
use the default named here. Placeholders in texts: {{title}}, {{first_name}}, {{last_name}} are call variables
(write them with double braces); <label>, <past_due>, <days_late>, <floor>, <list> are filled by code (angle brackets).

bank_name: string. language: language the agent speaks (default "English"). currency: word to say after amounts, "" if the brief uses none.

calendar:
  workdays: weekday numbers that are business days, Monday=0 ... Sunday=6 (default [0,1,2,3,4]).
  holidays: list of "YYYY-MM-DD" bank holidays (non-business days). Include every one the brief lists.

product_fields: names of the fields in the account tool's product objects:
  {"id": ..., "type": ..., "amount": <past-due amount field>, "days_late": <days-late field>}. Take them from the tool API file.

products: map product type value (as returned by the account tool) ->
  {"label": how the agent names it, with <field> placeholders for product fields, e.g. "credit card ending <card_last4>",
   "floor_field": product field that holds the minimum acceptable amount for that type ("" if none: then the floor is the full amount)}

bands: list ordered by days late. Each:
  {"name", "min_days", "max_days" (inclusive),
   "deadline": {"kind": "business_days_after_today" | "calendar_days_after_today" | "end_of_month", "n": int},
   "attempts": list of persuasion offers, one per allowed persuasion attempt, in order. Each {"date": DATE_EXPR, "amount": AMOUNT_EXPR}}
  DATE_EXPR: "today" | "deadline" | "last_business_day_this_week_else_next" (last business day of this week; if today is that day, of next week)
             | "next_business_day" | "business_days_after_today:N" | "calendar_days_after_today:N".
  AMOUNT_EXPR: "current" (full amount, or the floor if the customer already said the full amount is too much) | "full" | "floor".
  The number of attempts per band = the number of items in "attempts".

promise: {"vague_asks": how many times to ask for an exact day after a vague period before proposing the deadline (default 1)}.
product_results: allowed ways a product can end without a promise, from: "refused", "hardship", "dispute", "paid" (keep all four unless the brief removes one).
confirmation: {"retries": how many times a failed promise call is retried (default 1)}.

notes: when the brief wants a note tool call (add_note) for products without a promise:
  {"when": "never" | "always" | "multi_product_only", "reasons": allowed reason values of the note tool (from the tool API enum)}.
  Use "never" if the brief says notes are not used. Notes are never written when the call is transferred, if the brief says so.
multi: {"clamp_common_date_to_deadline": true if, when a customer names one date for all products and it is after one product's
  deadline, that product is recorded on its own deadline (and told in the read-back); else false}.

callback: when someone else answers and a callback may be booked:
  {"days": list of DATE_EXPR allowed for the callback day (e.g. ["today","next_business_day"]), "start": "HH:MM", "end": "HH:MM" (inclusive),
   "after_now": true if it must be later than now, "max_invalid": how many invalid proposals end without a callback (asking once more = 2)}.

identity:
  questions: ordered list, each {"field": the exact value of the verify tool's field enum, "ask": how to ask it as a noun phrase,
             "format": "date" (send YYYY-MM-DD) | "digits" | "text" | "year"}.
  numeric_date_order: "DMY" or "MDY" (how to read a date said only as numbers).
  max_wrong: total wrong answers allowed before failing.
  hints: map of hint values the verify tool can return -> what the agent must do.

texts:
  opening: the first sentence, exactly as the brief requires, ending with the confirm question with {{title}} {{last_name}}.
  identity_intro: thanks + the first security question.
  after_verification: what to say right after identity is verified, before the products (e.g. the team you call from and why).
  product_sentence: one sentence per late product, using <label>, <past_due>, <days_late>.
  first_ask: the first question for one product. first_ask_multi: the first question when there are several products.
  rest_overdue: sentence to add when a reduced amount is offered (e.g. "Say that the rest stays overdue.").
  readback: read-back sentence with <list> and the confirmation question the brief requires.
  after_promise: exact sentence the brief requires after a recorded promise ("" if none).
  record_failed: start of the sentence when a promise could not be recorded.
  goodbye: thanks and goodbye.
  exits: map exit -> sentence said before the goodbye: id_failed, id_declined, deceased, wrong_number, unclear, callback,
         no_callback, transfer, not_talking. Use the brief's exact wording where it gives one (e.g. condolence sentence, where to go after failed identity).
  result_say: map product result -> sentence said at the end when a product ends that way: paid, dispute, hardship, refused.

closing:
  map: event -> [outcome, reason] for the close tool, reason "" when none. Events: promise, paid, transfer:<queue> for every transfer queue,
       refused, hardship, dispute, service_error, id_failed, id_declined, callback, no_callback, wrong_number, deceased, unclear.
       Use exactly the outcome and reason values of the brief's outcome table / the tool API enums.

confirm_phrases: what counts as the customer confirming who they are.
max_contact_asks: how many times to ask who is on the line before closing as unclear (default 2).
transfer_queues: map queue value (from the transfer tool enum) -> when to use it.

rules: plain-English instructions for the phone agent, grouped by step. Short imperative sentences. Include EVERY behavioural rule of
  the brief that is not already captured by the structured fields above, with exact required wording in quotes.
  global: rules for the whole call (robot/AI question, scam question, unknown questions, never offer discounts...).
  contact: before identity is verified (what may not be said, what counts as confirmation).
  third_party: when someone else answers.
  identity: how to ask and send the security answers, declining.
  negotiate: after verification. The tools available here are: check_offer (the customer proposes or accepts a day/amount),
    customer_refused (flat refusal / non-answer, or reason full_too_much), vague_date, set_result (product ends without promise:
    refused, hardship, dispute, paid; product_id "all" for every product), transfer_to_agent (queue). Write each special case as
    "If <situation>: <what to say>, then call <tool> with <value>." (already paid, dispute, hardship, payment plan request,
    human request, not wanting to talk, changing a recorded promise...).
extra_rules: list of anything else the brief requires that fits nowhere above.
tool_map: optional {tool name: {role: field name}} when a tool API field name differs from the usual role name
  (roles: customer_id, product_id, amount, date, time, field, answer, reason, text, queue, outcome).
"""

REQUIRED = ["bank_name", "calendar", "product_fields", "products", "bands", "identity", "texts", "closing", "callback", "rules"]
DATE_EXPR = re.compile(r"^(today|deadline|last_business_day_this_week_else_next|end_of_week|next_business_day|"
                       r"business_days_after_today:\d+|nth_business_day:\d+|calendar_days_after_today:\d+)$")
CLOSE_EVENTS = ["promise", "paid", "refused", "hardship", "dispute", "service_error", "id_failed", "id_declined",
                "callback", "no_callback", "wrong_number", "deceased", "unclear"]


def validate_spec(spec, api_endpoints=None):
    """Return a list of human-readable problems. Empty list = usable."""
    errs = []
    if not isinstance(spec, dict):
        return ["spec is not a JSON object"]
    for k in REQUIRED:
        if k not in spec:
            errs.append("missing top-level key: " + k)
    for h in spec.get("calendar", {}).get("holidays", []):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(h)):
            errs.append("holiday not YYYY-MM-DD: %r" % h)
    bands = spec.get("bands", [])
    if not bands:
        errs.append("bands is empty")
    for b in bands:
        for k in ("name", "min_days", "max_days", "deadline", "attempts"):
            if k not in b:
                errs.append("band %r missing %s" % (b.get("name"), k))
        for a in b.get("attempts", []):
            if not DATE_EXPR.match(str(a.get("date", ""))):
                errs.append("band %r attempt date expr invalid: %r" % (b.get("name"), a.get("date")))
            if a.get("amount") not in ("current", "full", "floor"):
                errs.append("band %r attempt amount expr invalid: %r" % (b.get("name"), a.get("amount")))
        dl = b.get("deadline", {})
        if dl.get("kind") not in ("business_days_after_today", "calendar_days_after_today", "end_of_month"):
            errs.append("band %r deadline kind invalid" % b.get("name"))
    if not spec.get("products"):
        errs.append("products is empty")
    qs = spec.get("identity", {}).get("questions", [])
    if not qs:
        errs.append("identity.questions is empty")
    if api_endpoints and "verify_identity" in api_endpoints:
        ep = api_endpoints["verify_identity"]
        field_enum = (ep["props"].get("field") or {}).get("enum")
        if field_enum:
            for q in qs:
                if q.get("field") not in field_enum:
                    errs.append("identity question field %r not in the verify tool enum %s" % (q.get("field"), field_enum))
    cmap = spec.get("closing", {}).get("map", {})
    for e in CLOSE_EVENTS:
        if e not in cmap:
            errs.append("closing.map missing event " + e)
    for e, v in cmap.items():
        if not (isinstance(v, list) and len(v) == 2):
            errs.append("closing.map[%s] must be [outcome, reason]" % e)
    if api_endpoints and "close_call" in api_endpoints:
        oenum = (api_endpoints["close_call"]["props"].get("outcome") or {}).get("enum")
        if oenum:
            for e, v in cmap.items():
                if isinstance(v, list) and v and v[0] not in oenum:
                    errs.append("closing.map[%s] outcome %r not in the close tool enum %s" % (e, v[0], oenum))
    if api_endpoints and "transfer_to_agent" in api_endpoints:
        qenum = (api_endpoints["transfer_to_agent"]["props"].get("queue") or {}).get("enum") or []
        for q in qenum:
            if "transfer:" + q not in cmap:
                errs.append("closing.map missing event transfer:%s" % q)
    t = spec.get("texts", {})
    for k in ("opening", "product_sentence", "readback", "goodbye"):
        if not t.get(k):
            errs.append("texts.%s is empty" % k)
    if "{{last_name}}" not in t.get("opening", ""):
        errs.append("texts.opening must contain {{last_name}}")
    if "<list>" not in t.get("readback", "<list>"):
        errs.append("texts.readback must contain <list>")
    return errs


def apply_defaults(spec):
    spec.setdefault("language", "English")
    spec.setdefault("currency", "")
    spec.setdefault("promise", {}).setdefault("vague_asks", 1)
    spec.setdefault("product_results", ["refused", "hardship", "dispute", "paid"])
    spec.setdefault("confirmation", {}).setdefault("retries", 1)
    spec.setdefault("notes", {}).setdefault("when", "never")
    spec["notes"].setdefault("reasons", ["refused", "hardship", "dispute", "paid", "service_error"])
    spec.setdefault("multi", {}).setdefault("clamp_common_date_to_deadline", False)
    spec.setdefault("max_contact_asks", 2)
    spec.setdefault("product_fields", {"id": "product_id", "type": "type", "amount": "past_due", "days_late": "days_late"})
    cal = spec.setdefault("calendar", {})
    cal.setdefault("workdays", [0, 1, 2, 3, 4])
    cal.setdefault("holidays", [])
    t = spec.setdefault("texts", {})
    t.setdefault("exits", {})
    t.setdefault("result_say", {})
    t.setdefault("first_ask_multi", t.get("first_ask", "Can you pay all of them today?"))
    t.setdefault("record_failed", "I am sorry, I could not record the promise for")
    spec.setdefault("rules", {})
    for k in ("global", "contact", "third_party", "identity", "negotiate"):
        spec["rules"].setdefault(k, [])
    if spec.get("extra_rules"):
        spec["rules"]["global"] = list(spec["rules"]["global"]) + list(spec["extra_rules"])
    return spec
