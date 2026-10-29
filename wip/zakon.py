"""Read a law from a born-digital Official Gazette issue (two columns, Word-made).

Layout rules (Службен весник since about 2003): "Член N" lines are centered; an article's title is the bold
line(s) just above it; chapter headings are centered, not bold; every paragraph's first line is indented;
footnotes and page headers are set in a smaller size. The Macedonian text comes first; the Albanian text
that follows (Latin script) is not read.
"""
import re
import fitz
import fetch, pdftext

RE_CLEN = re.compile(r"^\s*Член\s+(\d+)(?:-([а-ж]))?\s*$")


def layout_lines(path, pages):
    doc = fitz.open(path)
    out = []
    for pn in pages:
        pg = doc[pn]
        pw = pg.rect.width
        for b in pg.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                sp = [s for s in l["spans"] if s["text"].strip()]
                if not sp:
                    continue
                size = max(s["size"] for s in sp)
                t = "".join(s["text"] for s in l["spans"]).strip()
                x0, y0, x1, y1 = l["bbox"]
                if size < 9 or y0 < 58:
                    continue  # page header, footnote
                out.append({"page": pn, "x0": x0, "x1": x1, "y0": y0, "text": t, "pw": pw,
                            "bold": all("Bold" in s["font"] or s["flags"] & 16 for s in sp),
                            "col": 0 if x0 < pw / 2 - 15 else 1})
    out.sort(key=lambda l: (l["page"], l["col"], round(l["y0"])))
    # column margins per page/column
    for l in out:
        same = [m for m in out if m["page"] == l["page"] and m["col"] == l["col"]]
        left = min(m["x0"] for m in same)
        right = max(m["x1"] for m in same)
        l["lin"] = l["x0"] - left
        l["rin"] = right - l["x1"]
        l["centered"] = l["lin"] > 20 and abs(l["lin"] - l["rin"]) < 12
        l["start"] = 9 < l["lin"] < 25
    return out


def join_hyphen(prev, nxt, vocab):
    m = re.search(r"(\S+)-$", prev)
    if m and re.search(r"[^\W\d_]-$", prev):
        first = nxt.split()[0]
        clean = lambda w: w.lower().strip(".,;:„“\"()")
        both = clean(m.group(1) + "-" + first)
        if first[:1].isupper() or both in vocab:
            return prev + nxt
        return prev[:-1] + nxt
    return prev + " " + nxt


def read_law(path, pages, title_re, log, stop_re=r"^(L\s*I\s*G\s*J|LIGJ)\b"):
    ls = layout_lines(path, pages)
    clean = lambda w: w.lower().strip(".,;:„“\"()")
    vocab = {clean(w) for l in ls for w in l["text"].split()[:-1]}
    # find the law's start: "З А К О Н" / "ЗАКОН" followed by the title
    i0 = None
    for i, l in enumerate(ls):
        if re.match(r"^З\s*А\s*К\s*О\s*Н$", l["text"]) and i + 1 < len(ls) and re.search(title_re, " ".join(x["text"] for x in ls[i + 1:i + 4])):
            i0 = i
            break
    if i0 is None:
        raise ValueError("law start not found")
    items = []  # (kind, text): head / title / clen / para
    lastl = None
    i = i0 + 1
    while i < len(ls) and not ls[i]["text"].startswith(("I.", "Член", "ГЛАВА")) and not ls[i]["bold"] is False:
        i += 1
    while i < len(ls):
        l = ls[i]
        t = re.sub(r"\s+", " ", l["text"]).strip()
        if re.match(stop_re, t) or t.startswith("_____"):
            break
        letters = re.sub(r"[^А-Яа-яЀ-ӿ]", "", t)
        upper = len(letters) > 3 and letters.isupper()
        prev = items[-1][0] if items else None
        if RE_CLEN.match(t):
            items.append(["clen", t])
        elif l["bold"] and re.match(r"^\d+\.\s", t):  # numbered section heading
            items.append(["head", t])
        elif l["bold"] and prev == "head" and re.match(r"^\d+\.\s", items[-1][1]) and lastl is not None \
                and lastl["bold"] and lastl["col"] == l["col"] and lastl["page"] == l["page"] and 0 < l["y0"] - lastl["y0"] < 12.5:
            items[-1][1] = join_hyphen(items[-1][1], t, vocab)  # a section heading set on two lines
        elif l["bold"]:  # article title (body text is never bold)
            if prev == "title":  # a title set on two lines
                items[-1][1] = join_hyphen(items[-1][1], t, vocab)
            else:
                items.append(["title", t])
        elif upper and (re.match(r"^[IVXLC]+\.\s", t) or prev == "head"):
            if prev == "head" and not re.match(r"^([IVXLC]+|\d+)\.\s", t):
                items[-1][1] = join_hyphen(items[-1][1], t, vocab)
            else:
                items.append(["head", t])
        elif l["start"] or not items or items[-1][0] != "para" or re.match(r"^[-–]\s?\S", t) and not items[-1][1].endswith("-"):
            items.append(["para", t])
        else:
            items[-1][1] = join_hyphen(items[-1][1], t, vocab)
        lastl = l
        i += 1
    # assemble articles
    order, arts, heads = [], {}, {}
    cur, pending_title, chap = None, None, 0
    for it in items:
        kind, t = it[0], it[1]
        if kind == "head":
            chap += 1
            k = f"glava-{chap:02d}"
            heads[k] = t; order.append(("head", k)); cur = None
        elif kind == "title":
            pending_title = t if pending_title is None else pending_title + " " + t
        elif kind == "clen":
            m = RE_CLEN.match(t)
            n = int(m.group(1)); suf = m.group(2)
            key = (n, suf)
            if key in arts:
                raise ValueError(f"duplicate {t}")
            arts[key] = {"title": pending_title, "paras": []}
            order.append(("art", key)); cur = key; pending_title = None
        else:
            if cur is None:
                log.append(f"zakon: text outside an article: {t[:80]}")
                continue
            arts[cur]["paras"].append(re.sub(r"^-(?=\S)", "- ", t))
    return order, arts, heads


def units(order, arts, heads):
    u = {}
    for kind, k in order:
        if kind == "head":
            u[k] = {"label": heads[k], "kind": "heading", "text": heads[k] + "\n"}
        elif k in arts:
            n, suf = k
            label = f"Член {n}" + (f"-{suf}" if suf else "")
            key = f"clen-{n:03d}" + (f"-{'abvgdgez'['абвгдѓеж'.index(suf)]}" if suf else "")
            a = arts[k]
            lines = [f"# {label}"] + ([f"## {a['title']}"] if a["title"] else []) + a["paras"]
            u[key] = {"label": label, "kind": "art", "text": "\n".join(lines) + "\n"}
    return u
