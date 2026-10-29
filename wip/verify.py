"""Independent checks of corpus.json against other official copies (cache only). Writes verification.json.

1. Закон за заштита на личните податоци as enacted: our parse of the two-column gazette issue 42/2020 against
   the publisher's single-act copy of the same law (Aspose single column, no hyphenation), word for word.
2. Устав: Amendments XIX-XXXII words, born-digital gazette against the Assembly copy (reported by parse.log).
"""
import difflib, json, os, re
import fitz
import fetch, pdftext

HERE = os.path.dirname(os.path.abspath(__file__))
ZZLP_COPY = "https://azlp.mk/wp-content/uploads/2022/11/zakon_za_zastita_na_licnite_podatoci.pdf"


def words(text):
    return [k for k in (pdftext.key(w) for w in text.split()) if k]


def main():
    c = json.load(open(os.path.join(HERE, "corpus.json"), encoding="utf-8"))
    act = next(a for a in c["acts"] if a["slug"] == "zakon-za-zastita-na-licnite-podatoci")
    u = act["versions"][0]["units"]
    mine = [w for k in u if u[k]["kind"] != "heading" for l in u[k]["text"].split("\n") if not l.startswith("#") for w in words(l)]
    d = fitz.open(fetch.path_for(ZZLP_COPY))
    t = "\n".join(p.get_text() for p in d)
    t = re.sub(r"Службен весник на РСМ, бр\. 42 од 16\.2\.2020 година\s*\n\s*\d+ од \d+\s*\n", "\n", t)
    t = t[t.find("I. ОПШТИ ОДРЕДБИ"):]
    t = t[:t.find("Член 124") + 400]
    # the copy's own article numbers and titles are not in our body text; drop them from its stream
    titles = {a["text"].split("\n")[1][3:] for a in u.values() if a["kind"] == "art" and a["text"].split("\n")[1].startswith("## ")}
    heads = {a["text"].strip() for a in u.values() if a["kind"] == "heading"}
    ref_lines = [l.strip() for l in t.split("\n")]
    ref = []
    for l in ref_lines:
        if re.match(r"^Член \d+", l) or l in titles or l in heads or re.match(r"^(Службен весник на РСМ, бр\. 42|\d+ од 70$)", l):
            continue
        ref += words(l)
    sm = difflib.SequenceMatcher(None, mine, ref, autojunk=False)
    furniture = [words(x) for x in titles | heads]
    note = words(t[t.find("* Со овој закон"):t.find("CELEX број 32016R0679") + 22]) if "* Со овој закон" in t else []
    def explained(seg):  # a title set on two lines in the copy, or the footnote on harmonisation with the GDPR
        return any(seg == f for f in furniture) or (note and " ".join(seg) in " ".join(note))
    diffs, skipped = [], 0
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            continue
        if op == "insert" and explained(ref[b0:b1]):
            skipped += 1
            continue
        diffs.append((op, " ".join(mine[a0:a1]), " ".join(ref[b0:b1])))
    out = {"zzlp_as_enacted_vs_publisher_copy": {"source": ZZLP_COPY, "our_words": len(mine), "copy_words": len(ref),
                                                 "differences": diffs, "explained_inserts (titles, footnote)": skipped}}
    log = open(os.path.join(HERE, "parse.log"), encoding="utf-8").read()
    out["ustav_amendments_gazette_vs_assembly"] = [l for l in log.split("\n") if "gazette vs Assembly" in l]
    with open(os.path.join(HERE, "verification.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"zzlp: {len(mine)} vs {len(ref)} words, {len(diffs)} differences: {diffs[:10]}")


if __name__ == "__main__":
    main()
