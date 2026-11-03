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

An act is held back, not published (the reason is written to the report), when:
- it is a principal law and the database links a later act to it (an amendment, a consolidated text, a court
  decision, a replacing law): its text today may differ from the text as published;
- it is an amending law and a later linked act amends that amending law, or a Constitutional Court decision follows it;
- a correction (Исправка) names it;
- the database gives it an end-of-validity day (repealed or temporary);
- its in-force day is missing, or its own final clause names a different day;
- its text does not split cleanly into articles, or the PDF has no usable text layer.
"""
from __future__ import annotations

import argparse
import gzip
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
ORD = {"првиот": 1, "вториот": 2, "третиот": 3, "четвртиот": 4, "петтиот": 5, "шестиот": 6, "седмиот": 7, "осмиот": 8,
       "деветтиот": 9, "десеттиот": 10, "петнаесеттиот": 15, "дваесеттиот": 20, "триесеттиот": 30}
LAT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ѓ": "gj", "е": "e", "ж": "zh", "з": "z", "ѕ": "dz", "и": "i",
       "ј": "j", "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n", "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s",
       "т": "t", "ќ": "kj", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "џ": "dzh", "ш": "sh"}
ART = re.compile(r"^Член\s+(\d+)\s*(?:-?\s*([а-ш]{1,2}))?\s*\.?$")
HEAD_WORDS = re.compile(r"^(?:Глава|ГЛАВА|Дел|ДЕЛ|Оддел|ОДДЕЛ|Поглавје|ПОГЛАВЈЕ|Подглава|[IVXLC]+\.?\s)")
ANNEX = re.compile(r"^(?:Прилог|ПРИЛОГ)(?:\s+(?:бр\.\s*)?\S{1,6})?\.?$")
TITLE_START = re.compile(r"^З\s?А\s?К\s?О\s?Н(?:\s+|$)")
PAGE_NO = re.compile(r"^\s*\d+\s+од\s+\d+\s*$")
OPEN_Q, CLOSE_Q = "„", "“”"
IN_FORCE = re.compile(r"(?:Овој\s+закон|Законот)\s+влегува\s+во\s+сила\s+([^.]*?)(?:\.|,\s*а\s|$)", re.S)
AMENDING = re.compile(r"^Закон\s+за\s+(?:изменување|дополнување|изменувања|дополнувања)", re.I)
COURT = re.compile(r"Уставен\s+суд|Уставниот\s+суд", re.I)
CORRECTION = re.compile(r"^Исправка\s+на\s+(.+)$")
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
    feet = [r for r in rows if r["size"] < size - 1.2]  # footnotes (printed smaller), kept apart
    rows = [r for r in rows if r["size"] >= size - 1.2]
    left = statistics.median(r["x0"] for r in rows)
    lefts = [r["x0"] for r in rows if abs(r["x0"] - left) < 25]
    left = min(lefts) if lefts else left
    right = max(statistics.median(r["x1"] for r in rows), max(r["x1"] for r in rows) - 2)
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
        if out and out[-1].get("foot") and not re.match(r"^\(?\*+\)?|^\d{1,2}\)?\s", f["text"]):
            out[-1]["text"] += " " + f["text"]
        else:
            out.append({"text": f["text"], "centred": False, "bold": False, "page": f["page"], "block": f["block"], "foot": True})
    for p in out:
        p["text"] = norm(p["text"])
    return out


def suffix_key(s: str) -> str:
    return "".join(LAT.get(c, c) for c in s)


def split(paras: list[dict], db_title: str = "") -> tuple[dict, str, str | None]:
    """(units, title, adoption day). The law starts at its upper-case title after the decree."""
    feet = [p["text"] for p in paras if p.get("foot")]
    paras = [p for p in paras if not p.get("foot")]
    texts = [p["text"] for p in paras]
    first_art = next((i for i, p in enumerate(paras) if p["centred"] and ART.match(p["text"])), None)
    if first_art is None:
        raise Hold("no_articles")
    start = None
    for i in range(first_art - 1, -1, -1):
        t = texts[i]
        if TITLE_START.match(t) and t == t.upper():
            start = i
            break
    if start is None:
        raise Hold("no_title_line")
    if not any(re.match(r"^У\s*К\s*А\s*З", t) for t in texts[:start]):
        raise Hold("no_decree")
    decree = " ".join(texts[:start])
    m = re.search(r"го\s+донесе\s+на\s+седницата\s+одржана\s+на\s+(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", decree)
    adopted = date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).isoformat() if m else None
    title = TITLE_START.sub("ЗАКОН ", texts[start], count=1).strip()
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
    pre: list[str] = []

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
        elif depth == 0 and p["centred"] and len(t) < 250 and (cur is not None or not pre):
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


def own_in_force(units: dict, published: str) -> tuple[str | None, str]:
    text = " ".join(u["text"].replace("\n", " ") for k, u in units.items() if k.startswith("art-"))
    text = re.sub(r"„[^“”]{40,}?[“”]", " ", text)  # provisions quoted into another act
    clauses = [m.group(1) for m in IN_FORCE.finditer(text)]
    if not clauses:
        return None, "none"
    c = clauses[-1].strip()
    p = date.fromisoformat(published)
    if re.match(r"^со\s+денот\s+на\s+(?:неговото\s+)?објавувањето", c):
        return published, "publication"
    m = re.match(r"^(" + "|".join(ORD) + r")\s+ден\s+од\s+денот\s+на\s+(?:неговото\s+)?објавувањето", c)
    if m:
        return (p + timedelta(days=ORD[m.group(1)])).isoformat(), "days"
    m = re.match(r"^(?:на|со)\s+(?:денот\s+)?(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", c)
    if m:
        return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).isoformat(), "date"
    return None, "unreadable:" + c[:100]


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
            recs[int(r["id"])] = r
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--retrieved", help="the day the inputs were retrieved (YYYY-MM-DD); default: the latest retrieval day "
                                         "recorded in the text files of the part's acts")
    ap.add_argument("--report")
    ap.add_argument("--out", help="folder for the parts (default corpus.d beside this script)")
    ap.add_argument("--rejects", help="JSON {id: reason}: acts held back by hand or by a failed read-back check")
    a = ap.parse_args()
    src = Path(a.src)
    recs = load(src)
    rejects = json.loads(Path(a.rejects).read_text(encoding="utf-8")) if a.rejects and Path(a.rejects).exists() else {}
    corrections: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for i, r in recs.items():
        m = CORRECTION.match(r.get("title") or "")
        if m and r.get("published"):
            corrections[subject(m.group(1))].append((dmy(r["published"]) or "", i))
    laws = {i: r for i, r in recs.items() if r.get("kind") == "Закон" and (r.get("text") or {}).get("sha256")}
    parts: dict[int, dict] = defaultdict(lambda: {"laws": {}, "acts": []})
    holds = []
    for i in sorted(laws):
        r = laws[i]
        title = norm(r.get("title") or "")
        pub = dmy(r.get("published"))
        try:
            if str(i) in rejects:
                raise Hold("rejected:" + str(rejects[str(i)])[:200])
            if title.startswith("Исправка"):
                raise Hold("correction_record")
            if not pub or not str(r.get("issue") or "").isdigit():
                raise Hold("no_gazette_issue_or_day")
            iss, year = int(r["issue"]), int(pub[:4])
            if (title, iss, year) in BUILT_ELSEWHERE:
                raise Hold("built_elsewhere")
            ended = (r.get("ended") or "").strip()
            if ended and not ended.startswith(("Активен", "Нов")):
                raise Hold("end_of_validity:" + ended)
            amending = r.get("version") == "Текст измена/дополна" or bool(AMENDING.match(title))
            later = []
            for ct, ciss, cday, cid in r.get("chain") or []:
                if cid == i:
                    continue
                d = dmy(cday) or ""
                if d > pub or (d == pub and cid > i):
                    later.append((ct, cid))
            if not amending and later:
                raise Hold("later_linked_act:" + ",".join(str(c) for _, c in later[:5]))
            if amending:
                bad = [c for ct, c in later if COURT.search(ct) or re.search(r"на\s+Законот\s+за\s+(?:изменување|дополнување)", ct)]
                if bad:
                    raise Hold("amending_law_amended_or_ruled:" + ",".join(map(str, bad[:5])))
            for d, cid in corrections.get(subject(title), []):
                if d >= pub:
                    raise Hold(f"correction:{cid}")
            raw = src / "raw" / str(i // 1000) / f"{i}.json.gz"
            if not raw.exists():
                raise Hold("no_text_file")
            data = json.loads(gzip.decompress(raw.read_bytes()).decode("utf-8"))
            if sum(len(x[-1]) for x in data["lines"]) < 200:
                raise Hold("no_text_layer")
            units, text_title, adopted = split(paragraphs(merge_rows(data["lines"])), title)
            if not same_title(text_title, title):
                raise Hold("title_mismatch:" + text_title[:100])
            day = dmy(r.get("in_force"))
            own, how = own_in_force(units, pub)
            if not day:
                raise Hold("in_force_missing")
            if own and own != day:
                raise Hold(f"in_force_mismatch:ldbis {day} vs own clause {own}")
            if day < pub:
                raise Hold(f"in_force_before_publication:{day}")
            if adopted and not (adopted <= pub <= (date.fromisoformat(adopted) + timedelta(days=60)).isoformat()):
                raise Hold(f"adoption_day_inconsistent:{adopted}")
        except Hold as h:
            holds.append({"id": i, "sv": f"{r.get('issue')}/{(pub or '')[:4]}", "title": title[:160], "reason": str(h)})
            continue
        slug = f"zakon-{year}-sv{iss:03d}-{i}"
        gaz = "Службен весник на Република Северна Македонија" if pub >= "2019-02-12" else "Службен весник на Република Македонија"
        label = f"{title} („{gaz}“ бр. {iss}/{year})"
        applies = dmy(r.get("applies"))
        note = None
        if applies and applies != day:
            note = f"По базата LDBIS, законот почнува да се применува на {applies}."
        parts[year]["laws"][slug] = {"label": label, "title": title, "kind": "law", "enacted": adopted, "published": pub,
                                     "url": PAGE_URL.format(id=i), "temporary_until": None, "restores": None, "note": None}
        parts[year].setdefault("_retrieved", set()).add(data["meta"].get("retrieved") or a.retrieved)
        parts[year]["acts"].append({
            "slug": slug, "short": title, "title": title, "url": PAGE_URL.format(id=i), "first_law": slug,
            "earlier_laws": [],
            "versions": [{"from": day, "source_url": PAGE_URL.format(id=i), "sha256": data["meta"]["sha256"],
                          "units": units, "credits": {}, **({"note": note} if note else {})}]})
    parts_dir = Path(a.out) if a.out else PARTS
    parts_dir.mkdir(exist_ok=True)
    written = []
    for year in sorted(parts):
        p = parts[year]
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
            "A principal law is published only when LDBIS links no later act to it (no amendment, consolidated text, "
            "court decision or replacing law); an amending law only when no later act amends it and no Constitutional "
            "Court decision follows it; never when a correction names it or LDBIS records an end of validity.",
            "Paragraphs follow the PDF's layout (a new block, an indented first line or a centred line starts one); a "
            "centred line just above an article is its caption, other centred lines are headings."]
        p["ai_use"] = ["none"]
        f = parts_dir / f"40-zakoni-{year}.json"
        body = json.dumps(p, ensure_ascii=False, indent=1) + "\n"
        if not f.exists() or f.read_text(encoding="utf-8") != body:
            tmp = f.with_suffix(".tmp")
            tmp.write_text(body, encoding="utf-8", newline="\n")
            tmp.replace(f)
        pub_f = f.with_suffix(".PUBLISH")
        if not pub_f.exists():
            pub_f.write_text("Parsed from LDBIS records and the Official Gazette text of each law; one version, as "
                             "published; in-force day from LDBIS checked against the law's own clause; held back: laws "
                             "with later linked acts, corrections or an end of validity (parse_acts.py).\n",
                             encoding="utf-8", newline="\n")
        written.append((f.name, len(p["acts"])))
    if a.report:
        Path(a.report).write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in holds), encoding="utf-8")
    reasons = defaultdict(int)
    for h in holds:
        reasons[h["reason"].split(":")[0]] += 1
    print(json.dumps({"laws_seen": len(laws), "published": sum(n for _, n in written), "held": len(holds),
                      "held_by_reason": dict(reasons), "parts": written}, ensure_ascii=False))


if __name__ == "__main__":
    main()
