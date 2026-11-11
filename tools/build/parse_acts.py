"""North Macedonia: every law in the Ministry of Justice's legal database (LDBIS) as published in Службен весник.

Reads a local copy of LDBIS records (ldbis.pravda.gov.mk PregledNaZakon.aspx?id=N: title, kind, gazette issue,
publication / in-force / application / end dates, the chain of linked acts) and of each law's text, extracted line
by line from the law's PDF (the Official Gazette text the database serves), and writes corpus.d parts
(SCHEMA.md in scripts/gitlaw_common). Deterministic: the same inputs give the same bytes.

    python -X utf8 parse_acts.py --src <folder with records*.jsonl and raw/> --retrieved YYYY-MM-DD [--report holds.jsonl]

Input formats: records*.jsonl, one JSON row per LDBIS id ({"id", "title", "kind", "version", "issue", "published",
"in_force", "applies", "ended", "chain": [[title, issue, dd.mm.yyyy, id], ...], "text": {...}}); raw/<id//1000>/<id>.json.gz
= {"meta": {"sha256", "pages", ...}, "lines": [[page, block, x0, x1, y, size, bold, italic, text], ...]}.

What becomes an act here: a law (Закон) whose text is the law as published, one version, from the in-force day the
database gives, checked against the law's own final clause where that clause can be read. The promulgation decree
(Указ) above the law is not part of it and is cut.

An act the database marks no longer valid (Неактивен, with an end-of-validity day) gets a second version: no units, from
that day. LDBIS records as the end of validity the day the repealing act enters into force (the first day the act is
no longer valid), so the day is used only when an act the database links to it enters into force on that very day
(its LDBIS in-force day, agreeing with its own final clause where that can be read) and no other linked act enters
into force the day before or after. The removal is credited to that act when its text says the act ceases to be
valid and cites the act's gazette number in the same article; otherwise to the publisher, as unexplained. An amending
act gets the second version only with such a text: LDBIS also marks earlier amending acts no longer valid when a later
amendment takes effect.

An act with no numbered articles at all (an instruction or order made of points or paragraphs) is one unit, the text
from its title to its signature, when its own in-force clause is there and agrees and the text is clean. Acts released
by this rule, or by the citing-acts exception below, go to parts of their own (60-<kind>-<year>.json).

2026-11-09 (romania-law standard): an act the database links to later acts has no official consolidated text, so it
is published as enacted and labelled, not held back: `label` "amended, not consolidated" with `amended_by` (the later
linked acts that amend, repeal or rule on it: amending acts and Constitutional Court decisions) and `cited_by` (other
later linked acts whose text does more than cite it in an enacting formula), or "cited later, not consolidated" when
only the latter exist. The same for an amending law that a later act amends or a court decision follows, and for an
act no longer valid that linked acts amended before its end of validity. An in-scope record whose page serves no
text is a holds row (no_text_on_ldbis), not a silent drop.

An act is held back, not published (the reason is written to the report), when:
- the database links an official consolidated text (Пречистен текст) to it (later_consolidated_text): it waits until
  that text is built as a version;
- a correction (Исправка) names it, by title or as a linked act;
- the database gives it an end-of-validity day that is still to come, or one that no linked act explains as above;
- its in-force day is missing, or its own final clause names a different day;
- its text does not split cleanly into articles, or the PDF has no usable text layer.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARTS = HERE / "corpus.d"
PAGE_URL = "https://ldbis.pravda.gov.mk/PregledNaZakon.aspx?id={id}"

MONTHS = {m: i for i, m in enumerate(["јануари", "февруари", "март", "април", "мај", "јуни", "јули", "август",
                                       "септември", "октомври", "ноември", "декември"], 1)}
ORD = {"наредниот": 1, "следниот": 1, "првиот": 1, "вториот": 2, "третиот": 3, "четвртиот": 4, "петтиот": 5, "шестиот": 6, "седмиот": 7, "осмиот": 8,
       "деветтиот": 9, "десеттиот": 10, "петнаесеттиот": 15, "дваесеттиот": 20, "триесеттиот": 30}
LAT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ѓ": "gj", "е": "e", "ж": "zh", "з": "z", "ѕ": "dz", "и": "i",
       "ј": "j", "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n", "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s",
       "т": "t", "ќ": "kj", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "џ": "dzh", "ш": "sh"}
ART = re.compile(r"^Член\s+(\d+)\s*(?:-?\s*([а-ш]{1,2}))?\s*\.?$")
HEAD_WORDS = re.compile(r"^(?:Глава|ГЛАВА|Дел|ДЕЛ|Оддел|ОДДЕЛ|Поглавје|ПОГЛАВЈЕ|Подглава|[IVXLC]+\.?\s)")
ANNEX = re.compile(r"^(?:Прилог|ПРИЛОГ)(?:\s+(?:бр\.\s*)?\S{1,6})?\.?$")
# LDBIS kind -> (slug kind, title word, law kind for the commit). Laws carry a promulgation decree above them;
# the others (Government decrees, ministers' rulebooks, ...) carry an enacting formula above and a signature below.
KINDS = {"Закон": ("zakon", "ЗАКОН", "law"), "Правилник": ("pravilnik", "ПРАВИЛНИК", "decree"),
         "Уредба": ("uredba", "УРЕДБА", "decree"), "Упатство": ("upatstvo", "УПАТСТВО", "decree"),
         "Наредба": ("naredba", "НАРЕДБА", "decree"), "Статут": ("statut", "СТАТУТ", "decree"),
         "Деловник": ("delovnik", "ДЕЛОВНИК", "decree")}
# 2026-11-11 (deep completion): every other kind LDBIS records is in scope too. These acts' heading word is read from
# each record's own title (LDBIS files some decisions titled "Решение" under Одлука, some "Правила" under Останато), and
# their slug kind is kind_slug() of the LDBIS kind, as the law keys of such acts already were. The seven kinds above
# keep every rule they had (OLD_KINDS), so nothing already published reads differently.
OLD_KINDS = KINDS
NEW_KINDS = {"Одлука", "Решение", "Останато", "Исправка (на закон или друг пропис)", "Договор", "Указ", "Декларација",
             "Резолуција", "Заклучок", "Заедничка позиција", "Узанси", "Уставен закон", "Препорака",
             "Амандман на Уставот", "Устав"}
SCOPE = set(OLD_KINDS) | NEW_KINDS
# The Constitution (1991 text and Amendments I-XXXVI) is built by hand in ustav/ (corpus.json): its LDBIS records are
# never published twice.
CONSTITUTION_KINDS = {"Амандман на Уставот", "Устав"}
IN_FORCE_ANY = re.compile(r"(?:Овој|Оваа|Ова|Овие|Овоj)\s+[а-шѓќјљњџѕ]+(?:\s+[а-шѓќјљњџѕ]+)?\s+(?:влегува(?:ат)?\s+во\s+сила|произведува\s+правно\s+дејство)"
                          r"\s+([^.]*?)(?:\.|,\s*а\s|$)", re.S)


def title_start(word: str) -> re.Pattern:
    return re.compile("^" + r"\s?".join(word) + r"(?:\s+|$)")


TITLE_START = title_start("ЗАКОН")
SIGNATURE = re.compile(r"^(?:Бр\.|Број)\s*:?\s*\d")
PAGE_NO = re.compile(r"^\s*\d+\s+од\s+\d+\s*$")
OPEN_Q, CLOSE_Q = "„", "“”"
IN_FORCE = re.compile(r"(?:(?:Овој|Оваа|Ова|Овоj)\s+(?i:закон|правилник|уредба|упатство|наредба|статут|деловник)|Законот)\s+влегува\s+во\s+сила\s+([^.]*?)(?:\.|,\s*а\s|$)", re.S)
AMENDING = re.compile(r"^(?:Закон|Правилник|Уредба|Упатство|Наредба|Статут|Деловник)\s+за\s+(?:изменување|дополнување|изменувања|дополнувања)", re.I)
COURT = re.compile(r"Уставен\s+суд|Уставниот\s+суд", re.I)
CORRECTION = re.compile(r"^Исправка\s+на\s+(.+)$")
REPEAL = re.compile(r"престанува(?:ат)?\s+да\s+(?:важ(?:и|ат)|се\s+применува(?:ат)?)|престануваат\s+да\s+важат"
                    r"|престанува\s+со\s+важење|се\s+става(?:ат)?\s+вон\s+сила|престанаа\s+да\s+важат", re.I)
# A footnote's mark at the start of its first line: "*", "(*)", "∗", the Gazette's symbol-font mark U+F02A (kept in the
# text as extracted), or a number of one or two digits before the footnote's capitalised first word ("1 Со оваа
# уредба", "1Во овој Правилник"; not a degree sign's "0").
FOOT_MARK = re.compile(r"^(?:\(\s*\)\s*[*∗٭]|\(?\s*[*∗٭]+\s*\)?|[1-9]\d?(?=\s*[А-ЯЀ-ЏA-Z]))")
WORD = re.compile(r"[А-Яа-яЃЌЀЍѓќѐѝЈјЉљЊњЏџЅѕ]{3,}")  # a word of the text (formula pieces have none or one)
RUNNING_HEAD = re.compile(r"\s*(?:Службен весник на (?:РСМ|РМ|Република)[^.]{0,80}?бр\.\s*\d+ од [\d.]+ ?година|\d+\s+од\s+\d+|\d{1,4})\s*")
# Laws held elsewhere in this corpus (hand-built acts in corpus.json): never published twice.
BUILT_ELSEWHERE = {("Закон за заштита на личните податоци", 42, 2020)}


class Hold(Exception):
    pass


def norm(s: str) -> str:
    s = s.replace(" ", " ").replace("­", "").replace("\t", " ")
    s = s.replace("–", "-").replace("—", "-").replace("‐", "-").replace("‑", "-")
    s = s.replace("’", "'").replace("‘", "'")
    return re.sub(r" {2,}", " ", s).strip()


def dmy(s: str | None) -> str | None:
    m = re.match(r"^(\d\d)\.(\d\d)\.(\d{4})", (s or "").strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def merge_rows(lines: list) -> list[dict]:
    """Physical lines: spans PyMuPDF returned separately on one baseline (justified text) are joined left to right."""
    rows: list[dict] = []
    # Reading order: top to bottom, left to right on each page (the PDF's own block order can put a paragraph
    # before the heading printed above it).
    lines = sorted(lines, key=lambda l: (l[0], round(l[4] / 3), l[2]))
    for pn, blk, x0, x1, y, size, bold, ital, t in lines:
        r = rows[-1] if rows else None
        if r and r["page"] == pn and abs(r["y"] - y) < 2.0 and x0 >= r["x1"] - 1:
            gap = " " if not r["text"].endswith(" ") and not t.startswith(" ") else ""
            r["text"] += gap + t
            r["x1"] = x1
            r["bold"] = r["bold"] and bold
            continue
        rows.append({"page": pn, "block": blk, "x0": x0, "x1": x1, "y": y, "size": size, "bold": bool(bold), "text": t})
    for r in rows:
        r["text"] = norm(r["text"])
    return [r for r in rows if r["text"] and not PAGE_NO.match(r["text"])]


def paragraphs(rows: list[dict]) -> list[dict]:
    """Paragraphs: a new block, an indented first line, or a centred line starts one."""
    if not rows:
        return []
    size = statistics.median(r["size"] for r in rows)
    # Running heads and page numbers in the top and bottom margins are not text; anything else there stops the act.
    head = re.compile(r"^(?:Службен весник на (?:РСМ|РМ|Република)[^.]*бр\.\s*\d+ од [\d.]+ ?година|\d{1,4}|Стр\.?\s*\d+.*)$")
    margin = [r for r in rows if r["y"] < 55 or (r["y"] > 762 and r["size"] < size - 0.5) or head.match(r["text"])]
    if any(not head.match(r["text"]) for r in margin):
        raise Hold("text_in_margin:" + next(r["text"] for r in margin if not head.match(r["text"]))[:80])
    rows = [r for r in rows if r not in margin]
    # A line's size is its first span's: a small first span is no proof the line is small print (a footnote's first
    # line starts with its small superscript mark, the rest printed at text size; a text line may start with a small
    # dash). The count of small-print rows below (as paragraphs) keeps deciding which acts are held back.
    small = [r for r in rows if r["size"] < size - 1.2]
    old_feet = 0
    for f in small:
        if not (old_feet and not re.match(r"^\(?\*+\)?|^\d{1,2}\)?\s", f["text"])):
            old_feet += 1
    is_small = [r["size"] < size - 1.2 for r in rows]
    in_foot = list(is_small)  # as before: small print is a footnote, unless one of the two rules below says otherwise
    blocks: dict[tuple, list[int]] = defaultdict(list)
    for k, r in enumerate(rows):
        blocks[(r["page"], r["block"])].append(k)
    # 1. A footnote printed as one block at the foot of a page: from a line that starts with a footnote mark to the end
    #    of the page, all in that block, the mark printed small (a superscript) or the lines after it small, no article
    #    heading or signature in it. Its lines printed at text size are the footnote too, not the article's.
    for (pg, _b), ks in blocks.items():
        k0 = next((k for k in ks if FOOT_MARK.match(rows[k]["text"])), None)
        if k0 is None:
            continue
        foot = [k for k in ks if k >= k0]
        rest_of_page = [k for k in range(k0, len(rows)) if rows[k]["page"] == pg]
        if foot != rest_of_page or len(foot) > 25:
            continue
        if not (is_small[k0] or (len(foot) > 1 and all(is_small[k] for k in foot[1:]))):
            continue
        if any(ART.match(rows[k]["text"]) or SIGNATURE.match(rows[k]["text"]) for k in foot):
            continue
        for k in foot:
            in_foot[k] = True
    # 2. A line of an article that starts with a small character (a degree sign, a dash, a math sign, a paragraph
    #    number): below the first article heading and above the act's last signature, not a footnote mark, and
    #    printed in a block that has lines at text size, or between two such lines on its page. (Small print above
    #    the first article, such as the issuing body's name, stays where it was.)
    sig_last = max((k for k, r in enumerate(rows) if SIGNATURE.match(r["text"])), default=-1)
    art_first = next((k for k, r in enumerate(rows) if ART.match(r["text"])), len(rows))
    texty = {key: any(not is_small[k] for k in ks) for key, ks in blocks.items()}

    def body_like(k: int) -> bool:
        return not is_small[k] or texty[(rows[k]["page"], rows[k]["block"])]

    for k, r in enumerate(rows):
        if not is_small[k] or not in_foot[k] or k <= art_first or k >= sig_last or FOOT_MARK.match(r["text"]):
            continue
        if len(WORD.findall(r["text"])) < 2:
            continue  # formula pieces and table cells: where they belong can't be told from the line alone
        if any(in_foot[j] and not is_small[j] for j in blocks[(r["page"], r["block"])]):
            continue  # part of a footnote found by rule 1
        if k > 0 and rows[k - 1]["page"] == r["page"] and in_foot[k - 1]:
            continue  # the next line of a footnote printed in small print
        prev_ok = k > 0 and rows[k - 1]["page"] == r["page"] and body_like(k - 1)
        next_ok = k + 1 < len(rows) and rows[k + 1]["page"] == r["page"] and body_like(k + 1)
        if texty[(r["page"], r["block"])] or (prev_ok and next_ok):
            in_foot[k] = False
    feet = [r for r, f in zip(rows, in_foot) if f]
    # the page's measures (left margin, centre) from the lines whose size is the text's, as before
    sized = [r for r in rows if r["size"] >= size - 1.2]
    rows = [r for r, f in zip(rows, in_foot) if not f]
    if not sized:
        raise Hold("no_text_rows")
    left = statistics.median(r["x0"] for r in sized)
    lefts = [r["x0"] for r in sized if abs(r["x0"] - left) < 25]
    left = min(lefts) if lefts else left
    right = max(statistics.median(r["x1"] for r in sized), max(r["x1"] for r in sized) - 2)
    centre = (left + right) / 2
    out: list[dict] = []
    for r in rows:
        mid = (r["x0"] + r["x1"]) / 2
        centred = abs(mid - centre) < 12 and r["x0"] > left + 15 and r["x1"] < right - 10
        indent = r["x0"] > left + 6
        prev = out[-1] if out else None
        if prev is None or centred or prev["centred"] or indent:
            new = True
        elif r["page"] != prev["page"]:
            new = False  # a paragraph running over a page break
        else:
            new = r["block"] != prev["block"]
        if prev is not None and centred and prev["centred"] and r["block"] == prev["block"] and prev["page"] == r["page"]                 and not ART.match(r["text"]) and not ART.match(prev["text"]):
            new = False  # a centred heading over two lines
        if new:
            out.append({"text": r["text"], "centred": centred, "bold": r["bold"], "page": r["page"], "block": r["block"]})
        else:
            a, b = prev["text"], r["text"]
            if re.search(r"[A-Za-zА-Яа-яЃЌЀЍѓќѐѝЈјЉљЊњЏџЅѕ]-$", a) and re.match(r"[а-шѓќјљњџѕ]", b):
                prev["text"] = a + b
            else:
                prev["text"] = a + " " + b
            prev["bold"] = prev["bold"] and r["bold"]
            prev["page"], prev["block"] = r["page"], r["block"]
    for f in feet:
        # a new footnote starts at a mark; a footnote's lines printed at text size (rule 1) only at a footnote mark
        # proper ("2 март 2015 година" goes on the footnote above)
        starts = FOOT_MARK.match(f["text"]) if f["size"] >= size - 1.2 else re.match(r"^\(?\*+\)?|^\d{1,2}\)?\s", f["text"])
        if out and out[-1].get("foot") and not starts:
            a, b = out[-1]["text"], f["text"]
            # a footnote's lines printed at text size (rule 1) join as the text's lines do: a word broken at a line end
            # is joined again
            if f["size"] >= size - 1.2 and re.search(r"[A-Za-zА-Яа-яЃЌЀЍѓќѐѝЈјЉљЊњЏџЅѕ]-$", a) \
                    and re.match(r"[а-шѓќјљњџѕ]", b):
                out[-1]["text"] = a + b
            else:
                out[-1]["text"] = a + " " + b
        else:
            out.append({"text": f["text"], "centred": False, "bold": False, "page": f["page"], "block": f["block"], "foot": True})
    for p in out:
        p["text"] = norm(p["text"])
    return Paras(out, old_feet)


class Paras(list):
    """Paragraphs, with the number of small-print paragraphs as counted before footnotes were told from text."""

    def __init__(self, items, old_feet: int):
        super().__init__(items)
        self.old_feet = old_feet


def suffix_key(s: str) -> str:
    return "".join(LAT.get(c, c) for c in s)


def split(paras: list[dict], db_title: str = "", word: str = "ЗАКОН", new: bool = False) -> tuple[dict, str, str | None]:
    """(units, title, adoption day). The law starts at its upper-case title after the decree."""
    feet = [p["text"] for p in paras if p.get("foot")]
    if getattr(paras, "old_feet", len(feet)) > 6:
        raise Hold("small_print_tables")  # forms and tables printed small: not split reliably
    paras = [p for p in paras if not p.get("foot")]
    is_law = word == "ЗАКОН"
    starter = title_start(word)
    texts = [p["text"] for p in paras]
    first_art = next((i for i, p in enumerate(paras) if p["centred"] and ART.match(p["text"])), None)
    if first_art is None:
        raise Hold("no_articles")
    start = None
    for i in range(first_art - 1, -1, -1):
        t = texts[i]
        if starter.match(t) and t == t.upper():
            start = i
            break
    if start is None:
        raise Hold("no_title_line")
    if is_law and not any(re.match(r"^У\s*К\s*А\s*З", t) for t in texts[:start]):
        raise Hold("no_decree")
    decree = " ".join(texts[:start])
    m = re.search(r"(?:го\s+донесе\s+на\s+седницата|на\s+седницата)\s+одржана\s+на\s+(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", decree)
    adopted = date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).isoformat() if m else None
    title = starter.sub(word + " ", texts[start], count=1).strip()
    preamble = [] if is_law else texts[:start]
    if not is_law:
        # the enacting formula above the title, after the issuing body's name: "Врз основа на член ... донесе"
        k0 = next((j for j, t in enumerate(preamble) if t.startswith(("Врз основа", "Согласно", "Врз основ"))
                   or (new and formula_inside(t))), None)
        if k0 is None:
            raise Hold("no_enacting_formula")
        preamble = preamble[k0:]
    k = start + 1
    while k < first_art and texts[k] == texts[k].upper() and not HEAD_WORDS.match(texts[k]) \
            and len(title) < len(db_title) - 2 and re.search(r"[А-Ш]", texts[k]):
        title += " " + texts[k]
        k += 1
    body = paras[k:]
    units: dict[str, dict] = {}
    order: list[str] = []
    pending: list[str] = []
    cur = None
    depth = 0
    last_num = 0
    pre: list[str] = list(preamble)

    def heading_units(after: str | None):
        for j, h in enumerate(pending, 1):
            hk = f"{after}-zz-heading-{j}" if after else f"0-heading-{j}"
            units[hk] = {"label": h, "kind": "heading", "lines": [f"# {h}"]}
            order.append(hk)
        pending.clear()

    for idx, p in enumerate(body):
        t = p["text"]
        m = ART.match(t) if p["centred"] else None
        if m and depth:
            # An unclosed quote in the text before: a centred article heading that continues the numbering, after a
            # paragraph that does not introduce quoted text, is this law's next article.
            prev_t = body[idx - 1]["text"] if idx else ""
            if not (not m.group(2) and int(m.group(1)) == last_num + 1 and not prev_t.rstrip().endswith(":")):
                m = None
            else:
                depth = 0
        if m:
            if not m.group(2):
                last_num = int(m.group(1))
            key = f"art-{int(m.group(1)):03d}" + (f"-{suffix_key(m.group(2))}" if m.group(2) else "")
            if key in units:
                raise Hold(f"duplicate_unit:{key}")
            # A short centred line just above the article is its caption; earlier ones are headings.
            caption = None
            if pending and not HEAD_WORDS.match(pending[-1]) and pending[-1] != pending[-1].upper():
                caption = pending.pop()
            heading_units(cur)
            label = f"Член {m.group(1)}" + (f"-{m.group(2)}" if m.group(2) else "")
            units[key] = {"label": label, "kind": "art", "lines": [f"# {label}"] + ([caption] if caption else [])}
            order.append(key)
            cur = key
        elif depth == 0 and p["centred"] and cur is not None and ANNEX.match(t):
            heading_units(cur)
            key = f"prilog-{sum(1 for x in order if x.startswith('prilog-')) + 1:02d}"
            units[key] = {"label": t, "kind": "annex", "lines": [f"# {t}"]}
            order.append(key)
            cur = key
        elif depth == 0 and p["centred"] and len(t) < 250 and (cur is not None or not pre or not is_law):
            pending.append(t)
        else:
            if pending:
                # centred lines that are not followed by an article belong to the text (quoted headings etc.)
                if cur is None:
                    pre.extend(pending)
                else:
                    units[cur]["lines"].extend(pending)
                pending.clear()
            if cur is None:
                pre.append(t)
            else:
                units[cur]["lines"].append(t)
        depth = max(0, depth + t.count(OPEN_Q) - sum(t.count(c) for c in CLOSE_Q))
    if pending:
        if cur is None:
            raise Hold("trailing_headings")
        units[cur]["lines"].extend(pending)
    if not is_law:
        # The signature below the text (number, day, place, office, name) is not part of the act: cut from the last
        # article, from its first line that starts with the act's number ("Бр. 12-345/1").
        arts_order = [k for k in order if units[k]["kind"] == "art"]
        if not arts_order:
            raise Hold("no_articles")
        last = units[arts_order[-1]]["lines"]
        if new:
            cut, annex = sig_cut(last, True)
        else:
            annex = None
            cut = next((j for j in range(1, len(last)) if SIGNATURE.match(last[j])), None)
            if cut is None or sum(len(x) for x in last[cut:]) > 400:
                raise Hold("no_signature")
            # the office printed beside it may start a line or two higher ("Претседател на комисија и"): short lines
            # that do not end a sentence or item, right above the number
            while cut > 1 and len(last[cut - 1]) < 60 and not re.search(r"[.,;:“”\"]$", last[cut - 1]):
                cut -= 1
        del last[cut:]
        if annex:
            if any(units[k]["kind"] != "heading" for k in order[order.index(arts_order[-1]) + 1:]):
                raise Hold("text_after_signature")
            key = f"prilog-{sum(1 for x in order if x.startswith('prilog-')) + 1:02d}"
            units[key] = {"label": annex[0], "kind": "annex", "lines": [f"# {annex[0]}"] + annex[1:]}
            order.append(key)
        if any(units[k]["kind"] not in ("annex", "heading") for k in order[order.index(arts_order[-1]) + 1:]):
            raise Hold("text_after_signature")
    out = {}
    if pre:
        out["uvod"] = {"label": "Вовед", "kind": "preamble", "text": "\n".join(pre) + "\n"}
    for key in order:
        u = units[key]
        out[key] = {"label": u["label"], "kind": u["kind"], "text": "\n".join(u["lines"]) + "\n"}
    if feet:
        out["zz-fusnoti"] = {"label": "Фусноти", "kind": "note", "text": "# Фусноти\n" + "\n".join(feet) + "\n"}
    arts = [k for k in out if out[k]["kind"] == "art"]
    if not arts:
        raise Hold("no_articles")
    nums = [int(k[4:7]) for k in arts if len(k) == 7]
    if nums and nums != list(range(1, len(nums) + 1)):
        raise Hold("article_numbers_not_consecutive")
    for k in arts:
        if out[k]["text"].count("\n") < 2:
            raise Hold(f"empty_article:{k}")
    return out, norm(title), adopted


def own_in_force(units: dict, published: str, rx: re.Pattern = IN_FORCE) -> tuple[str | None, str]:
    text = " ".join(u["text"].replace("\n", " ") for k, u in units.items() if k.startswith("art-"))
    text = re.sub(r"„[^“”]{40,}?[“”]", " ", text)  # provisions quoted into another act
    clauses = [m.group(1) for m in rx.finditer(text)]
    if not clauses:
        return None, "none"
    c = clauses[-1].strip()
    p = date.fromisoformat(published)
    if rx is IN_FORCE_ANY and re.match(r"^(?:со|од)\s+денот\s+на\s+(?:неговото\s+|нејзиното\s+)?објавувањето", c):
        return published, "publication"
    if re.match(r"^со\s+денот\s+на\s+(?:неговото\s+)?објавувањето", c):
        return published, "publication"
    m = re.match(r"^(" + "|".join(ORD) + r")\s+ден\s+од\s+денот\s+на\s+(?:неговото\s+)?објавувањето", c)
    if m:
        return (p + timedelta(days=ORD[m.group(1)])).isoformat(), "days"
    m = re.match(r"^(?:на|со)\s+(?:денот\s+)?(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", c)
    if m:
        return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).isoformat(), "date"
    return None, "unreadable:" + c[:100]


CYR_UP, CYR_LO = "А-ШЃЌЅЈЉЊЏЀЍ", "а-шѓќѕјљњџѐѝ"
UNCLEAN = [
    ("odd_character", re.compile(r"[^\sA-Za-z0-9" + CYR_UP + CYR_LO + r".,;:!?()\[\]/%+*=§№°<>&@#_'\"„“”‚‘«»…\-]")),
    ("doubled_capital", re.compile(r"(?<![" + CYR_UP + r"])([" + CYR_UP + r"])\1[" + CYR_LO + r"]")),
    ("glued_words", re.compile(r"[" + CYR_LO + r"][" + CYR_UP + r"]")),
    ("glued_number", re.compile(r"\d[" + CYR_LO + r"]{3,}")),
    ("mixed_script", re.compile(r"[A-Za-z][" + CYR_UP + CYR_LO + r"]|[" + CYR_UP + CYR_LO + r"][A-Za-z]")),
    ("long_token", re.compile(r"[" + CYR_UP + CYR_LO + r"]{31,}")),
    # short words typed in Latin letters that look Cyrillic ("a ce упатени"): the wrong letters, though they look right
    # (Roman numerals, "I." "II." "X.", are not words)
    ("latin_lookalike", re.compile(r"(?<![A-Za-z])(?![IVXLCDM]+(?![A-Za-z]))[aceojpxyiACEOJPXYKMTHBI]{1,3}(?![A-Za-z])")),
]


# 2026-11-11 (new kinds only): the act's number under the text also reads "УП1 бр. 08-582/24" (regulators' cases),
# "О. бр. 02-15/162/2000" (the National Bank's council), "бр." in lower case; and an annex printed after the signature
# (a regulator's licence under "Прилог") is the act's annex: the signature block ends at the signatory's "с.р.".
SIGNATURE_ANY = re.compile(r"^(?:(?:УП\s?\d|О\.|У\.)\s*)?(?:[Бб]р\.|Број)\s*:?\s*\d")
ANNEX_START = re.compile(r"^(?:Прилог|ПРИЛОГ|Анекс|АНЕКС)(?:\s+(?:бр\.\s*)?\S{1,6})?\.?$")


def formula_inside(t: str) -> bool:
    """(new kinds) A paragraph that names the issuing body and then gives the enacting formula in the same sentence:
    "врз основа на член" within its first 400 characters, not a heading in capitals."""
    return t != t.upper() and bool(re.search(r"\bврз\s+основа\s+на\s+(?:член|точка|одредб|Устав)", t[:400]))


ARABIC_POINT = re.compile(r"^(\d{1,3})\.\s+\S")
ROMAN_POINT = re.compile(r"^([IVX]{1,6})\.\s+\S")
ROMAN = {r: i for i, r in enumerate(["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII",
                                     "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX"], 1)}


def point_units(body: list[str]) -> dict | None:
    """(new kinds, rule 6) An act made of numbered points ("1. ...", "2. ..." or "I. ...", "II. ...") instead of
    articles: one unit per point, when the text starts with point 1 (headings in capitals before it allowed), every
    point number from 1 to N starts exactly one paragraph and no other paragraph starts with such a number (so a list
    inside a point can never be taken for the next point). Headings in capitals between points ("II. ЗАВРШНИ
    ОДРЕДБИ") are heading units. None when the text is not so made."""
    first = next((j for j, t in enumerate(body) if t != t.upper() or not re.search(r"[А-Ш]", t)), None)
    if first is None:
        return None
    m_a, m_r = ARABIC_POINT.match(body[first]), ROMAN_POINT.match(body[first])
    if m_a and m_a.group(1) == "1":
        rx, num = ARABIC_POINT, int
    elif m_r and m_r.group(1) == "I":
        rx, num = ROMAN_POINT, ROMAN.get
    else:
        return None

    def is_heading(t: str) -> bool:
        return t == t.upper() and bool(re.search(r"[А-Ш]{3}", t)) and len(t) < 200

    starts = []
    for j, t in enumerate(body):
        m = rx.match(t)
        if m and not is_heading(t):
            n = num(m.group(1))
            if n is None:
                return None
            starts.append((n, j))
    nums = [n for n, _ in starts]
    if len(nums) < 2 or nums != list(range(1, len(nums) + 1)):
        return None
    units: dict = {}
    for k, t in enumerate(body[:first]):
        units[f"0-heading-{k + 1}"] = {"label": t, "kind": "heading", "text": f"# {t}\n"}
    for idx, (n, j) in enumerate(starts):
        end = starts[idx + 1][1] if idx + 1 < len(starts) else len(body)
        lines = body[j:end]
        heads = []
        while len(lines) > 1 and is_heading(lines[-1]) and idx + 1 < len(starts):
            heads.insert(0, lines.pop())  # a heading printed above the next point
        key = f"tocka-{n:03d}"
        units[key] = {"label": f"Точка {n}" if rx is ARABIC_POINT else f"Точка {m_r.group(1) if n == 1 else [r for r, v in ROMAN.items() if v == n][0]}",
                      "kind": "point", "text": "\n".join(lines) + "\n"}
        for h, t in enumerate(heads, 1):
            units[f"{key}-zz-heading-{h}"] = {"label": t, "kind": "heading", "text": f"# {t}\n"}
    return units


def sig_cut(lines: list[str], new: bool) -> tuple[int, list[str] | None]:
    """(index of the signature's first line, annex lines after the signature block or None); Hold no_signature."""
    rx = SIGNATURE_ANY if new else SIGNATURE
    cut = next((j for j in range(1, len(lines)) if rx.match(lines[j])), None)
    annex = None
    if new and cut is not None and sum(len(x) for x in lines[cut:]) > 400:
        end = next((k for k in range(cut, min(len(lines), cut + 8)) if re.search(r"с\.\s?р\.?$", lines[k])), None)
        if end is not None and end + 1 < len(lines) and ANNEX_START.match(lines[end + 1]) \
                and sum(len(x) for x in lines[cut:end + 1]) <= 400:
            annex = lines[end + 1:]
            del lines[end + 1:]
    if cut is None or sum(len(x) for x in lines[cut:]) > 400:
        raise Hold("no_signature")
    while cut > 1 and len(lines[cut - 1]) < 60 and not re.search(r"[.,;:“”\"]$", lines[cut - 1]):
        cut -= 1
    return cut, annex


def flat_split(paras: list[dict], db_title: str, word: str, new: bool = False) -> tuple[dict, str, None]:
    """(units, title, None) for an act with no numbered articles at all (a short instruction or order made of points
    or paragraphs): the enacting formula as the preamble and the whole text, up to the signature, as one unit.
    Raises Hold unless the text is clean: no stray characters, no words run together, no letters doubled by a font
    mapping (the Gazette's quotes extracted as letters)."""
    if word == "ЗАКОН":
        raise Hold("no_articles")  # a law always has articles: none found means the text did not extract cleanly
    if getattr(paras, "old_feet", 0) or any(p.get("foot") for p in paras):
        raise Hold("no_articles")
    texts = [p["text"] for p in paras]
    if any(re.match(r"^Член\s*\d", t) for t in texts):
        raise Hold("no_articles")  # articles there but not laid out as headings: not one text
    starter = title_start(word)
    start = next((i for i, t in enumerate(texts) if starter.match(t) and t == t.upper()), None)
    if start is None:
        raise Hold("no_title_line")
    k0 = next((j for j, t in enumerate(texts[:start]) if t.startswith(("Врз основа", "Согласно", "Врз основ"))
               or (new and formula_inside(t))), None)
    if k0 is None:
        raise Hold("no_enacting_formula")
    preamble = texts[k0:start]
    title = starter.sub(word + " ", texts[start], count=1).strip()
    k = start + 1
    while k < len(texts) and texts[k] == texts[k].upper() and not HEAD_WORDS.match(texts[k]) \
            and len(title) < len(db_title) - 2 and re.search(r"[А-Ш]", texts[k]):
        title += " " + texts[k]
        k += 1
    body = texts[k:]
    if new:
        cut, annex = sig_cut(body, True)
    else:
        annex = None
        cut = next((j for j in range(1, len(body)) if SIGNATURE.match(body[j])), None)
        if cut is None or sum(len(x) for x in body[cut:]) > 400:
            raise Hold("no_signature")
        while cut > 1 and len(body[cut - 1]) < 60 and not re.search(r"[.,;:“”\"]$", body[cut - 1]):
            cut -= 1
    body = body[:cut]
    if new and sum(len(x) for x in body) >= 100:
        pts = point_units(body)
        if pts is not None:
            if any(len(re.sub(r"^\S+\s*", "", u["text"]).strip()) < 3 for u in pts.values() if u["kind"] == "point"):
                raise Hold("empty_point")
            units = {"uvod": {"label": "Вовед", "kind": "preamble", "text": "\n".join(preamble) + "\n"}, **pts}
            if annex:
                units["prilog-01"] = {"label": annex[0], "kind": "annex", "text": "\n".join([f"# {annex[0]}"] + annex[1:]) + "\n"}
            return units, norm(title), None
    if annex:
        body = body + annex  # checked for cleanliness with the text, then split off below
    if sum(len(x) for x in body) < 100:
        raise Hold("flat_text_too_short")
    for name, rx in UNCLEAN:
        for t in [title] + preamble + body:
            if rx.search(t):
                raise Hold(f"flat_text_unclean:{name}:{rx.search(t).group(0)}")
    # a word split at a line end with its hyphen left in ("лабора-ториски", "си-те"); real compounds ("жиро-сметка")
    # cannot be told apart from these without a dictionary, so any hyphen between two lower-case letters holds the act
    for t in [title] + preamble + body:
        m = re.search(r"[" + CYR_LO + r"]-[" + CYR_LO + r"]", t)
        if m:
            raise Hold(f"flat_text_unclean:hyphen_in_word:{t[max(0, m.start() - 8):m.end() + 8]}")
    # a paragraph broken in the middle of a sentence: a line ending in a word or a comma and the next one going on in
    # lower case
    for a_, b_ in zip(preamble + body, (preamble + body)[1:]):
        if re.search(r"[" + CYR_LO + CYR_UP + r",]$", a_) and re.match(r"[" + CYR_LO + r"]", b_):
            raise Hold(f"flat_text_unclean:broken_paragraph:{a_[-20:]}")
    # forms and tables come out of the PDF as runs of short cells and column numbers, not as text
    if any(re.fullmatch(r"(?:\d+\s+){3,}\d+|_{3,}.*|.*_{5,}.*", t) for t in body) \
            or sum(1 for t in body if len(t) < 30) > max(2, len(body) // 5):
        raise Hold("flat_text_unclean:table_layout")
    if annex:
        body = body[:len(body) - len(annex)]
    units = {"uvod": {"label": "Вовед", "kind": "preamble", "text": "\n".join(preamble) + "\n"},
             "tekst": {"label": "Текст", "kind": "text", "text": "\n".join(body) + "\n"}}
    if annex:
        units["prilog-01"] = {"label": annex[0], "kind": "annex", "text": "\n".join([f"# {annex[0]}"] + annex[1:]) + "\n"}
    return units, norm(title), None


LINK_AMEND_WORDS = re.compile(
    r"се\s+(?:менува|менуваат|изменува|изменуваат|дополнува|дополнуваат|брише|бришат|заменува|заменуваат|додава|додаваат)"
    r"|изменување|дополнување|престанува|престануваат|престанаа|вон\s+сила|се\s+укинува|се\s+укинуваат"
    r"|не\s+се\s+применува", re.I)


def gazette_cite(issue: str, published: str) -> re.Pattern:
    y = published[:4]
    d = date.fromisoformat(published)
    month = [m for m, n in MONTHS.items() if n == d.month][0]
    return re.compile(r"(?<![\d/.])" + re.escape(issue) + r"\s*/\s*(?:" + y + "|" + y[2:] + r")(?!\d)"
                      r"|(?:број|бр\.)\s*" + re.escape(issue) + r"\s+од\s+(?:" + str(d.day) + r"\s+" + month + r"\s+" + y
                      + r"|0?" + str(d.day) + r"\.\s?0?" + str(d.month) + r"\.\s?" + y + r")")


def only_cites(src: Path, a: dict, lrec: dict | None, lid: int) -> bool:
    """True when a later act linked to act `a` is, by its title and text, an original act (not a correction, a court
    decision, a consolidated text, an amendment or a repeal) that cites the act's gazette number only in its enacting
    formula ("Врз основа на член 5 од Законот за ... (бр. 12/05) ... донесе ПРАВИЛНИК ЗА ..."), so it changes nothing
    in it. Any other case, or a text that cannot be read, is False."""
    if not lrec or lrec.get("version") != "Изворен текст":
        return False
    lt = norm(lrec.get("title") or "")
    if (lrec.get("kind") or "").startswith("Исправка") or lt.startswith("Исправка") or AMENDING.match(lt) \
            or COURT.search(lt) or re.search(r"пречистен|изменување|дополнување|престанување|укинување|вон\s+сила", lt, re.I):
        return False
    if lrec.get("kind") not in OLD_KINDS or lrec["kind"] == "Закон":
        return False  # a law's text has no enacting formula to cite from
    t = flat_text(src, lid)
    if t is None or len(t) < 300:
        return False
    if REPEAL.search(t) and cites_repeal(t, str(a["issue"]), dmy(a["published"])):
        return False
    hits = list(gazette_cite(str(a["issue"]), dmy(a["published"])).finditer(t))
    if not hits:
        return False
    word = OLD_KINDS[lrec["kind"]][1]
    tstart = re.search(r"\s?".join(word) + r"\s+(?:ЗА|НА)\s", t)
    if not tstart:
        return False
    f = re.search(r"Врз\s+основ|Согласно", t[:tstart.start()])
    if not f or f.start() >= 300 or LINK_AMEND_WORDS.search(t[:tstart.start()]):
        return False
    art = re.compile(r"Член\s*\d")
    for m in hits:
        if m.end() <= tstart.start():
            continue
        # cited in the act's own text: only where the same article, around the citation, says nothing of changing
        # or ending anything (a definition, a reference)
        s, e = max(tstart.start(), m.start() - 400), min(len(t), m.end() + 400)
        b = [x.end() for x in art.finditer(t, s, m.start())]
        s = b[-1] if b else s
        n = art.search(t, m.end(), e)
        e = n.start() if n else e
        if LINK_AMEND_WORDS.search(t[s:e]):
            return False
    return True


def flat_text(src: Path, i: int) -> str | None:
    """An act's whole PDF text on one line (running heads and page numbers left out), or None without a text file."""
    raw = src / "raw" / str(i // 1000) / f"{i}.json.gz"
    if not raw.exists():
        return None
    data = json.loads(gzip.decompress(raw.read_bytes()).decode("utf-8"))
    lines = sorted(data["lines"], key=lambda l: (l[0], round(l[4] / 3), l[2]))
    return norm(" ".join(l[-1] for l in lines if not RUNNING_HEAD.fullmatch(norm(l[-1]))))


def cites_repeal(text: str, issue: str, published: str) -> bool:
    """The text says an act ceases to be valid and cites, next to that, the gazette number of the act published in
    issue `issue` on `published` ("број 80/12", "бр. 80/2012", "број 142 од 13 ноември 2012 година")."""
    y = published[:4]
    d = date.fromisoformat(published)
    month = [m for m, n in MONTHS.items() if n == d.month][0]
    cite = re.compile(r"(?<![\d/.])" + re.escape(issue) + r"\s*/\s*(?:" + y + "|" + y[2:] + r")(?!\d)"
                      r"|(?:број|бр\.)\s*" + re.escape(issue) + r"\s+од\s+(?:" + str(d.day) + r"\s+" + month + r"\s+" + y
                      + r"|0?" + str(d.day) + r"\.\s?0?" + str(d.month) + r"\.\s?" + y + r")")
    reps = [(m.start(), m.end()) for m in REPEAL.finditer(text)]
    if not reps:
        return False
    # In the same article: the citation follows the phrase ("престанува да важи Законот за ... (број 14/06, 80/12)")
    # or just precedes it, with no article heading in between.
    art = re.compile(r"Член\s+\d")
    for m in cite.finditer(text):
        for s, e in reps:
            if 0 <= m.start() - e <= 1500 and not art.search(text, e, m.start()):
                return True
            if 0 <= s - m.end() <= 400 and not art.search(text, m.end(), s):
                return True
    return False


def gazette_name(pub: str) -> str:
    return "Службен весник на Република Северна Македонија" if pub >= "2019-02-12" else "Службен весник на Република Македонија"


def word_of(r: dict) -> str:
    """The heading word an act's text starts its title with: the kind's own for the seven first kinds, otherwise the
    first word of the record's title (Latin look-alike letters read as Cyrillic), in capitals."""
    if r.get("kind") in OLD_KINDS:
        return OLD_KINDS[r["kind"]][1]
    w = re.split(r"[\s(,]", norm(r.get("title") or "").translate(LOOKALIKE))[0]
    return re.sub(r"[^А-Яа-яЃЌЀЍѓќѐѝЈјЉљЊњЏџЅѕ]", "", w).upper() or "ОДЛУКА"


def kind_slug(kind: str) -> str:
    if kind in OLD_KINDS:
        return OLD_KINDS[kind][0]
    word = re.split(r"[\s(]", kind.strip().lower())[0]
    return re.sub(r"[^a-z]", "", "".join(LAT.get(c, c) for c in word)) or "akt"


def act_key(i: int, r: dict) -> str | None:
    """The law key (and, for an act published here, the act slug) of an LDBIS record: <kind>-<year>-sv<issue>-<id>."""
    pub = dmy(r.get("published"))
    if not pub or not str(r.get("issue") or "").isdigit():
        return None
    return f"{kind_slug(r.get('kind') or '')}-{int(pub[:4])}-sv{int(r['issue']):03d}-{i}"


def law_entry(i: int, r: dict, adopted: str | None) -> dict:
    title = norm(r.get("title") or "")
    pub = dmy(r["published"])
    if r.get("kind") in OLD_KINDS:
        lkind = OLD_KINDS[r["kind"]][2]
    elif COURT.search(title):
        lkind = "court"
    else:
        lkind = "law" if (r.get("kind") or "").endswith("акон") else "decree"
    return {"label": f"{title} („{gazette_name(pub)}“ бр. {int(r['issue'])}/{pub[:4]})", "title": title, "kind": lkind,
            "enacted": adopted, "published": pub, "url": PAGE_URL.format(id=i), "temporary_until": None,
            "restores": None, "note": None}


def subject(title: str) -> str:
    t = re.sub(r"\s+", " ", title.lower()).strip(" .")
    t = re.sub(r"\(пречистен текст\)", "", t).strip()
    return t


LOOKALIKE = str.maketrans("AaBEeKMHOoPpCcTXxYyJjIi", "АаВЕеКМНОоРрСсТХхУуЈјІі")


def same_title(a: str, b: str) -> bool:
    """The law's own title and the database's agree, up to case, Latin look-alike letters and small typos."""
    import difflib
    x = subject(a.translate(LOOKALIKE)).replace("ѓ", "г")
    y = subject(b.translate(LOOKALIKE)).replace("ѓ", "г")
    return x == y or difflib.SequenceMatcher(None, x, y).ratio() >= 0.93


def load(src: Path) -> dict[int, dict]:
    recs: dict[int, dict] = {}
    for f in sorted(src.glob("records*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue  # a row being written
            # 2026-11-09: LDBIS breaks some long titles over lines ("...организациона\r\nоспособеност ..."); a title
            # is one line (it becomes a commit trailer)
            r["title"] = re.sub(r"\s*[\r\n]+\s*", " ", r.get("title") or "")
            r["chain"] = [[re.sub(r"\s*[\r\n]+\s*", " ", c[0] or ""), *c[1:]] for c in r.get("chain") or []]
            recs[int(r["id"])] = r
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--retrieved", help="the day the inputs were retrieved (YYYY-MM-DD); default: the latest retrieval day "
                                         "recorded in the text files of the part's acts")
    ap.add_argument("--report")
    ap.add_argument("--out", help="folder for the parts (default corpus.d beside this script)")
    ap.add_argument("--cache", help="file to keep split results in between runs (same text file and parser = same result)")
    ap.add_argument("--rejects", help="JSON {id: reason}: acts held back by hand or by a failed read-back check")
    a = ap.parse_args()
    src = Path(a.src)
    recs = load(src)
    me = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    cache, new_cache = {}, {}
    if a.cache and Path(a.cache).exists():
        try:
            c = json.loads(gzip.decompress(Path(a.cache).read_bytes()).decode("utf-8"))
            cache = c["entries"] if c.get("parser") == me else {}
        except Exception:
            cache = {}
    rejects = json.loads(Path(a.rejects).read_text(encoding="utf-8")) if a.rejects and Path(a.rejects).exists() else {}
    corrections: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for i, r in recs.items():
        m = CORRECTION.match(r.get("title") or "")
        if m and r.get("published"):
            corrections[subject(m.group(1))].append((dmy(r["published"]) or "", i))
    laws = {i: r for i, r in recs.items() if r.get("kind") in SCOPE and (r.get("text") or {}).get("sha256")}
    parts: dict[tuple, dict] = defaultdict(lambda: {"laws": {}, "acts": []})
    holds = []
    # 2026-11-09: in-scope records whose LDBIS page serves no PDF are held with a row, not dropped silently.
    for i, r in sorted(recs.items()):
        if r.get("kind") in SCOPE and not (r.get("text") or {}).get("sha256"):
            p_ = dmy(r.get("published")) or ""
            holds.append({"id": i, "sv": f"{r.get('issue')}/{p_[:4]}", "title": norm(r.get("title") or "")[:160],
                          "reason": "no_text_on_ldbis"})

    def split_of(i: int, title: str) -> dict | None:
        """The split result of a law's text file (cached by file and title), or None without a text file."""
        raw = src / "raw" / str(i // 1000) / f"{i}.json.gz"
        if not raw.exists():
            return None
        st = raw.stat()
        ck = f"{i}:{st.st_size}:{st.st_mtime_ns}:{title}"
        hit = new_cache.get(ck) or cache.get(ck)
        if hit is None:
            data = json.loads(gzip.decompress(raw.read_bytes()).decode("utf-8"))
            try:
                if sum(len(x[-1]) for x in data["lines"]) < 200:
                    raise Hold("no_text_layer")
                paras = paragraphs(merge_rows(data["lines"]))
                try:
                    hit = {"ok": split(paras, title, word_of(recs[i]), recs[i].get("kind") not in OLD_KINDS), "meta": data["meta"]}
                except Hold as h:
                    if str(h) != "no_articles":
                        raise
                    # no numbered articles at all: the whole text as one unit, when it is clean
                    hit = {"ok": flat_split(paras, title, word_of(recs[i]), recs[i].get("kind") not in OLD_KINDS), "meta": data["meta"], "flat": True}
            except Hold as h:
                hit = {"hold": str(h)}
        new_cache[ck] = hit
        return hit

    def adopted_of(i: int) -> str | None:
        """The adoption day read from a law's text, when it is consistent with its publication (else None)."""
        r = recs[i]
        if r.get("kind") not in OLD_KINDS or not (r.get("text") or {}).get("sha256"):
            return None
        hit = split_of(i, norm(r.get("title") or ""))
        if not hit or "ok" not in hit:
            return None
        adopted, pub = hit["ok"][2], dmy(r["published"])
        if adopted and adopted <= pub <= (date.fromisoformat(adopted) + timedelta(days=60)).isoformat():
            return adopted
        return None

    def link_class(cid: int) -> str:
        """What a linked act is, by its LDBIS record: an official consolidated text, a correction, a Constitutional
        Court decision, an amendment (or repeal) of another act, or an original act (a bylaw, a replacing law)."""
        lr = recs.get(cid) or {}
        lt = norm(lr.get("title") or "")
        if "Пречистена" in (lr.get("version") or "") or re.search(r"пречистен\s+текст", lt, re.I):
            return "consolidated"
        if (lr.get("kind") or "").startswith("Исправка") or lt.startswith("Исправка"):
            return "correction"
        if COURT.search(lt):
            return "court"
        if lr.get("version") == "Текст измена/дополна" or AMENDING.match(lt) \
                or re.search(r"изменување|дополнување|престанување|укинување|вон\s+сила", lt, re.I):
            return "amending"
        return "other"

    def ref_of(cid: int) -> dict:
        lr = recs.get(cid) or {}
        pub_ = dmy(lr.get("published")) or ""
        t = norm(lr.get("title") or "")
        if pub_ and str(lr.get("issue") or "").isdigit():
            t += f" („{gazette_name(pub_)}“ бр. {int(lr['issue'])}/{pub_[:4]})"
        return {"ref": f"ldbis:{cid}", "title": t, "url": PAGE_URL.format(id=cid), "_key": (pub_, cid)}

    def marks_of(ids: list[int]) -> dict:
        """amended_by (amendments, repeals, court decisions) and cited_by (other later acts), oldest first."""
        am = sorted((ref_of(c) for c in set(ids) if link_class(c) in ("amending", "court")), key=lambda x: x["_key"])
        ci = sorted((ref_of(c) for c in set(ids) if link_class(c) == "other"), key=lambda x: x["_key"])
        for x in am + ci:
            x.pop("_key")
        return {"amended_by": am, "cited_by": ci}

    def in_force_of(cid: int) -> str | None:
        return dmy((recs.get(cid) or {}).get("in_force"))

    def repeal_of(i: int, r: dict, ended: str, later: list, amending: bool) -> tuple[str, int, bool]:
        """(end day, repealing act's id, whether its text names this act) for an act LDBIS marks no longer valid."""
        end = dmy(ended)
        if not end or not ended.endswith("Неактивен"):
            raise Hold("end_of_validity_scheduled:" + ended)
        e = date.fromisoformat(end)
        around = {(e - timedelta(days=1)).isoformat(), (e + timedelta(days=1)).isoformat()}
        on_day = [c for c in later if in_force_of(c[1]) == end]
        if not on_day:
            near = [c[1] for c in later if in_force_of(c[1]) in around]
            raise Hold(("end_of_validity_off_by_one:" if near else "end_of_validity_unexplained:") + end
                       + ("" if not near else " vs " + ",".join(map(str, near[:5]))))
        named = []
        for ct, cid, cday in on_day:
            t = flat_text(src, cid)
            if t is not None and cites_repeal(t, str(r["issue"]), dmy(r["published"])):
                named.append(cid)
        if len(named) == 1:
            rep, is_named = named[0], True
        elif len(on_day) == 1 and not named:
            if amending:
                # LDBIS also marks earlier amending acts no longer valid when a later amendment of the same act
                # takes effect: without a text that repeals it, the day says nothing about the amending act itself.
                raise Hold(f"end_of_validity_amending_not_named:{end} {on_day[0][1]}")
            rep, is_named = on_day[0][1], False
            rv = recs[rep]
            if rv.get("version") == "Пречистена верзија" or AMENDING.match(norm(rv.get("title") or "")):
                # an amendment or a consolidated text taking effect that day does not end the act
                raise Hold(f"end_of_validity_linked_act_amends:{end} {rep}")
            near = [c[1] for c in later if in_force_of(c[1]) in around]
            if near:
                raise Hold(f"end_of_validity_off_by_one:{end} vs " + ",".join(map(str, near[:5])))
        else:
            raise Hold("end_of_validity_repealer_ambiguous:" + ",".join(str(c[1]) for c in on_day[:5]))
        rr = recs[rep]
        rpub = dmy(rr.get("published"))
        if not rpub or rpub > end:
            raise Hold(f"end_of_validity_repealer_published_after:{rep}")
        t = flat_text(src, rep)
        if t is not None:
            own, _how = own_in_force({"art-000": {"text": t}}, rpub)
            if own and own != end:
                raise Hold(f"end_of_validity_repealer_own_clause:{rep} ldbis {end} vs own clause {own}")
        before = []
        if not amending:
            # A principal act: linked acts published before its end may have changed it in between. 2026-11-09
            # (Romania standard): published as enacted with its end, labelled with the acts that amended it, unless
            # an official consolidated text exists (then held until it is built as a version).
            before = [c[1] for c in later if c[1] != rep and c[2] < end]
            if any(link_class(c) == "consolidated" for c in before):
                raise Hold("later_consolidated_text:" + ",".join(str(c) for c in before if link_class(c) == "consolidated"))
            # 2026-11-11: a correction (Исправка) published before the end no longer holds the act: it is labelled
            # with it (in the main loop), as with amendments.
        return end, rep, is_named, before

    for i in sorted(laws):
        r = laws[i]
        title = norm(r.get("title") or "")
        pub = dmy(r.get("published"))
        repeal = None
        try:
            if str(i) in rejects:
                raise Hold("rejected:" + str(rejects[str(i)])[:200])
            if title.startswith("Исправка"):
                raise Hold("correction_record")
            if r.get("kind") in CONSTITUTION_KINDS:
                raise Hold("built_elsewhere:ustav (the Constitution and its amendments, built by hand from the official texts)")
            new_kind = r.get("kind") not in OLD_KINDS
            if new_kind and "Пречистена" in (r.get("version") or ""):
                raise Hold("consolidated_version_not_yet_placed")
            if not pub or not str(r.get("issue") or "").isdigit():
                raise Hold("no_gazette_issue_or_day")
            iss, year = int(r["issue"]), int(pub[:4])
            if (title, iss, year) in BUILT_ELSEWHERE:
                raise Hold("built_elsewhere")
            ended = (r.get("ended") or "").strip()
            amending = r.get("version") == "Текст измена/дополна" or bool(AMENDING.match(title))                 or (new_kind and bool(re.match(r"^\S+\s+за\s+(?:изменување|дополнување|изменувања|дополнувања)", title, re.I)))
            later = []
            for ct, ciss, cday, cid in r.get("chain") or []:
                if cid == i:
                    continue
                d = dmy(cday) or ""
                if d > pub or (d == pub and cid > i):
                    later.append((ct, cid, d))
            if ended and not ended.startswith(("Активен", "Нов")):
                repeal = repeal_of(i, r, ended, later, amending)
            cited_only = False
            marks = None
            if repeal is not None and repeal[3]:
                marks = marks_of(repeal[3])
            if not amending and later and repeal is None:
                if not all(only_cites(src, r, recs.get(c[1]), c[1]) for c in later):
                    # 2026-11-09 (Romania standard): no official consolidated text, so the act is published as
                    # enacted and labelled with the later acts (amended_by / cited_by), not held back. Where LDBIS
                    # links an official consolidated text, the act waits until that text is built as a version.
                    cons = [c[1] for c in later if link_class(c[1]) == "consolidated"]
                    if cons:
                        raise Hold("later_consolidated_text:" + ",".join(map(str, cons[:5])))
                    marks = marks_of([c[1] for c in later if not only_cites(src, r, recs.get(c[1]), c[1])])
                else:
                    cited_only = True  # every later linked act only cites it (bylaws made under it)
            if amending:
                bad = [c for ct, c, _ in later if COURT.search(ct) or re.search(r"на\s+Законот\s+за\s+(?:изменување|дополнување)", ct)]
                if bad:
                    # 2026-11-09 (Romania standard): an amending act later amended or ruled on: published as
                    # enacted, labelled with those acts. A correction of it (Исправка) or a consolidated text still
                    # holds it.
                    hit_ = [c for c in bad if link_class(c) == "consolidated"]
                    if hit_:
                        raise Hold("later_consolidated_text:" + ",".join(map(str, hit_[:5])))
                    marks = marks_of(bad)
            # 2026-11-11 (Romania standard): a correction (Исправка) of the act no longer holds it back: the act is
            # published as published and labelled with the correction ("corrected, not consolidated", or "amended,
            # not consolidated" when amendments are there too; amended_by lists both). A correction is the act's when
            # LDBIS links it to the act, or when its title names the act's title and its text cites the act's gazette
            # number; one whose text cites only other numbers is another act's (same title); one citing no number
            # at all cannot be placed and holds the act.
            corr_ids = [c[1] for c in later if link_class(c[1]) == "correction"]
            for d, cid in corrections.get(subject(title), []):
                if d < pub or cid in corr_ids:
                    continue
                ct_ = flat_text(src, cid)
                if ct_ is not None and gazette_cite(str(r["issue"]), pub).search(ct_):
                    corr_ids.append(cid)
                elif ct_ is not None and re.search(r"(?<![\d/.])\d{1,3}\s*/\s*(?:19|20)?\d{2}(?!\d)|(?:број|бр\.)\s*\d{1,3}\s+од\s+\d", ct_):
                    continue
                else:
                    raise Hold(f"correction_unplaced:{cid}")
            if new_kind:
                kslug, word = kind_slug(r["kind"]), word_of(r)
                lkind = "court" if COURT.search(title) else ("law" if r["kind"].endswith("акон") else "decree")
                in_force_rx = IN_FORCE_ANY
            else:
                kslug, word, lkind = KINDS[r["kind"]]
                in_force_rx = IN_FORCE
            hit = split_of(i, title)
            if hit is None:
                raise Hold("no_text_file")
            if "hold" in hit:
                raise Hold(hit["hold"])
            data = {"meta": hit["meta"]}
            units, text_title, adopted = hit["ok"]
            if not same_title(text_title, title):
                raise Hold("title_mismatch:" + text_title[:100])
            day = dmy(r.get("in_force"))
            own, how = own_in_force(units, pub, in_force_rx)
            if not day:
                raise Hold("in_force_missing")
            if own and own != day:
                raise Hold(f"in_force_mismatch:ldbis {day} vs own clause {own}")
            if hit.get("flat"):
                # one text with no articles: its own in-force clause must be there and agree, which also shows the
                # text runs to its end
                own, how = own_in_force({"art-000": {"text": "".join(u["text"] for k, u in units.items()
                                                                     if u["kind"] in ("text", "point"))}}, pub, in_force_rx)
                if not own:
                    raise Hold("flat_text_no_own_clause:" + how[:80])
                if own != day:
                    raise Hold(f"in_force_mismatch:ldbis {day} vs own clause {own}")
            if day < pub:
                raise Hold(f"in_force_before_publication:{day}")
            if adopted and not (adopted <= pub <= (date.fromisoformat(adopted) + timedelta(days=60)).isoformat()):
                raise Hold(f"adoption_day_inconsistent:{adopted}")
            if repeal:
                if repeal[0] <= day:
                    raise Hold(f"end_of_validity_not_after_in_force:{repeal[0]}")
                ret = data["meta"].get("retrieved") or a.retrieved
                if not ret or repeal[0] > ret:
                    raise Hold(f"end_of_validity_after_retrieval:{repeal[0]}")
        except Hold as h:
            holds.append({"id": i, "sv": f"{r.get('issue')}/{(pub or '')[:4]}", "title": title[:160], "reason": str(h)})
            continue
        slug = f"{kslug}-{year}-sv{iss:03d}-{i}"
        pk = (40, "zakoni") if kslug == "zakon" else (50, kslug)
        if hit.get("flat") or cited_only:
            pk = (60, kslug)  # released by the one-text or the citing-acts rule: parts of their own
        gaz = "Службен весник на Република Северна Македонија" if pub >= "2019-02-12" else "Службен весник на Република Македонија"
        label = f"{title} („{gaz}“ бр. {iss}/{year})"
        applies = dmy(r.get("applies"))
        note = None
        if applies and applies != day:
            note = f"По базата LDBIS, законот почнува да се применува на {applies}."
        parts[(pk, year)]["laws"][slug] = {"label": label, "title": title, "kind": lkind, "enacted": adopted, "published": pub,
                                     "url": PAGE_URL.format(id=i), "temporary_until": None, "restores": None, "note": None}
        parts[(pk, year)].setdefault("_retrieved", set()).add(data["meta"].get("retrieved") or a.retrieved)
        versions = [{"from": day, "source_url": PAGE_URL.format(id=i), "sha256": data["meta"]["sha256"],
                     "units": units, "credits": {}, **({"note": note} if note else {})}]
        if repeal:
            end, rep, is_named, _before = repeal
            rkey = act_key(rep, recs[rep]) if is_named else None
            if rkey:
                parts[(pk, year)]["laws"].setdefault(rkey, law_entry(rep, recs[rep], adopted_of(rep)))
                versions.append({"from": end, "source_url": PAGE_URL.format(id=rep),
                                 **({"sha256": recs[rep]["text"]["sha256"]} if (recs[rep].get("text") or {}).get("sha256") else {}),
                                 "units": {}, "credits": {k: [rkey] for k in units}})
            else:
                versions.append({"from": end, "source_url": PAGE_URL.format(id=i), "units": {}, "credits": {},
                                 "note": "По базата LDBIS, актот престанува да важи на овој ден; прописот со кој "
                                         "престанува да важи не е утврден."})
        act = {"slug": slug, "short": title, "title": title, "url": PAGE_URL.format(id=i), "first_law": slug,
               "earlier_laws": [], "versions": versions}
        if corr_ids:
            marks = marks or {"amended_by": [], "cited_by": []}
            am = [x for x in marks["amended_by"] if x["ref"] not in {f"ldbis:{c}" for c in corr_ids}]
            cr = sorted((ref_of(c) for c in set(corr_ids)), key=lambda x: x["_key"])
            for x in cr:
                x.pop("_key")
            marks = {"amended_by": sorted(am + cr, key=lambda x: (dmy((recs.get(int(x["ref"][6:])) or {}).get("published")) or "", int(x["ref"][6:]))),
                     "cited_by": marks["cited_by"]}
            act["label"] = "amended, not consolidated" if am else "corrected, not consolidated"
            act.update({k: v for k, v in marks.items() if v})
        elif marks and (marks["amended_by"] or marks["cited_by"]):
            act["label"] = "amended, not consolidated" if marks["amended_by"] else "cited later, not consolidated"
            act.update({k: v for k, v in marks.items() if v})
        parts[(pk, year)]["acts"].append(act)
    parts_dir = Path(a.out) if a.out else PARTS
    parts_dir.mkdir(exist_ok=True)
    written = []
    for pk, year in sorted(parts):
        p = parts[(pk, year)]
        p["acts"].sort(key=lambda x: x["slug"])
        ret = p.pop("_retrieved", set())
        p["sources"] = [{"name": "ЛДБИС: база на закони и прописи (Министерство за правда, ldbis.pravda.gov.mk)",
                         "url": "https://ldbis.pravda.gov.mk/", "retrieved": a.retrieved or max(x for x in ret if x),
                         "licence": "Official texts, not protected by copyright (Закон за авторското право и сродните права)",
                         "notes": "Each law's record (gazette issue, publication, in-force and application days, linked acts) "
                                  "and its text as published in Службен весник (PDF)."}]
        p["decisions"] = [
            "A law is published here as published in Службен весник, from the text the Ministry of Justice's database "
            "(LDBIS) serves for it; the promulgation decree (Указ) above it is not part of the law.",
            "Its one version takes effect on the in-force day LDBIS records; where the law's own final article names its "
            "day, the two must agree or the law is held back. A different application day is noted on the version.",
            "A law LDBIS links to later acts (amendments, court decisions, replacing or implementing acts) has no "
            "official consolidated text: it is published as enacted and labelled \"amended, not consolidated\" "
            "(amended_by: the amending acts and court decisions) or \"cited later, not consolidated\" (cited_by), as "
            "is an amending law that a later act amends or a Constitutional Court decision follows. A law LDBIS links "
            "to an official consolidated text (Пречистен текст) is held until that text is built as a version; a law "
            "a correction names is held.",
            "A law LDBIS marks no longer valid gets a second version with no text, from its end-of-validity day, which "
            "LDBIS records as the day the repealing act enters into force. It is published only when a linked act "
            "enters into force on that very day (by LDBIS, and by its own final article where that can be read), no "
            "other linked act enters into force the day before or after; a principal law that other linked acts "
            "amended before that day keeps its text as enacted until then and is labelled with them. The removal is credited to that act when its text says the law ceases to "
            "be valid and cites its gazette number in the same article; otherwise to the publisher, as unexplained. An "
            "amending law gets this version only when such a text names it.",
            "Paragraphs follow the PDF's layout (a new block, an indented first line or a centred line starts one); a "
            "centred line just above an article is its caption, other centred lines are headings."]
        if pk[0] == 60:
            p["decisions"] += [
                "An act with no numbered articles at all (an instruction or order made of points or paragraphs) is one "
                "unit, the whole text from its title to its signature, with the enacting formula as the preamble. It is "
                "published only when its own final provision names its in-force day and that agrees with LDBIS, and the "
                "text is clean: no characters outside the alphabets and ordinary punctuation, no words run together, no "
                "letters doubled by the PDF's font mapping, no Latin and Cyrillic letters in one word.",
                "A principal act that LDBIS links to later acts is published as published only when every one of them is "
                "an original act (not an amendment, consolidated text, correction, court decision or repeal, by its title) "
                "whose text cites the act's gazette number only in its enacting formula or, in its own text, where the "
                "article says nothing of amending, replacing or ending anything: a bylaw made under the act, which "
                "changes nothing in it."]
        p["ai_use"] = ["none"]
        f = parts_dir / f"{pk[0]}-{pk[1]}-{year}.json"
        body = json.dumps(p, ensure_ascii=False, indent=1) + "\n"
        if not f.exists() or f.read_text(encoding="utf-8") != body:
            tmp = f.with_suffix(".tmp")
            tmp.write_text(body, encoding="utf-8", newline="\n")
            tmp.replace(f)
        pub_f = f.with_suffix(".PUBLISH")
        if pk[0] == 60 and not pub_f.exists():
            pub_f.write_text("Parsed from LDBIS records and the Official Gazette text of each act; one version, as "
                             "published; acts with no numbered articles as one clean text whose own in-force clause "
                             "agrees with LDBIS; acts whose later linked acts only cite them in their enacting formula "
                             "(checked in the linked acts' titles and texts) (parse_acts.py).\n",
                             encoding="utf-8", newline="\n")
        elif not pub_f.exists():
            pub_f.write_text("Parsed from LDBIS records and the Official Gazette text of each law; one version, as "
                             "published; in-force day from LDBIS checked against the law's own clause; laws no longer "
                             "valid end with an empty version on the repealing act's in-force day; held back: laws "
                             "with later linked acts, corrections or an unexplained end of validity (parse_acts.py).\n",
                             encoding="utf-8", newline="\n")
        written.append((f.name, len(p["acts"])))
    # Parts of years that no longer hold any act (all held back now) are removed, so a stale part is never read.
    keep = {n for n, _ in written}
    for old in [*parts_dir.glob("40-zakoni-*.json"), *parts_dir.glob("50-*.json"), *parts_dir.glob("60-*.json")]:
        if old.name not in keep:
            old.unlink()
            old.with_suffix(".PUBLISH").unlink(missing_ok=True)
    if a.cache:
        tmp = Path(a.cache + ".tmp")
        tmp.write_bytes(gzip.compress(json.dumps({"parser": me, "entries": new_cache}, ensure_ascii=False).encode("utf-8"), 1))
        tmp.replace(a.cache)
    if a.report:
        Path(a.report).write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in holds), encoding="utf-8")
    reasons = defaultdict(int)
    for h in holds:
        reasons[h["reason"].split(":")[0]] += 1
    print(json.dumps({"laws_seen": len(laws), "published": sum(n for _, n in written), "held": len(holds),
                      "held_by_reason": dict(reasons), "parts": written}, ensure_ascii=False))


if __name__ == "__main__":
    main()
