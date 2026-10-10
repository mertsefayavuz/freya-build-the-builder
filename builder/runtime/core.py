# Runtime logic that runs INSIDE the Freya workflow (code nodes, code_exec tools, api hooks).
# Rules for this file: standard library only (json, re, datetime), no double braces anywhere,
# every entry point catches its own errors. The spec dict comes from the builder, the call state
# lives in vars['st'] as a JSON string.
import json
import re
import datetime as _dt

WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
          'October', 'November', 'December']


# ---------------------------------------------------------------- calendar

def parse_now(s):
    s = str(s or '').strip().replace(' ', 'T')
    try:
        return _dt.datetime.fromisoformat(s[:16])
    except Exception:
        try:
            return _dt.datetime.fromisoformat(s[:10] + 'T00:00')
        except Exception:
            return _dt.datetime(2026, 1, 1, 9, 0)


def to_date(s):
    try:
        return _dt.date.fromisoformat(str(s).strip()[:10])
    except Exception:
        return None


def is_business_day(d, spec):
    cal = spec.get('calendar', {})
    workdays = cal.get('workdays', [0, 1, 2, 3, 4])
    return d.weekday() in workdays and d.isoformat() not in cal.get('holidays', [])


def next_business_day(d, spec, include=False):
    x = d if include else d + _dt.timedelta(days=1)
    for _ in range(400):
        if is_business_day(x, spec):
            return x
        x += _dt.timedelta(days=1)
    return x


def add_business_days(d, n, spec):
    x = d
    for _ in range(n):
        x = next_business_day(x, spec)
    return x


def last_business_day_of_week(d, spec):
    monday = d - _dt.timedelta(days=d.weekday())
    best = None
    for i in range(7):
        x = monday + _dt.timedelta(days=i)
        if is_business_day(x, spec):
            best = x
    return best


def resolve_date_expr(expr, today, deadline, spec):
    """Date expressions a brief can use for proposals and deadlines."""
    e = str(expr or '').strip()
    if e == 'today':
        return today if is_business_day(today, spec) else next_business_day(today, spec)
    if e == 'deadline':
        return deadline
    if e in ('end_of_week', 'last_business_day_this_week_else_next'):
        x = last_business_day_of_week(today, spec)
        if x is None or x <= today:
            x = last_business_day_of_week(today + _dt.timedelta(days=7 - today.weekday()), spec)
        return x
    if e == 'next_business_day':
        return next_business_day(today, spec)
    m = re.match(r'^(business_days_after_today|nth_business_day):(\d+)$', e)
    if m:
        return add_business_days(today, int(m.group(2)), spec)
    m = re.match(r'^calendar_days_after_today:(\d+)$', e)
    if m:
        return today + _dt.timedelta(days=int(m.group(1)))
    return deadline


def deadline_for(band, today, spec):
    dl = band.get('deadline', {})
    kind = dl.get('kind', 'business_days_after_today')
    n = int(dl.get('n', 0) or 0)
    if kind == 'calendar_days_after_today':
        d = today + _dt.timedelta(days=n)
        if dl.get('roll_back_to_business_day', True) and not is_business_day(d, spec):
            while not is_business_day(d, spec) and d > today:
                d -= _dt.timedelta(days=1)
        return d
    if kind == 'end_of_month':
        nxt = (today.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
        d = nxt - _dt.timedelta(days=1)
        while not is_business_day(d, spec):
            d -= _dt.timedelta(days=1)
        return d
    return add_business_days(today, n, spec)


def say_date(d):
    if d is None:
        return ''
    return '%s %d %s %d' % (WEEKDAYS[d.weekday()], d.day, MONTHS[d.month - 1], d.year)


def say_amount(x, spec=None):
    try:
        v = round(float(x), 2)
    except Exception:
        return str(x)
    s = ('%d' % v) if abs(v - int(v)) < 0.005 else ('%.2f' % v)
    cur = (spec or {}).get('currency', '') if spec else ''
    return (s + ' ' + cur).strip()


def calendar_block(now, spec, days=40):
    today = now.date()
    lines = ['Today is %s (%s), time %s.' % (say_date(today), today.isoformat(), now.strftime('%H:%M')),
             'Calendar (date, weekday, status). "this <weekday>" = in the current week, "next <weekday>" = in the following calendar week:']
    monday = today - _dt.timedelta(days=today.weekday())
    for i in range(days):
        d = monday + _dt.timedelta(days=i)
        if d < today:
            continue
        if d.isoformat() in spec.get('calendar', {}).get('holidays', []):
            st = 'bank holiday, not a business day'
        elif not is_business_day(d, spec):
            st = 'weekend, not a business day'
        else:
            st = 'business day'
        week = 'this week' if d - monday < _dt.timedelta(days=7) else ('next week' if d - monday < _dt.timedelta(days=14) else 'later')
        tag = ' (today)' if d == today else (' (tomorrow)' if d == today + _dt.timedelta(days=1) else '')
        lines.append('- %s %s %d %s%s: %s, %s' % (d.isoformat(), WEEKDAYS[d.weekday()], d.day, MONTHS[d.month - 1], tag, week, st))
    return '\n'.join(lines)


# ---------------------------------------------------------------- state

def _json(raw, default):
    if isinstance(raw, (dict, list)):
        return raw
    try:
        v = json.loads(raw) if raw else default
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def load_state(vars):
    # Each product lives in its own variable (st_<id>): the phone model sometimes calls several tools in one turn,
    # and those run on the same snapshot. Separate variables mean a tool for p2 cannot overwrite what a tool for p1 wrote.
    st = _json(vars.get('st'), {})
    order = st.get('order')
    if order:
        prods = []
        for pid in order:
            p = _json(vars.get('st_' + pid), {})
            if p:
                prods.append(p)
        st['products'] = prods
    return st


def save_state(vars, st):
    prods = st.get('products', [])
    g = dict(st)
    g.pop('products', None)
    g['order'] = [p['id'] for p in prods]
    # Write only what changed: every assigned variable is written back, and an unchanged copy could be stale.
    for p in prods:
        val = json.dumps(p)
        if vars.get('st_' + p['id']) != val:
            vars['st_' + p['id']] = val
    val = json.dumps(g)
    if vars.get('st') != val:
        vars['st'] = val


def today_of(vars):
    return parse_now(vars.get('now')).date()


def band_for(days_late, spec):
    bands = spec.get('bands', [])
    for b in bands:
        if int(b.get('min_days', 0)) <= days_late <= int(b.get('max_days', 10 ** 6)):
            return b
    return bands[-1] if bands else {'name': 'default', 'deadline': {'kind': 'business_days_after_today', 'n': 5}, 'attempts': []}


def product_label(p, spec):
    cfg = spec.get('products', {}).get(p.get('type', ''), {})
    label = cfg.get('label') or str(p.get('type', 'product')).replace('_', ' ')
    for k, v in p.items():
        label = label.replace('<' + str(k) + '>', str(v))
    return re.sub(r'<[^>]+>', '', label).strip()


def read_products(vars):
    """Products from get_account, defensively: mapped list, JSON string, raw account json, or flattened keys."""
    for key in ('acct_json', 'products', 'get_account_products'):
        v = vars.get(key)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                v = None
        if isinstance(v, dict):
            v = v.get('products')
        if isinstance(v, list) and v:
            return [x for x in v if isinstance(x, dict)]
    found = {}
    for k, v in vars.items():
        m = re.match(r'^(?:load_account_response|get_account_response)\.data\.products\.(\d+)\.(\w+)$', str(k))
        if m:
            found.setdefault(int(m.group(1)), {})[m.group(2)] = v
    return [found[i] for i in sorted(found)]


def num(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def prepare(vars, spec):
    st = load_state(vars)
    today = today_of(vars)
    fields = spec.get('product_fields', {})
    f_id, f_type = fields.get('id', 'product_id'), fields.get('type', 'type')
    f_amt, f_days = fields.get('amount', 'past_due'), fields.get('days_late', 'days_late')
    prods = []
    for i, raw in enumerate(read_products(vars)):
        ptype = str(raw.get(f_type, ''))
        cfg = spec.get('products', {}).get(ptype, {})
        days = int(num(raw.get(f_days), 0))
        band = band_for(days, spec)
        deadline = deadline_for(band, today, spec)
        full = round(num(raw.get(f_amt)), 2)
        floor_field = cfg.get('floor_field', '')
        floor = round(num(raw.get(floor_field), full), 2) if floor_field else full
        p = {
            'id': str(raw.get(f_id) or ('p%d' % (i + 1))), 'type': ptype, 'raw': raw,
            'label': product_label(dict(raw, type=ptype), spec), 'full': full, 'floor': floor,
            'days_late': days, 'band': band.get('name', ''), 'deadline': deadline.isoformat(),
            'attempts_max': len(band.get('attempts', [])), 'attempts_used': 0,
            'cur_amount': full, 'floor_offered': False, 'deadline_offered': False, 'vague_asks': 0,
            'below_floor_count': 0, 'result': '', 'date': '', 'amount': 0.0, 'clamped': False,
            'record_tries': 0, 'promise_ok': False, 'note_done': False,
        }
        prods.append(p)
    st['products'] = prods
    st['today'] = today.isoformat()
    st.setdefault('today_refused_all', False)
    st.setdefault('transfer', '')
    vars['n_products'] = str(len(prods))
    vars['debt_statement'] = debt_statement(st, spec)
    vars['first_ask'] = first_ask(st, spec)
    vars['neg_say'] = vars['first_ask']
    vars['neg_done'] = 'no'
    vars['neg_state'] = render_state(st, spec)
    save_state(vars, st)
    return st


def debt_statement(st, spec):
    tpl = spec.get('texts', {}).get('product_sentence', '<label>: <past_due> past due, <days_late> days late.')
    out = []
    for p in st.get('products', []):
        s = tpl.replace('<label>', p['label']).replace('<past_due>', say_amount(p['full'], spec)) \
               .replace('<days_late>', str(p['days_late'])).replace('<floor>', say_amount(p['floor'], spec))
        out.append(s)
    return ' '.join(out)


def first_ask(st, spec):
    t = spec.get('texts', {})
    if len(st.get('products', [])) > 1:
        return t.get('first_ask_multi', 'Can you pay all of them today?')
    return t.get('first_ask', 'Can you make this payment today?')


def find(st, pid):
    for p in st.get('products', []):
        if p['id'] == str(pid).strip():
            return p
    open_ = [p for p in st.get('products', []) if not p['result']]
    return open_[0] if open_ else None


def current(st):
    for p in st.get('products', []):
        if not p['result']:
            return p
    return None


def offer_text(p, d, amount, spec):
    return '%s for %s on %s' % (say_amount(amount, spec), p['label'], say_date(to_date(d) if isinstance(d, str) else d))


def render_state(st, spec):
    lines = ['Products (handle them in this order):']
    for p in st.get('products', []):
        status = p['result'] or 'open'
        if p['result'] == 'agreed':
            status = 'agreed: %s on %s' % (say_amount(p['amount'], spec), p['date'])
        lines.append('- %s = %s: past due %s, %d days late, minimum %s, latest allowed date %s. Status: %s.' % (
            p['id'], p['label'], say_amount(p['full'], spec), p['days_late'], say_amount(p['floor'], spec),
            say_date(to_date(p['deadline'])), status))
    c = current(st)
    lines.append('Current product: %s' % (c['id'] + ' = ' + c['label'] if c else 'none, all decided'))
    made = [o for p in st.get('products', []) for o in p.get('offers', [])]
    if made:
        lines.append('Offers you already made (NEVER say them again; wait for the answer): ' + '; '.join(made))
    return '\n'.join(lines)


def _after_decision(st, spec):
    """Text that tells the model what comes next once a product is decided."""
    c = current(st)
    if c is None:
        return ' All products are decided. Say nothing more about offers.'
    multi_start = spec.get('multi', {}).get('next_product_starts_at_attempt', True)
    if (st.get('today_refused_all') or multi_start) and c['attempts_used'] == 0 and len(st.get('products', [])) > 1:
        # Do not ask about today again for each product: go straight to its first persuasion offer.
        return ' Next product: ' + _attempt(st, c, spec)
    return ' Now turn to the next product, %s (%s past due). Ask about it.' % (c['label'], say_amount(c['full'], spec))


def _attempt(st, p, spec):
    band = band_for(p['days_late'], spec)
    atts = band.get('attempts', [])
    p['attempts_used'] += 1
    i = p['attempts_used']
    if i <= len(atts):
        a = atts[i - 1]
        today = to_date(st['today'])
        d = resolve_date_expr(a.get('date', 'deadline'), today, to_date(p['deadline']), spec)
        amt_kind = a.get('amount', 'current')
        amount = p['floor'] if amt_kind == 'floor' else (p['full'] if amt_kind == 'full' else p['cur_amount'])
        p['last_offer'] = {'date': d.isoformat(), 'amount': amount}
        p.setdefault('offers', []).append(offer_text(p, d, amount, spec))
        extra = ''
        if amount < p['full'] - 0.004:
            extra = ' ' + spec.get('texts', {}).get('rest_overdue', 'Say that the rest stays overdue.')
        return 'Propose exactly this, once: %s.%s Do not repeat an offer the customer already refused.' % (offer_text(p, d, amount, spec), extra)
    p['result'] = 'refused'
    return 'No offers are left for %s. Do not insist; acknowledge politely.' % p['label'] + _after_decision(st, spec)


def _check_one(st, p, d, amount, for_all, spec):
    today = to_date(st['today'])
    deadline = to_date(p['deadline'])
    amt = p['cur_amount'] if amount is None else amount
    if amt > p['full'] + 0.004:
        amt = p['full']
    if amt < p['floor'] - 0.004:
        p['below_floor_count'] += 1
        if not p['floor_offered'] and p['below_floor_count'] == 1:
            p['floor_offered'] = True
            p['cur_amount'] = p['floor']
            return False, '%s cannot be recorded for %s: it is below the minimum. Offer the minimum %s once and say that the rest stays overdue.' % (
                say_amount(amt, spec), p['label'], say_amount(p['floor'], spec))
        return False, 'The customer does not accept the minimum. ' + _attempt(st, p, spec)
    if d is None:
        return False, 'The amount %s is fine. Ask on which exact day they will pay it (from today until %s).' % (say_amount(amt, spec), say_date(deadline))
    if d < today:
        return False, 'That date is in the past. Ask for a day from today until %s.' % say_date(deadline)
    clamped = False
    if d > deadline:
        if for_all and spec.get('multi', {}).get('clamp_common_date_to_deadline', False):
            d = deadline
            clamped = True
        else:
            p['deadline_offered'] = True
            return False, 'That is after the latest date that can be recorded for %s. Say the latest date you can record is %s and offer it. If the customer refuses it, call customer_refused.' % (p['label'], say_date(deadline))
    if not is_business_day(d, spec):
        nd = next_business_day(d, spec)
        if nd > deadline:
            nd = deadline
        return False, '%s is not a business day. Do not accept it. Offer %s instead.' % (say_date(d), say_date(nd))
    p['result'] = 'agreed'
    p['date'] = d.isoformat()
    p['amount'] = round(amt, 2)
    p['clamped'] = clamped
    msg = 'Valid: %s.' % offer_text(p, d, amt, spec)
    if clamped:
        msg += ' Tell the customer that %s will be recorded on its latest possible date, %s.' % (p['label'], say_date(d))
    return True, msg


def _parse_amount(x):
    if x is None or x == '':
        return None
    if isinstance(x, (int, float)):
        return None if x <= 0 else float(x)
    s = re.sub(r'[^0-9.,]', '', str(x)).replace(',', '')
    try:
        v = float(s)
        return None if v <= 0 else v
    except Exception:
        return None


def _parse_date(x):
    if x is None:
        return None
    s = str(x).strip()
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    return None


def _finish(vars, st, spec, say):
    vars['neg_say'] = say
    vars['neg_done'] = 'yes' if current(st) is None else 'no'
    vars['neg_state'] = render_state(st, spec)
    vars['readback_text'] = readback_text(st, spec)
    vars['n_agreed'] = str(len([p for p in st.get('products', []) if p['result'] == 'agreed']))
    save_state(vars, st)
    if vars['neg_done'] == 'yes':
        advance(vars, spec)
    return say


def tool_check_offer(vars, params, spec):
    st = load_state(vars)
    pid = str(params.get('product_id') or '').strip()
    d = _parse_date(params.get('date'))
    amount = _parse_amount(params.get('amount'))
    targets = [p for p in st.get('products', []) if not p['result'] or p['result'] == 'agreed'] if pid.lower() == 'all' else [find(st, pid)]
    targets = [p for p in targets if p is not None]
    if not targets:
        return _finish(vars, st, spec, 'There is no open product.')
    msgs = []
    for p in targets:
        ok, m = _check_one(st, p, d, amount if len(targets) == 1 else None, pid.lower() == 'all' and len(targets) > 1, spec)
        msgs.append(m)
    say = ' '.join(msgs)
    if all(p['result'] in ('agreed', 'refused') for p in targets) or current(st) is not None:
        say += _after_decision(st, spec) if all(p['result'] for p in targets) else ''
    return _finish(vars, st, spec, say)


def tool_customer_refused(vars, params, spec):
    st = load_state(vars)
    pid = str(params.get('product_id') or '').strip()
    reason = str(params.get('reason') or 'flat').strip()
    if pid.lower() == 'all':
        st['today_refused_all'] = True
        p = current(st)
    else:
        p = find(st, pid)
    if p is None:
        return _finish(vars, st, spec, 'All products are decided.')
    if reason == 'full_too_much' and not p['floor_offered'] and p['floor'] < p['full'] - 0.004:
        # The floor is offered once. It goes with the next persuasion date, so it is not offered twice.
        p['floor_offered'] = True
        p['cur_amount'] = p['floor']
        if p['attempts_used'] < p['attempts_max']:
            return _finish(vars, st, spec, _attempt(st, p, spec))
        last = p.get('last_offer', {}).get('date') or p['deadline']
        say = 'Propose exactly this, once: %s. %s' % (offer_text(p, last, p['floor'], spec),
                                                     spec.get('texts', {}).get('rest_overdue', 'Say that the rest stays overdue.'))
        return _finish(vars, st, spec, say)
    return _finish(vars, st, spec, _attempt(st, p, spec))


def tool_vague_date(vars, params, spec):
    st = load_state(vars)
    p = find(st, params.get('product_id') or '')
    if p is None:
        return _finish(vars, st, spec, 'All products are decided.')
    p['vague_asks'] += 1
    if p['vague_asks'] <= int(spec.get('promise', {}).get('vague_asks', 1)):
        say = 'Ask once for the exact day (latest %s).' % say_date(to_date(p['deadline']))
    else:
        d = to_date(p['deadline'])
        say = 'Propose %s.' % offer_text(p, d, p['cur_amount'], spec)
    return _finish(vars, st, spec, say)


def tool_set_result(vars, params, spec):
    st = load_state(vars)
    pid = str(params.get('product_id') or '').strip()
    result = str(params.get('result') or params.get('reason') or '').strip().lower()
    allowed = spec.get('product_results', ['refused', 'hardship', 'dispute', 'paid'])
    if result not in allowed:
        return _finish(vars, st, spec, 'Unknown result. Use one of: ' + ', '.join(allowed) + '.')
    if result == 'refused' and pid.lower() != 'all':
        p = find(st, pid)
        if p is not None and not p['result'] and p['attempts_used'] < p['attempts_max']:
            # A refusal with persuasion attempts left is a flat refusal: make the next attempt (the bank's rule, not the model's).
            return _finish(vars, st, spec, 'Do not close this product yet. ' + _attempt(st, p, spec))
    targets = [p for p in st.get('products', []) if p['result'] != 'agreed'] if pid.lower() == 'all' else [find(st, pid)]
    for p in targets:
        if p is not None:
            p['result'] = result
    say = 'Noted.' + _after_decision(st, spec)
    return _finish(vars, st, spec, say)


def readback_text(st, spec):
    agreed = [p for p in st.get('products', []) if p['result'] == 'agreed']
    if not agreed:
        return ''
    parts = []
    for p in agreed:
        s = '%s on %s for %s' % (say_amount(p['amount'], spec), say_date(to_date(p['date'])), p['label'])
        if p.get('clamped'):
            s += ' (its latest possible date)'
        parts.append(s)
    tpl = spec.get('texts', {}).get('readback', 'To confirm: <list>. Do you confirm?')
    return tpl.replace('<list>', '; '.join(parts))


def reopen(vars, spec):
    st = load_state(vars)
    for p in st.get('products', []):
        if p['result'] == 'agreed':
            p['result'] = ''
    return _finish(vars, st, spec, 'The customer wants to change something. Ask what they want to change, then check the new value with check_offer.')


# ---------------------------------------------------------------- recording

def next_record(vars, spec):
    """Prepare the next create_promise call. Sets rec_pending yes/no."""
    st = load_state(vars)
    for p in st.get('products', []):
        if p['result'] == 'agreed' and not p['promise_ok'] and p['record_tries'] < 1 + int(spec.get('confirmation', {}).get('retries', 1)):
            vars['rec_pending'] = 'yes'
            vars['rec_pid'] = p['id']
            vars['rec_amount'] = p['amount']
            vars['rec_date'] = p['date']
            st['rec_counts'] = [int(num(vars.get('record_promise_success_count'), 0)), int(num(vars.get('record_promise_failure_count'), 0))]
            save_state(vars, st)
            return
    vars['rec_pending'] = 'no'
    save_state(vars, st)
    next_note(vars, spec)
    vars['wrap_text'] = wrap_text(load_state(vars), spec)


def after_record(vars, spec):
    st = load_state(vars)
    pid = vars.get('rec_pid')
    p = find(st, pid)
    before = st.get('rec_counts', [0, 0])
    succ = int(num(vars.get('record_promise_success_count'), 0))
    ok_flag = str(vars.get('record_promise_response.data.ok', '')).lower() == 'true'
    ok = succ > before[0] and ok_flag
    if p is not None and p['id'] == pid:
        p['record_tries'] += 1
        if ok:
            p['promise_ok'] = True
            p['result'] = 'recorded'
        elif p['record_tries'] >= 1 + int(spec.get('confirmation', {}).get('retries', 1)):
            p['result'] = 'service_error'
    save_state(vars, st)
    advance(vars, spec)


def advance(vars, spec):
    """Decide what the engine does next: 'record' a promise, write a 'note', or say the 'farewell'.
    For the farewell it also computes outcome, reason and the closing text, so no extra code step runs."""
    next_record(vars, spec)
    if vars.get('rec_pending') == 'yes':
        vars['flow'] = 'record'
    elif vars.get('note_pending') == 'yes':
        vars['flow'] = 'note'
    else:
        vars['flow'] = 'farewell'
        final_outcome(vars, spec)
        vars['farewell_text'] = closing_text(vars, spec)


def after_note(vars, spec):
    next_note(vars, spec)
    if vars.get('note_pending') == 'yes':
        vars['flow'] = 'note'
    else:
        vars['flow'] = 'farewell'
        final_outcome(vars, spec)
        vars['farewell_text'] = closing_text(vars, spec)


def wants_notes(st, spec):
    rule = spec.get('notes', {}).get('when', 'never')
    if rule == 'never' or st.get('transfer'):
        return False
    if rule == 'multi_product_only':
        return len(st.get('products', [])) > 1
    return True


def next_note(vars, spec):
    st = load_state(vars)
    vars['note_pending'] = 'no'
    if wants_notes(st, spec):
        for p in st.get('products', []):
            if not p['promise_ok'] and not p['note_done'] and p['result'] in spec.get('notes', {}).get('reasons', ['refused', 'hardship', 'dispute', 'paid', 'service_error']):
                vars['note_pending'] = 'yes'
                vars['note_pid'] = p['id']
                vars['note_reason'] = p['result']
                p['note_done'] = True
                break
    save_state(vars, st)


def wrap_text(st, spec):
    t = spec.get('texts', {})
    ok = [p for p in st.get('products', []) if p['promise_ok']]
    bad = [p for p in st.get('products', []) if p['result'] == 'service_error']
    parts = []
    if ok:
        parts.append('Your payment promise is recorded: ' + '; '.join('%s on %s for %s' % (say_amount(p['amount'], spec), say_date(to_date(p['date'])), p['label']) for p in ok) + '.')
        if t.get('after_promise'):
            parts.append(t['after_promise'])
    if bad:
        parts.append(t.get('record_failed', 'I am sorry, I could not record the promise for') + ' ' + ', '.join(p['label'] for p in bad) + '.')
    return ' '.join(parts)


def force_close(vars, spec):
    """The conversation ended while products were still open (the model never called a tool).
    Close them with the reason the extraction model heard, else refused, then continue as usual."""
    st = load_state(vars)
    reason = str(vars.get('open_reason') or '').strip()
    if reason not in spec.get('product_results', ['refused', 'hardship', 'dispute', 'paid']):
        reason = 'refused'
    for p in st.get('products', []):
        if p['result'] in ('', 'agreed'):
            p['result'] = reason
    save_state(vars, st)
    advance(vars, spec)


def transfer_queue_from_vars(vars, spec):
    known = [k.split(':', 1)[1] for k in spec.get('closing', {}).get('map', {}) if k.startswith('transfer:')]
    for k, v in vars.items():
        if str(k).startswith('transfer_to_agent_args.') and str(v) in known:
            return str(v)
    for k, v in vars.items():
        if str(k).startswith('transfer_to_agent_args.') and 'queue' in str(k):
            return str(v)
    return str(vars.get('transfer_queue') or '')


def final_outcome(vars, spec):
    st = load_state(vars)
    if not st.get('transfer') and int(num(vars.get('transfer_to_agent_success_count'), 0)) > 0:
        st['transfer'] = transfer_queue_from_vars(vars, spec)
        save_state(vars, st)
    omap = spec.get('closing', {}).get('map', {})
    def pick(key, default):
        v = omap.get(key) or default
        return v[0], (v[1] if len(v) > 1 else '')
    if st.get('transfer'):
        o, r = pick('transfer:' + st['transfer'], ['transferred', st['transfer']])
    elif any(p['promise_ok'] for p in st.get('products', [])):
        o, r = pick('promise', ['promise', ''])
    else:
        res = [p['result'] for p in st.get('products', [])]
        if res and all(x == 'paid' for x in res):
            o, r = pick('paid', ['paid', ''])
        else:
            first = next((x for x in res if x and x != 'paid'), 'refused')
            o, r = pick(first, ['no_promise', first])
    vars['outcome'] = o
    if r:
        vars['close_reason'] = r
    return o, r


def set_exit(vars, spec, key):
    v = spec.get('closing', {}).get('map', {}).get(key) or ['no_promise', key]
    vars['outcome'] = v[0]
    if len(v) > 1 and v[1]:
        vars['close_reason'] = v[1]


# ---------------------------------------------------------------- callback

def tool_check_callback(vars, params, spec):
    cb = spec.get('callback', {})
    now = parse_now(vars.get('now'))
    today = now.date()
    d = _parse_date(params.get('date'))
    t = str(params.get('time') or '').strip()
    m = re.match(r'^(\d{1,2})[:.]?(\d{2})?', t)
    allowed_days = []
    for e in cb.get('days', ['today', 'next_business_day']):
        if e == 'today':
            if is_business_day(today, spec) or cb.get('today_any_day', False):
                allowed_days.append(today)
        else:
            allowed_days.append(resolve_date_expr(e, today, today, spec))
    start, end = cb.get('start', '10:00'), cb.get('end', '19:00')
    ok = False
    if d is not None and m:
        hhmm = '%02d:%02d' % (int(m.group(1)), int(m.group(2) or 0))
        when = _dt.datetime.combine(d, _dt.time(int(hhmm[:2]), int(hhmm[3:])))
        if d in allowed_days and start <= hhmm <= end and (when > now or not cb.get('after_now', True)):
            ok = True
            vars['cb_date'] = d.isoformat()
            vars['cb_time'] = hhmm
    if ok:
        vars['cb_ok'] = 'yes'
        vars['cb_say'] = 'Valid. The callback is being booked.'
        return
    n = int(num(vars.get('cb_invalid'), 0)) + 1
    vars['cb_invalid'] = str(n)
    windows = '; '.join('%s from %s to %s' % (say_date(x), start, end) for x in allowed_days)
    if n >= int(cb.get('max_invalid', 2)):
        vars['cb_ok'] = 'no'
        vars['cb_say'] = 'Not possible. Do not schedule.'
    else:
        vars['cb_say'] = 'That time is not possible. Ask once for another time within: %s (and later than now, %s).' % (windows, now.strftime('%H:%M'))


# ---------------------------------------------------------------- identity hooks

def normalize_date_answer(s, order='DMY'):
    s = str(s or '').strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
    m = re.match(r'^(\d{1,4})[./\-\s](\d{1,2})[./\-\s](\d{1,4})$', s)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        if len(a) == 4:
            y, mo, d = a, b, c
        elif order == 'MDY':
            mo, d, y = a, b, c
        else:
            d, mo, y = a, b, c
        if len(y) == 2:
            y = ('19' if int(y) > 30 else '20') + y
        try:
            return _dt.date(int(y), int(mo), int(d)).isoformat()
        except Exception:
            return s
    low = s.lower()
    for i, name in enumerate(MONTHS):
        if name.lower()[:3] in low:
            nums = re.findall(r'\d+', low)
            y = next((x for x in nums if len(x) == 4), None)
            d = next((x for x in nums if len(x) <= 2), None)
            if y and d:
                try:
                    return _dt.date(int(y), i + 1, int(d)).isoformat()
                except Exception:
                    return s
    return s


def id_pre_hook(vars, config, spec):
    body = config.get('body')
    if not isinstance(body, dict):
        return
    field = str(body.get('field', ''))
    vars['id_last_field'] = field
    qs = spec.get('identity', {}).get('questions', [])
    q = next((x for x in qs if x.get('field') == field), None)
    if q and q.get('format') == 'date':
        body['answer'] = normalize_date_answer(body.get('answer'), spec.get('identity', {}).get('numeric_date_order', 'DMY'))
    elif q and q.get('format') == 'digits':
        body['answer'] = re.sub(r'\D', '', str(body.get('answer', '')))


def id_post_hook(vars, result, spec):
    if not isinstance(result, dict):
        return
    result.pop('header', None)
    d = result.get('data')
    if not isinstance(d, dict):
        result['data'] = d = {}
    idc = spec.get('identity', {})
    qs = idc.get('questions', [])
    done = [x for x in str(vars.get('id_done', '')).split(',') if x]
    field = vars.get('id_last_field', '')
    ok = str(d.get('ok')).lower() == 'true'
    verified = str(d.get('verified')).lower() == 'true'
    hint = d.get('hint')
    if str(d.get('all_verified')).lower() == 'true':
        vars['id_state'] = 'verified'
        d['what_to_do_next'] = 'Identity is verified. Do not ask anything else.'
        return
    left = d.get('wrong_answers_left')
    if left is not None and str(left) == '0':
        vars['id_state'] = 'failed'
        d['what_to_do_next'] = 'The check has failed. Do not ask anything else.'
        return
    if hint:
        d['what_to_do_next'] = idc.get('hints', {}).get(str(hint), 'Ask the same question again.')
        return
    if not ok:
        d['what_to_do_next'] = 'The answer could not be read. Ask the same question again, clearly.'
        return
    if verified:
        # Questions are asked in order, so the number of correct answers says which one comes next.
        # (The field name of the call is not reliably available to a post-hook.)
        n = int(num(vars.get('id_ok_n'), 0)) + 1
        vars['id_ok_n'] = str(n)
        if field and field not in done:
            done.append(field)
        vars['id_done'] = ','.join(done)
        nxt = qs[n] if n < len(qs) else None
        d['what_to_do_next'] = ('Correct. Now ask: ' + nxt.get('ask', nxt.get('field'))) if nxt else 'Correct.'
        return
    d['what_to_do_next'] = 'That answer is wrong. Ask the same question again.'


def account_post_hook(vars, result, spec):
    if not isinstance(result, dict):
        return
    result.pop('header', None)
    d = result.get('data')
    if isinstance(d, dict):
        vars['acct_json'] = json.dumps(d)


# ---------------------------------------------------------------- call setup and closing texts

def setup_call(vars, spec):
    now = parse_now(vars.get('now'))
    vars['calendar'] = calendar_block(now, spec)
    vars['today_iso'] = now.date().isoformat()
    for k in ('cb_say', 'neg_say', 'readback_text', 'wrap_text', 'farewell_text'):
        if not vars.get(k):
            vars[k] = ''
    st = load_state(vars)
    st.setdefault('today', now.date().isoformat())
    save_state(vars, st)


def exit_text(spec, key):
    t = spec.get('texts', {})
    return (str(t.get('exits', {}).get(key, '') or '') + ' ' + str(t.get('goodbye', 'Goodbye.'))).strip()


def closing_text(vars, spec):
    st = load_state(vars)
    t = spec.get('texts', {})
    parts = [wrap_text(st, spec)]
    said = []
    for p in st.get('products', []):
        r = p.get('result', '')
        msg = t.get('result_say', {}).get(r, '')
        if msg and 'sorry' in msg.lower():
            msg = ''        # sympathy was said during the call; saying it again fails 'one sentence of sympathy'
        if msg and r not in said and not p.get('promise_ok'):
            parts.append(msg)
            said.append(r)
    parts.append(t.get('goodbye', 'Goodbye.'))
    return ' '.join(x for x in parts if x).strip()
