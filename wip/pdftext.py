"""PDF text helpers for North Macedonia: line extraction, gazette paragraphing, word alignment.

The Assembly's certified copy of the Constitution (Word-made PDF) has clean words but merges some
paragraphs. The Official Gazette issues of 1991-2001 are scans whose publisher-made text layer has
OCR errors but keeps the layout (each paragraph's first line is indented). We take the words from
the Assembly copy and the paragraph breaks from the gazette layout, aligning the two word streams.
No OCR is run here: only text layers the publishers embedded are read.
"""
import difflib, re, unicodedata
import fitz  # PyMuPDF

CYR_LOOKALIKE = str.maketrans({"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у",
                               "A": "А", "E": "Е", "O": "О", "P": "Р", "C": "С", "X": "Х", "K": "К",
                               "M": "М", "H": "Н", "T": "Т", "B": "В", "j": "ј", "J": "Ј"})


def fix_word(w):
    """Latin look-alike letters inside an otherwise Cyrillic word become Cyrillic (publisher typesetting)."""
    if re.search(r"[А-Яа-яЀ-ӿ]", w) and re.search(r"[A-Za-z]", w) and not re.search(r"[IVX]{2,}", w):
        return w.translate(CYR_LOOKALIKE)
    return w


def key(w):
    w = fix_word(w).lower().replace("ѐ", "е").replace("ѝ", "и")
    return "".join(ch for ch in w if ch.isalnum())


def lines(path, pages=None):
    """Every text line: dict(page, x0, x1, y0, y1, text, col, pw)."""
    doc = fitz.open(path)
    out = []
    for pn in (pages if pages is not None else range(doc.page_count)):
        pg = doc[pn]
        pw = pg.rect.width
        for b in pg.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                t = "".join(s["text"] for s in l["spans"])
                if not t.strip():
                    continue
                x0, y0, x1, y1 = l["bbox"]
                out.append({"page": pn, "x0": x0, "x1": x1, "y0": y0, "y1": y1, "text": t.rstrip(), "pw": pw})
    return out


def gazette_paragraphs(path, pages, header_y=58, cols=2):
    """Gazette lines in reading order with a 'start' flag where a paragraph begins (indented first line).

    Two columns per page; the scans are skewed, so indentation is judged against nearby lines only."""
    ls = [l for l in lines(path, pages) if l["y0"] > header_y]
    for l in ls:
        l["col"] = 0 if (cols == 1 or l["x0"] < l["pw"] / 2 - 15) else 1
    ls.sort(key=lambda l: (l["page"], l["col"], round(l["y0"])))
    # merge fragments on the same baseline (OCR splits lines into spans/blocks)
    merged = []
    for l in ls:
        if merged and merged[-1]["page"] == l["page"] and merged[-1]["col"] == l["col"] and abs(merged[-1]["y0"] - l["y0"]) < 3:
            m = merged[-1]
            if l["x0"] >= m["x1"] - 2:
                m["text"] += " " + l["text"]; m["x1"] = l["x1"]
            else:
                m["text"] = l["text"] + " " + m["text"]; m["x0"] = l["x0"]
            continue
        merged.append(dict(l))
    for i, l in enumerate(merged):
        near = [m["x0"] for m in merged if m["page"] == l["page"] and m["col"] == l["col"] and abs(m["y0"] - l["y0"]) < 90]
        base = min(near)
        off = l["x0"] - base
        l["indent"] = off
        l["start"] = 6 < off < 40
    return merged


def tokens_from_lines(ls, dehyphen=True):
    """Word tokens: list of dict(w, key, line, start). A line-final '-' joins the word with the next line."""
    toks = []
    carry = False
    for li, l in enumerate(ls):
        ws = l["text"].split()
        for wi, w in enumerate(ws):
            if carry and wi == 0:
                toks[-1]["w"] = toks[-1]["w"][:-1] + w
            else:
                toks.append({"w": w, "line": li, "start": bool(l.get("start")) and wi == 0})
            carry = False
            if dehyphen and wi == len(ws) - 1 and w.endswith("-") and len(w) > 1 and re.search(r"\w-$", w):
                carry = True
    out, pending = [], None
    for t in toks:
        t["key"] = key(t["w"])
        if not t["key"]:  # punctuation-only word (a dash): carry it and its start flag to the next word
            if pending is None:
                pending = {"pre": [], "start": False}
            pending["pre"].append(t["w"]); pending["start"] |= t["start"]
            continue
        t["pre"] = []
        if pending is not None:
            t["pre"] = pending["pre"]; t["start"] |= pending["start"]; pending = None
        out.append(t)
    return out


def align_starts(a_toks, g_toks):
    """Map gazette paragraph starts onto the Assembly token stream. Returns (set of a-indices, unmatched count)."""
    a = [t["key"] for t in a_toks]
    g = [t["key"] for t in g_toks]
    sm = difflib.SequenceMatcher(None, a, g, autojunk=False)
    blocks = sm.get_matching_blocks()
    starts, missed = set(), 0
    bi = 0
    for gi, t in enumerate(g_toks):
        if not t["start"]:
            continue
        hit = None
        for (ai, bj, n) in blocks:
            if bj <= gi < bj + n and n >= 2:
                hit = ai + (gi - bj)
                break
        if hit is None:
            missed += 1
        else:
            starts.add(hit)
    return starts, missed, sm.ratio()
