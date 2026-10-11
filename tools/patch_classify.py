import json
p = 'out/final/nodes/classify_cards.json'
n = json.load(open(p, encoding='utf-8'))
prop = n['pre_actions'][0]['function']['parameters']['properties']['code']
code = prop['default']
old = "cards = G('cards')\n"
add = """cards = G('cards')
if not isinstance(cards, list):
    cards = []
    _i = 0
    while _i < 20:
        _p = 'cards_list_response.data.data.cards.' + str(_i) + '.'
        if vars.get(_p + 'cardNumber') is None:
            break
        cards.append(dict(cardNumber=str(vars.get(_p + 'cardNumber') or ''), cardNumberLast4=str(vars.get(_p + 'cardNumberLast4') or ''), productName=str(vars.get(_p + 'productName') or ''), status=str(vars.get(_p + 'status') or ''), isMainCard=str(vars.get(_p + 'isMainCard') or '')))
        _i += 1
    if len(cards) == 0:
        cards = None
"""
assert old in code
prop['default'] = code.replace(old, add, 1)
json.dump(n, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('patched')
