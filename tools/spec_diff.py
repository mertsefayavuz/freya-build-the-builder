"""Compare an extracted spec with a golden spec on the fields that decide the ledger.

    python tools/spec_diff.py out/brief-a.spec.json tests/fixtures/spec_a.json
"""
import json, sys


def norm_band(b):
    return (b.get("min_days"), b.get("max_days"), b.get("deadline", {}).get("kind"), b.get("deadline", {}).get("n"),
            tuple((a.get("date"), a.get("amount")) for a in b.get("attempts", [])))


CHECKS = [
    ("holidays", lambda s: sorted(s.get("calendar", {}).get("holidays", []))),
    ("workdays", lambda s: sorted(s.get("calendar", {}).get("workdays", [0, 1, 2, 3, 4]))),
    ("bands", lambda s: sorted(norm_band(b) for b in s.get("bands", []))),
    ("floor fields", lambda s: sorted((k, v.get("floor_field")) for k, v in s.get("products", {}).items())),
    ("product fields", lambda s: sorted(s.get("product_fields", {}).items())),
    ("identity fields", lambda s: [q.get("field") for q in s.get("identity", {}).get("questions", [])]),
    ("identity formats", lambda s: [q.get("format") for q in s.get("identity", {}).get("questions", [])]),
    ("callback", lambda s: {k: s.get("callback", {}).get(k) for k in ("days", "start", "end", "after_now", "max_invalid")}),
    ("notes.when", lambda s: s.get("notes", {}).get("when")),
    ("clamp", lambda s: s.get("multi", {}).get("clamp_common_date_to_deadline")),
    ("retries", lambda s: s.get("confirmation", {}).get("retries")),
    ("vague_asks", lambda s: s.get("promise", {}).get("vague_asks")),
    ("closing.map", lambda s: sorted((k, tuple(v)) for k, v in s.get("closing", {}).get("map", {}).items())),
    ("after_promise", lambda s: s.get("texts", {}).get("after_promise", "").strip().rstrip(".").lower()),
]


def diff(got, gold):
    res = []
    for name, f in CHECKS:
        try:
            a, b = f(got), f(gold)
        except Exception as e:
            a, b = "error %r" % e, f(gold)
        res.append((name, a == b, a, b))
    return res


if __name__ == "__main__":
    got, gold = json.load(open(sys.argv[1], encoding="utf-8")), json.load(open(sys.argv[2], encoding="utf-8"))
    res = diff(got, gold)
    for name, ok, a, b in res:
        print(("OK  " if ok else "DIFF"), name)
        if not ok:
            print("      got :", a)
            print("      want:", b)
    print("\n%d / %d fields match" % (sum(r[1] for r in res), len(res)))
