"""Закон за заштита на личните податоци (Службен весник 42/2020) with its two amending laws (294/2021, 101/2025).

The amendments are word-level edits. OPS transcribes each article of the amending law; parse checks that
every quoted string it uses appears in that amending law's own text (read from the gazette), and that every
target string occurs exactly once where it is applied (twice where the law says 'first and second sentence').
"""
import re
import fetch, zakon

GZ = "https://slvesnik.com.mk/Issues/%s.pdf"
BASE = ("42/2020", "606043d405e847ee92c7eaed5c8bd389", range(95, 172), "2020-02-16", "2020-02-16", "2020-02-24")
AMEND = [
    # key, label, issue, file, pages, adopted, published, in force, in-force clause
    ("zakon-2021-294", "Сл. весник 294/2021", "294/2021", "347ca6fd31344c17b804dab7b326c226", [2],
     "2021-12-23", "2021-12-27", "2022-01-04", "осмиот ден од денот на објавувањето"),
    ("zakon-2025-101", "Сл. весник 101/2025", "101/2025", "79b13c08eee04e138a6886642e4b0730", [305],
     "2025-05-14", "2025-05-21", "2025-05-21", "со денот на објавувањето"),
]
TITLES = {
    "zakon-2021-294": "Закон за изменување и дополнување на Законот за заштита на личните податоци",
    "zakon-2025-101": "Закон за дополнување на Законот за заштита на личните податоци",
}
# (law, article of the amending law, op, target unit, stav or None, args)
OPS = [
    ("zakon-2021-294", 1, "title", "clen-014", None, ["Обработка на лични податоци поврзани со казнени осуди и казнени дела"]),
    ("zakon-2021-294", 2, "replace_n", "clen-014", None, ["осуди за кривични дела", "казнени осуди и казнени дела", 2]),
    ("zakon-2021-294", 3, "insert_after", "clen-039", 9, ["Контролорот", "е должен да"]),
    ("zakon-2021-294", 4, "delete", "clen-064", 1, ["со овој"]),
    ("zakon-2021-294", 5, "replace_word", "clen-122", None, ["до", "со"]),
    ("zakon-2025-101", 1, "insert_after_ci", "clen-048", 2, ["Европската Унија", ", во земја членка на НАТО", "во земја членка на НАТО"]),
    ("zakon-2025-101", 1, "insert_after_ci", "clen-048", 3, ["во земја членка на Европската Унија,", " во земја членка на НАТО,", "во земја членка на НАТО,"]),
    ("zakon-2025-101", 2, "insert_after_ci", "clen-056", None, ["земјите членки на Европската Унија", ", во земјите членки на НАТО", "во земјите членки на НАТО"]),
    ("zakon-2025-101", 2, "insert_after_ci", "clen-056", None, ["меѓународни организации, Европската Унија", ", НАТО", "НАТО"]),
]
DECISIONS = [
    "Закон за заштита на личните податоци: in force on the eighth day after publication (its Art. 124): published 2020-02-16, in force 2020-02-24. Arts. 119-121 give controllers and regulations 18 months to conform; that is not a separate commencement of any provision.",
    "Сл. весник 294/2021 takes effect on the eighth day after publication (its Art. 6): published 2021-12-27, in force 2022-01-04. Сл. весник 101/2025 takes effect on publication (its Art. 4): 2025-05-21.",
    "101/2025 quotes 'Европската Унија' with a capital У; the 2020 text writes 'Европската унија' in Art. 48(3) and Art. 56 (and 'Унија' in Art. 48(2)). The insertion points are matched ignoring the case of that one letter; the words of the law are not recased.",
    "101/2025 says 'a comma is added and the words ... are added' after the quoted words: that is a comma, a space and the quoted words. In Art. 48(3) the quoted anchor already ends in a comma, so the insertion is a space, the quoted words and their own closing comma.",
    "Article titles are kept as a second line ('## title'); an article without its own title (Arts. 76 and 82) falls under the title before it in the gazette.",
]


def amending_text(fn, pages):
    import fitz
    d = fitz.open(fetch.get(GZ % fn))
    t = "\n".join(d[p].get_text() for p in pages)
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    return re.sub(r"\s+", " ", t)


def read_base(log):
    order, arts, heads = zakon.read_law(fetch.get(GZ % BASE[1]), BASE[2], r"ЗАШТИТА НА ЛИЧНИТЕ", log)
    return zakon.units(order, arts, heads)


def _stav_line(lines, stav):
    """Index of the line holding paragraph (stav); '(N) ' prefixed, or the only paragraph when stav is None."""
    if stav is None:
        return None
    for i, l in enumerate(lines):
        if l.startswith(f"({stav}) "):
            return i
    raise ValueError(f"stav ({stav}) not found")


def apply(units, law, log, texts):
    out = {k: dict(v) for k, v in units.items()}
    changed = set()
    for (lw, art, op, unit, stav, args) in OPS:
        if lw != law:
            continue
        src = texts[law]
        for q in args[:1] + args[2:3] if op.startswith("insert_after_ci") else [a for a in args if isinstance(a, str)]:
            if q.strip(", ") and q.strip(", ") not in src and q not in src:
                raise ValueError(f"{law} art {art}: quoted '{q}' not in the amending text")
        lines = out[unit]["text"].rstrip("\n").split("\n")
        if op == "title":
            ti = next(i for i, l in enumerate(lines) if l.startswith("## "))
            old = lines[ti]; lines[ti] = "## " + args[0]
        else:
            idx = _stav_line(lines, stav)
            rng = [idx] if idx is not None else [i for i, l in enumerate(lines) if not l.startswith("#")]
            body = "\n".join(lines[i] for i in rng)
            old = body
            if op == "replace_n":
                if body.count(args[0]) != args[2]:
                    raise ValueError(f"{unit}: '{args[0]}' found {body.count(args[0])} times")
                body = body.replace(args[0], args[1])
            elif op == "insert_after":
                if body.count(args[0]) != 1:
                    raise ValueError(f"{unit}: '{args[0]}' found {body.count(args[0])} times")
                body = body.replace(args[0], args[0] + " " + args[1])
            elif op == "delete":
                if body.count(args[0]) != 1:
                    raise ValueError(f"{unit}: '{args[0]}' found {body.count(args[0])} times")
                body = re.sub(r" {2,}", " ", body.replace(args[0], ""))
            elif op == "replace_word":
                hits = re.findall(rf"(?<!\w){args[0]}(?!\w)", body)
                if len(hits) != 1:
                    raise ValueError(f"{unit}: word '{args[0]}' found {len(hits)} times")
                body = re.sub(rf"(?<!\w){args[0]}(?!\w)", args[1], body)
            elif op == "insert_after_ci":
                pat = re.escape(args[0]).replace("Унија", "[Уу]нија")
                hits = list(re.finditer(pat, body))
                if len(hits) != 1:
                    raise ValueError(f"{unit}: '{args[0]}' found {len(hits)} times")
                m = hits[0]
                body = body[:m.end()] + args[1] + body[m.end():]
            else:
                raise ValueError(op)
            new = body.split("\n")
            for j, i in enumerate(rng):
                lines[i] = new[j]
        out[unit] = dict(out[unit], text="\n".join(lines) + "\n")
        changed.add(unit)
        log.append(f"zzlp {law} art {art} {op} {unit} stav {stav}: was {old[:100]!r}")
    return out, changed


def build(log, sha):
    units = read_base(log)
    laws = {"zakon-2020-042": {"label": "Сл. весник 42/2020", "title": "Закон за заштита на личните податоци", "kind": "law",
                               "enacted": BASE[3], "published": BASE[4], "url": GZ % BASE[1], "temporary_until": None,
                               "restores": None, "note": None}}
    versions = [{"from": BASE[5], "source_url": GZ % BASE[1], "sha256": sha(GZ % BASE[1]), "units": units, "credits": {},
                 "note": "As enacted, Службен весник 42/2020 (16 February 2020)."}]
    texts = {}
    for key, label, issue, fn, pages, adopted, published, in_force, clause in AMEND:
        texts[key] = amending_text(fn, pages)
        if clause not in texts[key]:
            raise ValueError(f"{key}: in-force clause '{clause}' not found")
        laws[key] = {"label": label, "title": TITLES[key], "kind": "law", "enacted": adopted, "published": published,
                     "url": GZ % fn, "temporary_until": None, "restores": None, "note": None}
        units, changed = apply(units, key, log, texts)
        versions.append({"from": in_force, "source_url": GZ % fn, "sha256": sha(GZ % fn), "units": units,
                         "credits": {k: [key] for k in sorted(changed)},
                         "note": f"{TITLES[key]}, Службен весник {issue} ({published})."})
    act = {"slug": "zakon-za-zastita-na-licnite-podatoci", "short": "ЗЗЛП",
           "title": "Закон за заштита на личните податоци", "url": GZ % BASE[1], "first_law": "zakon-2020-042",
           "earlier_laws": [], "versions": versions}
    return act, laws
