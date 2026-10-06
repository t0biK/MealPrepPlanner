import math
import re

# canonical unit -> (plural, aliases); lookup is case-insensitive with an optional trailing dot
UNITS = {
    "g": ("g", ["gr", "gramm"]),
    "kg": ("kg", ["kilo", "kilogramm"]),
    "ml": ("ml", ["milliliter"]),
    "cl": ("cl", []),
    "dl": ("dl", []),
    "l": ("l", ["liter", "ltr"]),
    "EL": ("EL", ["essl", "esslöffel", "eßlöffel"]),
    "TL": ("TL", ["teel", "teelöffel"]),
    "Msp.": ("Msp.", ["msp", "messerspitze", "messerspitzen"]),
    "Prise": ("Prisen", []),
    "Spritzer": ("Spritzer", []),
    "Schuss": ("Schuss", []),
    "Handvoll": ("Handvoll", []),
    "Tasse": ("Tassen", []),
    "Stück": ("Stück", ["stk", "st"]),
    "Dose": ("Dosen", ["ds"]),
    "Glas": ("Gläser", []),
    "Packung": ("Packungen", ["pck", "pkg", "pkt", "päckchen", "pack"]),
    "Becher": ("Becher", []),
    "Bund": ("Bund", ["bd"]),
    "Zehe": ("Zehen", []),
    "Scheibe": ("Scheiben", []),
    "Würfel": ("Würfel", []),
    "Zweig": ("Zweige", []),
    "Stange": ("Stangen", []),
    "Kopf": ("Köpfe", []),
    "Knolle": ("Knollen", []),
    "Flasche": ("Flaschen", []),
    "Blatt": ("Blätter", []),
}

_ALIASES = {}
for _unit, (_plural, _aliases) in UNITS.items():
    for _word in [_unit, _plural, *_aliases]:
        _ALIASES[_word.lower().rstrip(".")] = _unit

_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}
_UF = "".join(_FRACTIONS)
_NUMBER = re.compile(
    rf"(?:(\d+)\s*([{_UF}])"  # 1½, 1 ½
    rf"|([{_UF}])"  # ½
    r"|(\d+)\s+(\d+)/(\d+)"  # 1 1/2
    r"|(\d+)/(\d+)"  # 1/2
    r"|(\d+(?:[.,]\d+)?))"  # 2, 1,5, 1.5
)
_RANGE = re.compile(r"\s*(?:[-–—]|bis\b)\s*")
_MARKER = re.compile(r"(n\.\s*B\.|nach\s+Belieben|etwas|evtl\.?|ggf\.?|wenig|einige)(?=[\s,]|$)", re.I)
_UNIT_WORD = re.compile(r"([^\W\d_]+)\.?")


def _number(s):
    """Parse a leading number: (value, chars consumed) or None."""
    m = _NUMBER.match(s)
    if not m:
        return None
    g = m.groups()
    if g[0]:
        value = int(g[0]) + _FRACTIONS[g[1]]
    elif g[2]:
        value = _FRACTIONS[g[2]]
    elif g[3]:
        den = int(g[5])
        value = int(g[3]) + int(g[4]) / den if den else None
    elif g[6]:
        den = int(g[7])
        value = int(g[6]) / den if den else None
    else:
        value = float(g[8].replace(",", "."))
    return None if value is None else (value, m.end())


def _leading_amount(s):
    """Amount at the start of s (ranges use the upper value): (value, rest) or (None, s)."""
    first = _number(s)
    if not first:
        return None, s
    value, end = first
    r = _RANGE.match(s, end)
    if r:
        second = _number(s[r.end():])
        if second:
            value, end = max(value, second[0]), r.end() + second[1]
    return value, s[end:]


def parse_line(line):
    """German ingredient line -> {amount, unit, name, note}. Never raises, never loses text."""
    text = re.sub(r"\s+", " ", line).strip()
    text = re.sub(r"^(?:[-•*]+|\d+\.(?=\s))\s*", "", text)
    notes = []
    marker = _MARKER.match(text)
    if marker:
        notes.append(marker.group(1))
        text = text[marker.end():].lstrip()
    amount, rest = _leading_amount(text)
    rest = rest.strip()
    unit = None
    if amount is not None or marker:
        word = _UNIT_WORD.match(rest)
        if word and word.group(1).lower() in _ALIASES:
            unit = _ALIASES[word.group(1).lower()]
            rest = rest[word.end():].strip()
    parens = re.findall(r"\(([^)]*)\)", rest)
    notes += [p.strip() for p in parens if p.strip()]
    rest = re.sub(r"\([^)]*\)", " ", rest)
    name, _, after = rest.partition(",")
    if after.strip():
        notes.append(after.strip())
    name = re.sub(r"\s+", " ", name).strip(" ;")
    if not name:
        return {"amount": None, "unit": None, "name": re.sub(r"\s+", " ", line).strip(), "note": None}
    return {"amount": amount, "unit": unit, "name": name, "note": "; ".join(notes) or None}


def parse_lines(text):
    return [parse_line(line) for line in text.splitlines() if line.strip()]


def scale(amount, factor):
    return None if amount is None else round(amount * factor, 4)


def fmt_amount(amount, unit):
    """German display of an amount: '1,5 kg', '2 Dosen', '4'; '' without an amount."""
    if amount is None:
        return ""
    amount = round(amount, 2)
    number = f"{amount:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    if unit is None:
        return number
    return f"{number} {UNITS[unit][0] if amount > 1 else unit}"


# ---- shopping list (M8) ----

MASS = {"g": 1, "kg": 1000}
VOLUME = {"ml": 1, "cl": 10, "dl": 100, "l": 1000}
SPOONS = {"EL", "TL", "Msp.", "Prise", "Spritzer", "Schuss", "Handvoll", "Tasse"}  # summed per unit, 1 decimal


def unit_key(unit):
    """What amounts are summed in: 'g' (g, kg), 'ml' (ml, cl, dl, l), the canonical unit, '' without a unit."""
    if unit in MASS:
        return "g"
    if unit in VOLUME:
        return "ml"
    return unit or ""


def to_base(amount, unit):
    """Amount in the base unit of its group (g, ml); other units are unchanged."""
    return None if amount is None else amount * (MASS.get(unit) or VOLUME.get(unit) or 1)


def shopping_round(amount, key):
    """Rounding of a summed amount per unit_key: g/ml 2 decimals, spoons 1 decimal, countable units up."""
    if key in ("g", "ml"):
        return round(amount, 2)
    if key in SPOONS:
        return round(amount, 1)
    return math.ceil(round(amount, 4))


def aggregate(lines):
    """(name, amount, unit) lines with scaled amounts -> {name.casefold(): {name, amounts: {unit_key: summed amount}}}.
    Amount-less lines only count when the name has no amount at all (amounts stays empty then)."""
    items = {}
    for name, amount, unit in lines:
        item = items.setdefault(name.casefold(), {"name": name, "amounts": {}})
        if amount is not None:
            key = unit_key(unit)
            item["amounts"][key] = item["amounts"].get(key, 0) + to_base(amount, unit)
    for item in items.values():
        item["amounts"] = {k: shopping_round(a, k) for k, a in item["amounts"].items()}
    return items


def format_note(parts):
    """{unit_key: amount} -> '1,25 kg + 2 Dosen' (mass, volume, then other units alphabetically; '' if empty)."""
    out = []
    for key in sorted(parts, key=lambda k: ({"g": 0, "ml": 1}.get(k, 2), k.casefold())):
        amount = parts[key]
        if key in ("g", "ml") and amount >= 1000:
            amount, key = amount / 1000, "kg" if key == "g" else "l"
        out.append(fmt_amount(amount, key or None))
    return " + ".join(out)
