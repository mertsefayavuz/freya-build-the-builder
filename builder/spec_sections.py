"""The spec split into sections, one small focused LLM call each. Together they cover every key of the spec."""

COMMON = ("Every value must come from the bank's documents; if they say nothing, use the default named here. "
          "Return ONLY a JSON object with exactly the top-level keys of the skeleton, filled in. "
          "Values in angle brackets in the skeleton like <sentence> are placeholders you must replace with real content. "
          "Do not leave a value empty unless the documents clearly require nothing there.")

SECTIONS = {
    "money": {
        "tools": ["get_account", "create_promise"],
        "doc": r"""
bank_name: string. language: language the agent speaks (default "English"). currency: word said after amounts, "" if the documents use none.
calendar: {"workdays": business weekdays, Monday=0..Sunday=6 (default [0,1,2,3,4]), "holidays": every bank holiday as "YYYY-MM-DD"}.
product_fields: names of the fields of a product object returned by the account tool: {"id", "type", "amount" (past-due amount), "days_late"}.
products: map product type value (as the account tool returns it) -> {"label": how the agent names it, with <field> placeholders,
  e.g. "credit card ending <card_last4>", "floor_field": product field holding the minimum acceptable amount ("" = none, floor is the full amount)}.
bands: list ordered by days late, each {"name", "min_days", "max_days" (inclusive),
  "deadline": {"kind": "business_days_after_today" | "calendar_days_after_today" | "end_of_month", "n": int},
  "attempts": one item per allowed persuasion attempt, in order, each {"date": DATE_EXPR, "amount": AMOUNT_EXPR}}.
  DATE_EXPR: "today" | "deadline" | "last_business_day_this_week_else_next" (last business day of this week; if today is that day, of next week)
             | "next_business_day" | "business_days_after_today:N" | "calendar_days_after_today:N".
  AMOUNT_EXPR: "current" (full amount, or the floor if the customer already said the full amount is too much) | "full" | "floor".
promise: {"vague_asks": times to ask for an exact day after a vague period before proposing the deadline (default 1)}.
confirmation: {"retries": times a failed promise call is retried (default 1)}.
multi: {"clamp_common_date_to_deadline": true if, when a customer names one date for all products and it is after one product's deadline,
  that product is recorded on its own deadline; else false}.""",
        "skeleton": {"bank_name": "", "language": "English", "currency": "", "calendar": {"workdays": [0, 1, 2, 3, 4], "holidays": []},
                     "product_fields": {"id": "", "type": "", "amount": "", "days_late": ""},
                     "products": {"<type>": {"label": "", "floor_field": ""}},
                     "bands": [{"name": "", "min_days": 0, "max_days": 0, "deadline": {"kind": "business_days_after_today", "n": 0},
                                "attempts": [{"date": "", "amount": ""}]}],
                     "promise": {"vague_asks": 1}, "confirmation": {"retries": 1}, "multi": {"clamp_common_date_to_deadline": False}},
    },
    "people": {
        "tools": ["verify_identity", "schedule_callback"],
        "doc": r"""
identity: {"questions": ordered list, each {"field": exact value of the verify tool's field enum, "ask": how to ask it as a noun phrase,
             "format": "date" (sent as YYYY-MM-DD) | "digits" | "text" | "year"},
  "numeric_date_order": "DMY" or "MDY" (how to read a date said only as numbers), "max_wrong": total wrong answers allowed,
  "hints": map of hint values the verify tool can return -> what the agent must do}.
callback: when someone else answers: {"days": DATE_EXPR list of allowed callback days (e.g. ["today","next_business_day"]),
  "start": "HH:MM", "end": "HH:MM" (inclusive), "after_now": true if it must be later than now,
  "max_invalid": invalid proposals before closing without a callback (asking once more = 2)}.
confirm_phrases: what counts as the customer confirming who they are.
max_contact_asks: times to ask who is on the line before closing as unclear (default 2).""",
        "skeleton": {"identity": {"questions": [{"field": "", "ask": "", "format": ""}], "numeric_date_order": "DMY", "max_wrong": 3, "hints": {}},
                     "callback": {"days": ["today", "next_business_day"], "start": "", "end": "", "after_now": True, "max_invalid": 2},
                     "confirm_phrases": [], "max_contact_asks": 2},
    },
    "closing": {
        "tools": ["close_call", "transfer_to_agent", "add_note"],
        "doc": r"""
closing: {"map": event -> [outcome, reason] for the close tool; reason "" when none. Events (all required): promise, paid,
  transfer:<queue> for EVERY queue of the transfer tool, refused, hardship, dispute, service_error, id_failed (too many wrong identity answers),
  id_declined (refused identity questions / does not want to talk before verification), callback, no_callback, wrong_number, deceased,
  unclear (could not confirm who answered). Use exactly the outcome and reason values of the documents' outcome table and the tool enums.}
transfer_queues: map each queue value of the transfer tool -> when to use it.
product_results: ways a product can end without a promise, from "refused", "hardship", "dispute", "paid".
notes: {"when": "never" | "always" | "multi_product_only" (when a note tool call is made for each product without a promise),
        "reasons": allowed reason values of the note tool}.""",
        "skeleton": {"closing": {"map": {"promise": ["", ""], "paid": ["", ""], "transfer:<queue>": ["", ""], "refused": ["", ""],
                     "hardship": ["", ""], "dispute": ["", ""], "service_error": ["", ""], "id_failed": ["", ""], "id_declined": ["", ""],
                     "callback": ["", ""], "no_callback": ["", ""], "wrong_number": ["", ""], "deceased": ["", ""], "unclear": ["", ""]}},
                     "transfer_queues": {"<queue>": ""}, "product_results": ["refused", "hardship", "dispute", "paid"],
                     "notes": {"when": "never", "reasons": []}},
    },
    "texts": {
        "tools": [],
        "doc": r"""
texts: what the agent says. Use the documents' exact wording where they give one (quote it word for word). {{title}} {{first_name}} {{last_name}}
are call variables (keep the double braces); <label>, <past_due>, <days_late>, <list> are filled by code.
  opening: the first sentence as required (who is calling, recording notice...), ending with the confirm question with {{title}} {{last_name}}.
           It must not mention the reason for the call if the documents forbid that before verification.
  identity_intro: short thanks + the first security question.
  after_verification: said right after identity is verified, before the products (which team is calling and why).
  product_sentence: one sentence per late product using <label>, <past_due>, <days_late>.
  first_ask: the first question for one product. first_ask_multi: the first question when there are several products.
  rest_overdue: instruction added when a reduced amount is offered, e.g. "Say that the rest stays overdue." ("" if none).
  readback: read-back sentence containing <list> and the confirmation question the documents require.
  after_promise: exact sentence the documents require after a recorded promise ("" only if there is none).
  record_failed: start of the sentence when a promise could not be recorded.
  goodbye: thanks and goodbye.""",
        "skeleton": {"texts": {"opening": "<sentence>", "identity_intro": "<sentence>", "after_verification": "<sentence>",
                     "product_sentence": "<sentence with <label> <past_due> <days_late>>", "first_ask": "<question>",
                     "first_ask_multi": "<question>", "rest_overdue": "<instruction>", "readback": "<sentence with <list>>",
                     "after_promise": "<exact sentence>", "record_failed": "<sentence start>", "goodbye": "<sentence>"}},
    },
    "exits": {
        "tools": [],
        "doc": r"""
exits: sentence the agent says before the goodbye when the call ends this way. Use the documents' exact wording where they give one.
  id_failed (identity check failed: e.g. where to go instead), id_declined (refused the security questions), deceased (condolence sentence),
  wrong_number, unclear (could not confirm who answered), callback (callback booked), no_callback (no valid callback time),
  transfer (being transferred to a person), not_talking.
result_say: sentence said at the end when a product ends that way without a promise: paid (e.g. how long a payment takes to appear),
  dispute, hardship, refused ("" if nothing is required).""",
        "skeleton": {"exits": {"id_failed": "<sentence>", "id_declined": "<sentence>", "deceased": "<sentence>", "wrong_number": "<sentence>",
                     "unclear": "<sentence>", "callback": "<sentence>", "no_callback": "<sentence>", "transfer": "<sentence>",
                     "not_talking": "<sentence>"},
                     "result_say": {"paid": "<sentence>", "dispute": "<sentence>", "hardship": "<sentence>", "refused": "<sentence>"}},
    },
    "rules_call": {
        "tools": [],
        "doc": r"""
rules_call: plain-English instructions for the phone agent before the debt is discussed, as short imperative sentences, each list with
every relevant rule of the documents (exact required wording in quotes). At least 2 rules per list.
  global: for the whole call (robot/AI question, scam question, questions you cannot answer, never offer discounts or plans...).
  contact: before identity is verified (what may never be said, what counts as confirmation, answering questions first).
  third_party: when someone else answers (what may be said, relatives, offers to pay, asking when the customer can be reached).
  identity: how to ask and send the security answers, re-asking, declining.""",
        "skeleton": {"rules_call": {"global": ["<rule>", "<rule>"], "contact": ["<rule>", "<rule>"], "third_party": ["<rule>", "<rule>"],
                     "identity": ["<rule>", "<rule>"]}},
    },
    "rules_negotiate": {
        "tools": ["transfer_to_agent"],
        "doc": r"""
rules_negotiate: plain-English instructions for the phone agent after identity is verified, as short imperative sentences.
Dates, deadlines, amounts, floors and the number of persuasion attempts are enforced by code: do not restate their numbers, but do
state behaviour (e.g. "Never ask how much the customer can pay"). Tools available: check_offer (customer proposes/accepts a day and/or
amount), customer_refused (flat refusal or non-answer; reason full_too_much), vague_date, set_result (product ends without promise:
refused, hardship, dispute, paid; product_id "all" for every product), transfer_to_agent (queue).
Write EVERY special case of the documents as "If <situation>: <what to say>, then call <tool> with <value>." — for example already paid,
dispute / not mine, hardship (illness, death, job loss), payment plan or lower instalments, asking for a human, not wanting to talk,
asking to change a recorded promise. At least 6 rules.
extra_rules: anything else the documents require that fits nowhere else (list of sentences, may be empty).""",
        "skeleton": {"rules_negotiate": ["<rule>", "<rule>", "<rule>", "<rule>", "<rule>", "<rule>"], "extra_rules": []},
    },
}

SECTION_OF_KEY = {k: name for name, sec in SECTIONS.items() for k in sec["skeleton"]}


def section_of_error(err):
    """Which section a validate_spec error belongs to."""
    for word, name in (("texts.exits", "exits"), ("texts.result_say", "exits"), ("texts.", "texts"),
                       ("rules.negotiate", "rules_negotiate"), ("rules", "rules_call"),
                       ("closing", "closing"), ("transfer", "closing"), ("identity", "people"), ("callback", "people"),
                       ("band", "money"), ("holiday", "money"), ("product", "money")):
        if word in err:
            return name
    for key, name in SECTION_OF_KEY.items():
        if key in err:
            return name
    return "money"
