"""Build corpus.json for North Macedonia from the cache only (run fetch.py first; no network here)."""
import hashlib, json, os, re, sys
import fetch, pdftext, ustav, ustav_replay, zzlp

HERE = os.path.dirname(os.path.abspath(__file__))
MONTHS = {"јануари": 1, "февруари": 2, "март": 3, "април": 4, "мај": 5, "јуни": 6, "јули": 7, "август": 8,
          "септември": 9, "октомври": 10, "ноември": 11, "декември": 12}
fetch.get = (lambda orig: (lambda url, **kw: _cached(url, orig)))(fetch.get)


def _cached(url, orig):
    p = fetch.path_for(url)
    if not os.path.exists(p):
        raise SystemExit(f"not in cache (run fetch.py): {url}")
    return p


def sha(url):
    return hashlib.sha256(open(fetch.path_for(url), "rb").read()).hexdigest()


def issue_date(fn, number):
    """Publication date printed on the issue ('Број 52 ... 22 ноември 1991')."""
    import fitz
    d = fitz.open(fetch.get(ustav.GZ % fn))
    t = " ".join(d[i].get_text() for i in range(min(2, d.page_count)))
    t = re.sub(r"\s+", " ", t)
    for m in re.finditer(r"(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})", t):
        return f"{int(m.group(3)):04d}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    raise ValueError(f"no date in issue {number}")


ROMAN_OF = {v: k for k, v in ustav.ROMAN.items()}
# amendment -> (gazette issue, promulgated on, in force)
GROUPS = [
    ([1, 2], "1/1992", "1992-01-06", "1992-01-06"),
    ([3], "31/1998", "1998-07-01", "1998-07-01"),
    (list(range(4, 19)), "91/2001", "2001-11-16", "2001-11-16"),
    ([19], "84/2003", "2003-12-26", "2003-12-26"),
    (list(range(20, 31)), "107/2005", "2005-12-07", "2005-12-07"),
    ([31], "3/2009", "2009-01-09", "2009-01-09"),
    ([32], "49/2011", "2011-04-12", "2011-04-12"),
    ([33, 34, 35, 36], "6/2019", "2019-01-11", "2019-02-12"),
]


def build_ustav(log):
    paras, _ = ustav.assembly_paragraphs(log)
    pre, order, arts, heads, amends_a = ustav.parse_base(paras, log)
    vocab = {w.lower().strip(".,;:„“\"()") for ps in list(arts.values()) + [pre] for p in ps for w in p.split()}
    amends = {k: v for k, v in amends_a.items() if k <= 18}
    for issue in ["84/2003", "107/2005", "3/2009", "49/2011"]:
        fn, pages = ustav.ISSUES[issue]
        g = ustav.gazette_amendments(fn, pages, vocab, log)
        for k, v in g.items():
            # cross-check against the Assembly copy, word for word (keys ignore punctuation and case)
            ka = [pdftext.key(w) for p in amends_a.get(k, []) for w in p.split() if pdftext.key(w)]
            kg = [pdftext.key(w) for p in v for w in p.split() if pdftext.key(w)]
            if ka != kg:
                import difflib
                dif = [d for d in difflib.ndiff(ka, kg) if d[0] in "+-"]
                log.append(f"ustav: amendment {ROMAN_OF[k]} gazette vs Assembly copy word differences: {' '.join(dif)[:300]}")
            amends[k] = v
    g = ustav.gazette_amendments(ustav.G2019[1], [1, 2], vocab, log)
    amends.update(g)
    missing = [k for k in range(1, 37) if k not in amends]
    if missing:
        raise ValueError(f"amendments missing: {missing}")

    laws = {"ustav-1991": {"label": "Устав (1991)", "title": "Устав на Република Македонија", "kind": "law",
                           "enacted": "1991-11-17", "published": issue_date(ustav.ISSUES["52/1991"][0], "52/1991"),
                           "url": ustav.GZ % ustav.ISSUES["52/1991"][0], "temporary_until": None, "restores": None,
                           "note": None}}
    rp = ustav_replay.Replay(pre, arts, heads, order)
    versions = []
    srcs = [ustav.ASSEMBLY, ustav.GZ % ustav.ISSUES["52/1991"][0]]
    prev_units = rp.units()
    versions.append({"from": "1991-11-17", "source_url": ustav.ASSEMBLY, "sha256": sha(ustav.ASSEMBLY),
                     "units": prev_units, "credits": {},
                     "note": "Words from the Assembly's certified copy; paragraph breaks from Службен весник 52/1991."})
    for ams, issue, promulgated, in_force in GROUPS:
        fn = ustav.G2019[1] if issue == "6/2019" else ustav.ISSUES[issue][0]
        published = issue_date(fn, issue)
        credits = {}
        order_ams = [k for k in ustav_replay.OPS if k in ams]
        for am in order_ams:
            key = f"amandman-{am:02d}"
            laws[key] = {"label": f"Амандман {ROMAN_OF[am]}", "title": f"Амандман {ROMAN_OF[am]} на Уставот на Република Македонија",
                         "kind": "law", "enacted": promulgated, "published": published, "url": ustav.GZ % fn,
                         "temporary_until": None, "restores": None, "note": None}
            pts = ustav.split_points(amends[am])
            instr = pts[max(pts)][0]
            if not instr.startswith("Со ") and am != 33:
                raise ValueError(f"amendment {am}: last point is not an instruction: {instr[:80]}")
            before = rp.units()
            rp.apply(am, pts, instr)
            after = rp.units()
            for k in set(before) | set(after):
                if before.get(k, {}).get("text") != after.get(k, {}).get("text"):
                    credits.setdefault(k, []).append(key)
        units = rp.units()
        src = ustav.GZ % fn
        versions.append({"from": in_force, "source_url": src if am >= 19 else ustav.ASSEMBLY,
                         "sha256": sha(src if am >= 19 else ustav.ASSEMBLY), "units": units,
                         "credits": {k: v for k, v in sorted(credits.items())},
                         "note": f"Amendments {', '.join(ROMAN_OF[a] for a in ams)}, Службен весник {issue} ({published})."})
    log.extend(rp.log)
    act = {"slug": "ustav", "short": "Устав", "title": "Устав на Република Северна Македонија",
           "url": "https://www.sobranie.mk/ustav-na-rm.nspx", "first_law": "ustav-1991", "earlier_laws": [],
           "versions": versions}
    return act, laws


def main():
    log = []
    act, laws = build_ustav(log)
    act2, laws2 = zzlp.build(log, sha)
    laws.update(laws2)
    reqs = [json.loads(l) for l in open(os.path.join(fetch.CACHE, "requests.jsonl"), encoding="utf-8")]
    n_sl = sum(1 for r in reqs if "slvesnik" in r["url"])
    n_sob = sum(1 for r in reqs if "sobranie" in r["url"])
    corpus = {
        "country": {
            "code": "MK", "name": "North Macedonia", "slug": "north-macedonia", "lang": "mk", "tz": "Europe/Skopje",
            "future_branch": "idno",
            "publisher": {"name": "Службен весник на Република Северна Македонија", "email": "slvesnik@slvesnik.invalid"},
            "default_in_force_rule": {
                "text": "Устав чл. 52 ст. 4: a law takes effect no earlier than the eighth day after its publication in the Official Gazette, unless for especially justified reasons it sets an earlier day; each act's own final article states its day. The Constitution and its amendments state their own day.",
                "source": "https://www.sobranie.mk/ustav-na-rm.nspx"},
            "licence_note": "Laws and other official texts are not protected by copyright in North Macedonia (Закон за авторското право и сродните права). Text from Службен весник на Република Северна Македонија (slvesnik.com.mk, free issues) and the Assembly of the Republic of North Macedonia (sobranie.mk)."
        },
        "sources": [
            {"name": "Службен весник на Република Северна Македонија (free issues)", "url": "https://slvesnik.com.mk/besplaten-pristap-do-izdanija.nspx",
             "retrieved": "2026-10-29", "requests": n_sl, "licence": "Official texts, not protected by copyright (Закон за авторското право и сродните права)",
             "notes": "Issue lists via POST /FreeIssue/GetIssuesByYear; PDFs at /Issues/<FileName>.pdf. Issues 52/1991, 1/1992, 31/1998 and 91/2001 are scans with the publisher's OCR text layer (used for paragraph layout only); later issues are born-digital."},
            {"name": "Собрание на Република Северна Македонија: Устав", "url": "https://www.sobranie.mk/ustav-na-rm.nspx",
             "retrieved": "2026-10-29", "requests": n_sob, "licence": "Official texts, not protected by copyright (Закон за авторското право и сродните права)",
             "notes": "UstavnaRmizmeni.pdf: certified copy of the 1991 Constitution with Amendments I-XXXII (Word-made text). USTAVen.pdf (English) used only as a check."},
        ],
        "laws": laws,
        "acts": [act, act2],
        "applies": [],
        "decisions": [
            "There is no official consolidated text of the Constitution: officially it is the 1991 text plus Amendments I-XXXVI, each 'a constituent part of the Constitution'. The history here applies each amendment, by its own instruction point, to the provisions it names.",
            "Words of the 1991 text and of Amendments I-XVIII come from the Assembly's certified copy (UstavnaRmizmeni.pdf), whose text layer is clean; the gazette issues of 1991-2001 are scans whose publisher-made OCR layer has errors (e.g. 'делот' for 'денот').",
            "The Assembly copy merges some paragraphs (Art. 3 and 4 show 2 paragraphs where the gazette has 3, and Amendment XXXII replaces 'став 2' of Art. 4). Paragraph breaks are therefore taken from the gazette layout (an indented first line), aligned word by word with the Assembly copy, plus the copy's own line-final breaks and dash alineas.",
            "Amendments XIX-XXXVI are read from the born-digital gazette issues (84/2003, 107/2005, 3/2009, 49/2011, 6/2019); for XIX-XXXII the words were compared with the Assembly copy (differences listed in parse.log).",
            "One glyph is lost in the Assembly copy ('с#' in Amendment IV's preamble); it is read as 'сè', the word in that phrase ('зачувување и развој на сè што е вредно').",
            "Latin look-alike letters inside Cyrillic words (typesetting) are mapped to Cyrillic.",
            "Articles are files clen-NNN.md with the article heading as the first line; one paragraph (став) or alinea per line. Chapter and section headings are their own files (glava-NN, glava-NN-NN).",
        ] + ustav_replay.DECISIONS + zzlp.DECISIONS,
        "ai_use": ["none: no model produced or edited any text or data; parsing is rule-based Python (parse.py, ustav.py, ustav_replay.py, pdftext.py)."],
    }
    tmp = os.path.join(HERE, "corpus.json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(HERE, "corpus.json"))
    with open(os.path.join(HERE, "parse.log"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log) + "\n")
    v = act["versions"]
    print(f"ustav: {len(v)} versions, {len(v[-1]['units'])} units, {len(laws)} laws")
    v = act2["versions"]
    print(f"zzlp: {len(v)} versions, {len(v[-1]['units'])} units")


if __name__ == "__main__":
    main()
