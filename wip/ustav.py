"""The Constitution of North Macedonia (1991) with Amendments I-XXXVI, replayed onto the 1991 text.

Sources (all cached by fetch.py; parse reads only the cache):
- Assembly certified copy (UstavnaRmizmeni.pdf, Word-made text layer): the words of the 1991 text and of
  Amendments I-XXXII.
- Official Gazette issues: paragraph layout of the 1991 text and Amendments I-XXXII (52/1991, 1/1992,
  31/1998, 91/2001, 84/2003, 107/2005, 3/2009, 49/2011); the words of Amendments XXXIII-XXXVI (6/2019,
  born-digital) and the Government notice of their entry into force (36/2019).
"""
import re
import fetch, pdftext

ASSEMBLY = "https://www.sobranie.mk/WBStorage/Files/UstavnaRmizmeni.pdf"
ENGLISH = "https://www.sobranie.mk/content/EN%20DOKUMENTI/USTAVen.pdf"
GZ = "https://slvesnik.com.mk/Issues/%s.pdf"
ISSUES = {  # gazette issue -> (file name, pages holding the text we align, columns)
    "52/1991": ("19D704B29EC040A1968D7996AA0F1A56", None),
    "1/1992": ("98CDA7C67EDE420ABF00D5A2E42EECD4", [0]),
    "31/1998": ("5A5AFC3C14D749518E0C81BB6A930D37", [0]),
    "91/2001": ("4565B8A8CCA245AC99E52A0EE60B3FCD", [0, 1, 2]),
    "84/2003": ("472A2EE4E20F2A47B337802CC8EFD4EB", [1]),
    "107/2005": ("8B3E1432BAD3E94093DF8D80D5959D92", [1, 2]),
    "3/2009": ("5AB84E6DEF83584C847BD8AACF735678", [3]),
    "49/2011": ("D51AC5D691C31243B94EFFEBBDA61E05", [1]),
}
G2019 = ("6/2019", "46e75d0202104cb8ba5b2786583518bb")
G2019_NOTICE = ("36/2019", "348ba55dfcb3468aac190fa75e2918e0")

ROMAN = {}
for i, r in enumerate("I II III IV V VI VII VIII IX X XI XII XIII XIV XV XVI XVII XVIII XIX XX XXI XXII XXIII XXIV "
                      "XXV XXVI XXVII XXVIII XXIX XXX XXXI XXXII XXXIII XXXIV XXXV XXXVI".split(), 1):
    ROMAN[r] = i

RE_ART = re.compile(r"^\s*Член\s+(\d+)\s*$")
RE_HEAD = re.compile(r"^\s*(?:[IVX]+\.\s+[А-ЯЃЌЅЉЊЏЈ]|\d\.\s+[А-ЯЃЌЅЉЊЏЈ][^.;:]*[^.;:,\s]\s*$)")
RE_AMHEAD = re.compile(r"^\s*А\s*М\s*А\s*Н\s*Д\s*М\s*А\s*Н\s+([IVX]+)\s*$")
RE_DECISION = re.compile(r"^\s*Врз основа на член")


def assembly_paragraphs(log):
    path = fetch.get(ASSEMBLY)
    ls = pdftext.lines(path)
    for l in ls:
        l["text"] = l["text"].replace("с# ", "сè ")  # one glyph lost in the copy; see decisions
        # a sentence glued to the next one without a space ("особености.Републиката")
        l["text"] = re.sub(r"(?<=[а-яѓќѕљњџј][.;])(?=[А-ЯЃЌЅЉЊЏЈ])", " ", l["text"])
    right = max(l["x1"] for l in ls)
    struct = set()
    for i, l in enumerate(ls):
        t = l["text"]
        upper = len(t.strip()) > 3 and not re.search(r"[а-яѓќѕљњџј]", t)
        if upper or RE_ART.match(t) or RE_AMHEAD.match(t) or RE_DECISION.match(t) or (RE_HEAD.match(t) and i + 1 < len(ls) and (RE_ART.match(ls[i + 1]["text"]) or RE_HEAD.match(ls[i + 1]["text"]))):
            struct.add(i)
    for i, l in enumerate(ls):
        prev = ls[i - 1] if i else None
        own = (i in struct or (i - 1) in struct or l["text"].lstrip().startswith(("-", "–"))
               or (prev is not None and prev["x1"] < right - 60 and re.search(r"[.:;]\s*$", prev["text"]))
               or prev is None or prev["page"] != l["page"] and False)
        l["start"] = bool(own)
    a_toks = pdftext.tokens_from_lines(ls, dehyphen=False)
    own_starts = {i for i, t in enumerate(a_toks) if t["start"]}
    # gazette layout
    g_toks = []
    for issue, (fn, pages) in ISSUES.items():
        gl = pdftext.gazette_paragraphs(fetch.get(GZ % fn), pages)
        g_toks += pdftext.tokens_from_lines(gl)
    starts, missed, ratio = pdftext.align_starts(a_toks, g_toks)
    log.append(f"ustav: Assembly tokens {len(a_toks)}, gazette tokens {len(g_toks)}, similarity {ratio:.3f}, "
               f"gazette paragraph starts unmapped {missed}, mapped {len(starts)}, Assembly-own starts {len(own_starts)}, "
               f"gazette-only starts {len(starts - own_starts)}")
    allstarts = own_starts | starts
    paras = []
    for i, t in enumerate(a_toks):
        if i in allstarts or not paras:
            paras.append({"words": [], "line": t["line"], "src": "g" if i not in own_starts else "a"})
        paras[-1]["words"].extend(t["pre"] + [pdftext.fix_word(t["w"])])
    for p in paras:
        p["text"] = " ".join(p["words"])
    return paras, ls


def to_stavs(paras):
    """Paragraph texts -> list of stavs; a stav is [text, alinea, alinea, ...]."""
    stavs = []
    for p in paras:
        if re.match(r"^[-–—]\s*", p):
            line = re.sub(r"^[-–—]\s*", "- ", p)
            if not stavs:
                stavs.append([])
            stavs[-1].append(line)
        else:
            stavs.append([p])
    return stavs


def split_points(paras):
    """Amendment paragraphs -> {point number: [paragraph texts]} (the 'N. ' prefix removed)."""
    pts, cur = {}, None
    for p in paras:
        m = re.match(r"^(\d+)\.\s*(.*)$", p)
        if m and int(m.group(1)) == (cur or 0) + 1:
            cur = int(m.group(1))
            pts[cur] = [m.group(2)]
        elif cur is not None:
            pts[cur].append(p)
    return pts


def parse_base(paras, log):
    """1991 text: preamble, headings, articles 1-134; and the amendment blocks I-XXXII."""
    texts = [p["text"] for p in paras]
    i0 = next(i for i, t in enumerate(texts) if t.startswith("Тргнувајќи"))
    pre = []
    i = i0
    while not texts[i].startswith("УСТАВ"):
        pre.append(texts[i]); i += 1
    # the "УСТАВ НА РЕПУБЛИКА МАКЕДОНИЈА" title may be one or two paragraphs
    while not (RE_HEAD.match(texts[i]) and texts[i].startswith("I.")):
        i += 1
    order, arts, heads = [], {}, {}
    cur = None
    chap = 0; sec = 0
    while i < len(texts) and not RE_DECISION.match(texts[i]):
        t = texts[i]
        m = RE_ART.match(t)
        if m:
            cur = int(m.group(1)); arts[cur] = []; order.append(("art", cur))
        elif RE_HEAD.match(t) and (i + 1 < len(texts)) and (RE_ART.match(texts[i + 1]) or RE_HEAD.match(texts[i + 1])):
            if re.match(r"^[IVX]+\.", t):
                chap += 1; sec = 0; k = f"glava-{chap:02d}"
            else:
                sec += 1; k = f"glava-{chap:02d}-{sec:02d}"
            heads[k] = t; order.append(("head", k)); cur = None
        else:
            if cur is None:
                raise ValueError(f"text outside an article: {t[:80]}")
            arts[cur].append(t)
        i += 1
    log.append(f"ustav 1991: preamble {len(pre)} paragraphs, {len(arts)} articles, {len(heads)} headings")
    # amendment blocks
    amends = {}
    j = i
    curam = None
    while j < len(texts):
        t = texts[j]
        m = RE_AMHEAD.match(t)
        if m:
            curam = ROMAN[m.group(1)]; amends[curam] = []
        elif RE_DECISION.match(t) or t.startswith(("Дека преписот", "АМАНДМАН", "АМАНДМАНИ", "О Д Л У К А")):
            curam = None if not t.startswith("АМАНДМАН ") or not RE_AMHEAD.match(t) else curam
        elif curam is not None:
            amends[curam].append(t)
        j += 1
    return pre, order, arts, heads, amends


def _norm_roman(t):
    return t.replace("Х", "X").replace("І", "I")


def gazette_text_paragraphs(fn, pages, vocab, log):
    """Born-digital gazette pages -> paragraph texts (indented first line starts a paragraph).

    Line-final hyphens join the word halves, except where the hyphenated compound is attested
    elsewhere in the sources or the second half starts with a capital letter."""
    gl = pdftext.gazette_paragraphs(fetch.get(GZ % fn), pages)
    paras = []
    for l in gl:
        t = re.sub(r"\s+", " ", l["text"]).strip()
        if not t:
            continue
        head = l["indent"] >= 32 or RE_AMHEAD.match(_norm_roman(t))
        if not paras or l["start"] or head or paras[-1]["head"] or re.match(r"^[-–—] ", t):
            paras.append({"text": t, "head": bool(head)})
            continue
        prev = paras[-1]["text"]
        m = re.search(r"(\S+)-$", prev)
        if m and re.search(r"\w-$", prev):
            first = t.split()[0]
            both = (m.group(1) + "-" + first).lower().strip(".,;:")
            if first[:1].isupper() or both in vocab:
                paras[-1]["text"] = prev + t
                log.append(f"ustav: kept hyphen {m.group(1)}-{first}")
            else:
                paras[-1]["text"] = prev[:-1] + t
        else:
            paras[-1]["text"] = prev + " " + t
    return [p["text"] for p in paras]


def gazette_amendments(fn, pages, vocab, log):
    """{amendment number: [paragraph texts]} from a born-digital gazette issue."""
    out, cur = {}, None
    for t in gazette_text_paragraphs(fn, pages, vocab, log):
        m = RE_AMHEAD.match(_norm_roman(t))
        if m:
            cur = ROMAN[m.group(1)]; out[cur] = []; continue
        if cur is None:
            continue
        if RE_DECISION.match(t) or t.startswith(("__", "СОБРАНИЕ НА", "О Д Л У К А")) or re.match(r"^\d+\.$", t):
            cur = None; continue
        out[cur].append(t)
        if re.match(r"^\d+\. Со (овој амандман|точката)", t):  # the instruction point closes the amendment
            out[cur][-1] = re.sub(r"\s+\d+\.$", "", t)
            cur = None
    return out


def english_counts():
    """Paragraph count per article of the Assembly's English text (original 1991 wording), as a check."""
    import fitz
    d = fitz.open(fetch.get(ENGLISH))
    raw = "\n".join(p.get_text() for p in d)
    counts = {}
    cur = None
    for line in raw.split("\n"):
        m = re.match(r"^\s*Article (\d+)\s*$", line)
        if m:
            cur = int(m.group(1)); counts[cur] = 0; continue
        if cur and re.search(r"\S\s{2,}$", line):
            counts[cur] += 1
        if re.match(r"^\s*(DECISION|Amendment|AMENDMENT)", line):
            cur = None
    return counts
