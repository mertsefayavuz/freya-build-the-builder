import copy, json, pathlib, sys
import datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder.runtime import core as rt

SPEC_A = json.loads((ROOT / "tests/fixtures/spec_a.json").read_text())
SPEC_B = json.loads((ROOT / "tests/fixtures/spec_b.json").read_text())
CASES = {c["id"]: c for c in json.loads((ROOT / "public-test-set.json").read_text())}


# In a real call every tool call follows a new customer message. Unit tests call tools back to back, so each call
# advances the platform's turn counter (unless the test drives the turns itself with _manual_turns).
def _turned(fn):
    def w(v, params, spec):
        if not v.get("_manual_turns"):
            v["negotiate.total_turns"] = int(v.get("negotiate.total_turns", 0)) + 1
        return fn(v, params, spec)
    return w


for _name in ("tool_check_offer", "tool_customer_refused", "tool_vague_date", "tool_set_result"):
    setattr(rt, _name, _turned(getattr(rt, _name)))


def start(case_id, spec):
    c = CASES[case_id]
    prods = [dict(p, product_id="p%d" % (i + 1)) for i, p in enumerate(c["products"])]
    v = {"now": c["variables"]["now"], "acct_json": json.dumps({"ok": True, "products": prods})}
    rt.prepare(v, spec)
    return v


def record_all(v, spec, fail_first=0):
    """Simulate the engine's record loop: next_record -> api -> after_record."""
    if "flow" not in v:
        rt.advance(v, spec)
    calls, notes, s, f = [], [], 0, 0
    while v["flow"] != "farewell":
        if v["flow"] == "record":
            calls.append((v["rec_pid"], v["rec_amount"], v["rec_date"]))
            if fail_first > 0:
                fail_first -= 1
                f += 1
                v["record_promise_failure_count"] = f
            else:
                s += 1
                v["record_promise_success_count"] = s
                v["record_promise_response.data.ok"] = "true"
            rt.after_record(v, spec)
        else:
            notes.append((v["note_pid"], v["note_reason"]))
            rt.after_note(v, spec)
        assert len(calls) + len(notes) < 12
    assert v["outcome"] and "farewell_text" in v
    return calls, notes


def promises(v):
    st = rt.load_state(v)
    return sorted((p["id"], p["amount"], p["date"]) for p in st["products"] if p["promise_ok"])


# ------------------------------------------------------------ calendar

def test_deadlines_brief_a():
    v = start("PUB-01", SPEC_A)
    st = rt.load_state(v)
    assert st["products"][0]["deadline"] == "2026-11-02"   # early: 12th business day, 26 Oct is a holiday
    v = start("PUB-04", SPEC_A)
    assert rt.load_state(v)["products"][0]["deadline"] == "2026-10-23"   # late: 7th business day


def test_end_of_week_rule_on_a_friday():
    fri = dt.date(2026, 10, 16)
    assert rt.resolve_date_expr("last_business_day_this_week_else_next", fri, fri, SPEC_A) == dt.date(2026, 10, 23)
    wed = dt.date(2026, 10, 14)
    assert rt.resolve_date_expr("last_business_day_this_week_else_next", wed, wed, SPEC_A) == dt.date(2026, 10, 16)


def test_calendar_block_marks_holiday():
    blk = rt.calendar_block(rt.parse_now("2026-10-14T11:00"), SPEC_A)
    assert "2026-10-26 Monday 26 October" in blk and "bank holiday" in blk
    assert "(tomorrow)" in blk and "{" + "{" not in blk


# ------------------------------------------------------------ public cases

def test_pub01_full_today():
    v = start("PUB-01", SPEC_A)
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-14"}, SPEC_A)
    assert v["neg_done"] == "yes" and "Do you confirm" in v["readback_text"]
    calls, notes = record_all(v, SPEC_A)
    assert promises(v) == [("p1", 420.0, "2026-10-14")] and notes == []
    assert rt.final_outcome(v, SPEC_A)[0] == "promise"
    assert "text message" in v["wrap_text"]


def test_pub04_after_deadline_then_accepts_deadline():
    v = start("PUB-04", SPEC_A)
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-11-06"}, SPEC_A)
    assert v["neg_done"] == "no" and "Friday 23 October 2026" in v["neg_say"]
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-23"}, SPEC_A)
    record_all(v, SPEC_A)
    assert promises(v) == [("p1", 1250.0, "2026-10-23")]


def test_pub06_full_too_much_then_floor_friday():
    v = start("PUB-06", SPEC_A)
    rt.tool_customer_refused(v, {"product_id": "p1", "reason": "full_too_much"}, SPEC_A)
    assert "637.50" in v["neg_say"] and "rest stays overdue" in v["neg_say"]
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-16", "amount": 637.5}, SPEC_A)
    record_all(v, SPEC_A)
    assert promises(v) == [("p1", 637.5, "2026-10-16")]


def test_pub07_vague_then_holiday_shift():
    v = start("PUB-07", SPEC_A)
    rt.tool_customer_refused(v, {"product_id": "p1"}, SPEC_A)          # cannot pay today -> attempt 1
    rt.tool_vague_date(v, {"product_id": "p1"}, SPEC_A)
    assert "exact day" in v["neg_say"]
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-26"}, SPEC_A)
    assert "Tuesday 27 October" in v["neg_say"] and v["neg_done"] == "no"
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-27"}, SPEC_A)
    record_all(v, SPEC_A)
    assert promises(v) == [("p1", 540.0, "2026-10-27")]


def test_pub10_retry_once_after_failure():
    v = start("PUB-10", SPEC_A)
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-16"}, SPEC_A)
    calls, _ = record_all(v, SPEC_A, fail_first=1)
    assert len(calls) == 2 and promises(v) == [("p1", 365.0, "2026-10-16")]
    assert rt.final_outcome(v, SPEC_A)[0] == "promise"


def test_service_error_after_two_failures():
    v = start("PUB-10", SPEC_A)
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-16"}, SPEC_A)
    calls, _ = record_all(v, SPEC_A, fail_first=5)
    assert len(calls) == 2 and promises(v) == []
    assert rt.final_outcome(v, SPEC_A) == ("no_promise", "service_error")
    assert "could not record" in v["wrap_text"] and "recorded:" not in v["wrap_text"]


def test_late_band_two_attempts_then_refused():
    v = start("PUB-05", SPEC_A)          # late band, 21 days
    rt.tool_customer_refused(v, {"product_id": "p1"}, SPEC_A)
    assert "Friday 16 October" in v["neg_say"] and "780" in v["neg_say"]
    rt.tool_customer_refused(v, {"product_id": "p1"}, SPEC_A)
    assert "260" in v["neg_say"] and "Friday 23 October" in v["neg_say"]
    rt.tool_customer_refused(v, {"product_id": "p1"}, SPEC_A)
    assert v["neg_done"] == "yes"
    record_all(v, SPEC_A)
    assert rt.final_outcome(v, SPEC_A) == ("no_promise", "refused")


def test_pub08_paid_and_pub05_hardship():
    v = start("PUB-08", SPEC_A)
    rt.tool_set_result(v, {"product_id": "p1", "result": "paid"}, SPEC_A)
    calls, notes = record_all(v, SPEC_A)
    assert calls == [] and notes == [] and rt.final_outcome(v, SPEC_A) == ("paid", "")
    v = start("PUB-05", SPEC_A)
    rt.tool_set_result(v, {"product_id": "p1", "result": "hardship"}, SPEC_A)
    record_all(v, SPEC_A)
    assert rt.final_outcome(v, SPEC_A) == ("no_promise", "hardship")


def test_transfer_outcome():
    v = start("PUB-09", SPEC_A)
    st = rt.load_state(v); st["transfer"] = "restructuring"; rt.save_state(v, st)
    assert rt.final_outcome(v, SPEC_A) == ("transferred", "restructuring")


def test_below_floor_offered_once_then_refusal():
    v = start("PUB-06", SPEC_A)
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-16", "amount": 100}, SPEC_A)
    assert "below the minimum" in v["neg_say"]
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-16", "amount": 100}, SPEC_A)
    assert rt.load_state(v)["products"][0]["attempts_used"] == 1


def test_pub11_three_products_one_date():
    v = start("PUB-11", SPEC_B)
    rt.tool_customer_refused(v, {"product_id": "all"}, SPEC_B)
    rt.tool_check_offer(v, {"product_id": "all", "date": "2026-10-16"}, SPEC_B)
    assert v["neg_done"] == "yes"
    calls, notes = record_all(v, SPEC_B)
    assert promises(v) == [("p1", 220.0, "2026-10-16"), ("p2", 640.0, "2026-10-16"), ("p3", 75.0, "2026-10-16")]
    assert notes == [] and rt.final_outcome(v, SPEC_B) == ("promise", "")


def test_pub12_one_promise_two_refused():
    v = start("PUB-12", SPEC_B)
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-15"}, SPEC_B)
    assert "next product" in v["neg_say"].lower() or "personal loan" in v["neg_say"]
    rt.tool_customer_refused(v, {"product_id": "p2"}, SPEC_B)   # attempt 1
    rt.tool_customer_refused(v, {"product_id": "p2"}, SPEC_B)   # attempt 2 (late)
    rt.tool_customer_refused(v, {"product_id": "p2"}, SPEC_B)   # used up
    rt.tool_customer_refused(v, {"product_id": "p3"}, SPEC_B)   # attempt 1 (early)
    rt.tool_customer_refused(v, {"product_id": "p3"}, SPEC_B)   # used up
    assert v["neg_done"] == "yes"
    calls, notes = record_all(v, SPEC_B)
    assert promises(v) == [("p1", 510.0, "2026-10-15")]
    assert notes == [("p2", "refused"), ("p3", "refused")]
    assert rt.final_outcome(v, SPEC_B) == ("promise", "")


def test_pub13_common_date_clamped():
    v = start("PUB-13", SPEC_B)
    rt.tool_customer_refused(v, {"product_id": "all"}, SPEC_B)
    rt.tool_check_offer(v, {"product_id": "all", "date": "2026-10-30"}, SPEC_B)
    assert "latest possible date" in v["readback_text"]
    record_all(v, SPEC_B)
    assert promises(v) == [("p1", 300.0, "2026-10-30"), ("p2", 900.0, "2026-10-23")]


def test_no_promise_reason_first_non_paid():
    v = start("PUB-11", SPEC_B)
    for pid, r in (("p1", "paid"), ("p2", "dispute")):
        rt.tool_set_result(v, {"product_id": pid, "result": r}, SPEC_B)
    # p3 got its first offer automatically when p2 was decided (next product starts at attempt 1)
    rt.tool_customer_refused(v, {"product_id": "p3"}, SPEC_B)      # no attempts left -> refused
    calls, notes = record_all(v, SPEC_B)
    assert notes == [("p1", "paid"), ("p2", "dispute"), ("p3", "refused")]
    assert rt.final_outcome(v, SPEC_B) == ("no_promise", "dispute")


def test_transfer_means_no_notes():
    v = start("PUB-11", SPEC_B)
    st = rt.load_state(v); st["transfer"] = "restructuring"; st["products"][0]["result"] = "refused"; rt.save_state(v, st)
    _, notes = record_all(v, SPEC_B)
    assert notes == []


# ------------------------------------------------------------ callback + identity

def test_callback_window():
    v = {"now": "2026-10-14T11:00"}
    rt.tool_check_callback(v, {"date": "2026-10-15", "time": "11:00"}, SPEC_A)
    assert v["cb_ok"] == "yes" and v["cb_time"] == "11:00"
    v = {"now": "2026-10-14T11:00"}
    rt.tool_check_callback(v, {"date": "2026-10-15", "time": "20:00"}, SPEC_A)
    assert v.get("cb_ok") != "yes"
    rt.tool_check_callback(v, {"date": "2026-10-19", "time": "11:00"}, SPEC_A)
    assert v["cb_ok"] == "no"
    v = {"now": "2026-10-14T11:00"}
    rt.tool_check_callback(v, {"date": "2026-10-14", "time": "10:30"}, SPEC_A)   # earlier than now
    assert v.get("cb_ok") != "yes"


def test_identity_hooks():
    v = {}
    cfg = {"body": {"field": "date_of_birth", "answer": "14/03/1988"}}
    rt.id_pre_hook(v, cfg, SPEC_A)
    assert cfg["body"]["answer"] == "1988-03-14"
    cfg = {"body": {"field": "date_of_birth", "answer": "14 March 1988"}}
    rt.id_pre_hook(v, cfg, SPEC_A)
    assert cfg["body"]["answer"] == "1988-03-14"
    res = {"data": {"ok": True, "verified": True, "wrong_answers_left": 3, "all_verified": False}}
    rt.id_post_hook(v, res, SPEC_A)
    assert "last four digits" in res["data"]["what_to_do_next"]
    cfg = {"body": {"field": "phone_last4", "answer": "44 10"}}
    rt.id_pre_hook(v, cfg, SPEC_A)
    assert cfg["body"]["answer"] == "4410"
    res = {"data": {"ok": True, "verified": False, "wrong_answers_left": 2, "all_verified": False}}
    rt.id_post_hook(v, res, SPEC_A)
    assert "wrong" in res["data"]["what_to_do_next"]
    res = {"data": {"ok": True, "verified": True, "wrong_answers_left": 2, "all_verified": True}}
    rt.id_post_hook(v, res, SPEC_A)
    assert v["id_state"] == "verified"


def test_runtime_source_has_no_double_braces():
    src = (ROOT / "builder/runtime/core.py").read_text()
    assert "{" + "{" not in src and "}" + "}" not in src


def test_transfer_queue_read_from_model_args_without_hooks():
    v = start("PUB-09", SPEC_A)
    v["transfer_to_agent_success_count"] = 1
    v["transfer_to_agent_args.transfer_to_agent__queue"] = "restructuring"
    assert rt.final_outcome(v, SPEC_A) == ("transferred", "restructuring")


def test_set_result_refused_with_attempts_left_becomes_an_attempt():
    v = start("PUB-12", SPEC_B)
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-15"}, SPEC_B)
    rt.tool_set_result(v, {"product_id": "p2", "result": "refused"}, SPEC_B)
    st = rt.load_state(v)
    # p2 got attempt 1 automatically after p1; the refusal becomes attempt 2 (late band: floor on the deadline)
    assert st["products"][1]["result"] == "" and st["products"][1]["attempts_used"] == 2
    assert "Friday 23 October" in v["neg_say"] and "440" in v["neg_say"]


# ------------------------------------------------------------ other calendar days (the final runs on several days)

def _start_on(now, days_late, spec=SPEC_A, ptype="personal_loan"):
    p = {"product_id": "p1", "type": ptype, "past_due": 500.0, "days_late": days_late, "instalment_amount": 100.0, "minimum_payment": 100.0, "card_last4": "1111"}
    v = {"now": now, "acct_json": json.dumps({"ok": True, "products": [p]})}
    rt.prepare(v, spec)
    return v


def test_friday_now_attempt_goes_to_next_week_and_deadline_skips_holiday():
    v = _start_on("2026-10-16T15:00", 20)          # Friday; late band: 7 business days, 26 Oct holiday in between
    st = rt.load_state(v)
    assert st["products"][0]["deadline"] == "2026-10-28"
    rt.tool_customer_refused(v, {"product_id": "p1"}, SPEC_A)
    assert "Friday 23 October" in v["neg_say"]       # today is the last business day of this week -> next week's


def test_weekend_now_and_today_promise():
    v = _start_on("2026-10-17T11:00", 3)           # Saturday
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-17"}, SPEC_A)
    assert v["neg_done"] == "no" and "Monday 19 October" in v["neg_say"]


def test_holiday_season_deadline():
    v = _start_on("2026-12-22T10:00", 10)          # late band over 24/25 Dec (25 Dec + 1 Jan are holidays in spec A)
    st = rt.load_state(v)
    # 23,24,28,29,30,31 Dec, 4 Jan = 7 business days (25 Dec and 1 Jan are holidays)
    assert st["products"][0]["deadline"] == "2027-01-04"


def test_callback_on_friday_evening_goes_to_monday():
    v = {"now": "2026-10-16T18:30"}
    rt.tool_check_callback(v, {"date": "2026-10-19", "time": "10:00"}, SPEC_A)
    assert v["cb_ok"] == "yes"
    v = {"now": "2026-10-16T18:30"}
    rt.tool_check_callback(v, {"date": "2026-10-17", "time": "11:00"}, SPEC_A)   # Saturday: not allowed
    assert v.get("cb_ok") != "yes"


def test_calendar_block_on_month_end():
    blk = rt.calendar_block(rt.parse_now("2026-10-30T09:00"), SPEC_A)
    assert "2026-11-02 Monday 2 November" in blk and "next week" in blk


def test_parallel_tool_calls_on_different_products_do_not_clobber():
    """Two tools run on the same snapshot of vars; the platform merges the variables they write."""
    v = start("PUB-12", SPEC_B)
    class Tracking(dict):
        def __init__(self, d):
            super().__init__(d); self.assigned = {}
        def __setitem__(self, k, val):
            super().__setitem__(k, val); self.assigned[k] = val
    a, b = Tracking(v), Tracking(v)
    rt.tool_check_offer(a, {"product_id": "p1", "date": "2026-10-15"}, SPEC_B)
    rt.tool_customer_refused(b, {"product_id": "p2"}, SPEC_B)
    merged = dict(v)
    for snap in (a, b):                      # the platform writes back every key a snippet ASSIGNED, in finish order
        merged.update(snap.assigned)
    st = rt.load_state(merged)
    assert st["products"][0]["result"] == "agreed"
    assert st["products"][1]["attempts_used"] >= 1


def test_offer_generated_this_turn_cannot_be_refused_unheard():
    v = start("PUB-12", SPEC_B)
    v["_manual_turns"] = True
    v["negotiate.total_turns"] = 1
    rt.tool_check_offer(v, {"product_id": "p1", "date": "2026-10-15"}, SPEC_B)   # p2 gets attempt 1 automatically
    rt.tool_customer_refused(v, {"product_id": "p2"}, SPEC_B)                    # same customer turn: not heard yet
    st = rt.load_state(v)
    assert st["products"][1]["attempts_used"] == 1 and "not heard" in v["neg_say"] and "1320" in v["neg_say"]
    v["negotiate.total_turns"] = 2                                               # next customer message: a real refusal
    rt.tool_customer_refused(v, {"product_id": "p2"}, SPEC_B)
    assert rt.load_state(v)["products"][1]["attempts_used"] == 2
