"""Replay Amendments I-XXXVI onto the 1991 Constitution.

OPS transcribes each amendment's own instruction point ("Со овој амандман се заменува ставот 2 на
членот 4 ...") into an operation; the replacement words are always the amendment's own content
points, copied verbatim. parse checks every instruction text against the operation it was given
(article, paragraph and alinea numbers must appear in the instruction).
"""
import re

# (amendment, [ops]); op = (kind, article, stav, alinea, points)
OPS = {
    1: [("replace_stav", 3, 3, None, [2]), ("add_stav_end", 3, None, None, [1])],
    2: [("append_to_stav", 49, 1, None, [1])],
    3: [("replace_stav", 12, 5, None, [1])],
    4: [("replace_preamble", None, None, None, [1])],
    5: [("replace_art", 7, None, None, [1])],
    6: [("insert_after_alinea", 8, 1, 2, [1])],
    7: [("replace_stav", 19, 4, None, [2]), ("replace_stav", 19, 3, None, [1])],
    8: [("replace_art", 48, None, None, [1])],
    9: [("replace_stav", 56, 2, None, [1])],
    10: [("replace_art", 69, None, None, [1, 2])],
    11: [("replace_stav", 77, 2, None, [2]), ("replace_stav", 77, 1, None, [1])],
    12: [("replace_art", 78, None, None, [1]), ("delete_alinea", 84, 1, 7, [])],
    13: [("append_to_stav", 86, 2, None, [1])],
    14: [("append_to_stav", 104, 2, None, [1])],
    15: [("replace_stav", 109, 2, None, [1])],
    16: [("replace_stav", 114, 5, None, [1])],
    17: [("replace_stav", 115, 1, None, [1]), ("replace_stav", 117, 2, None, [2])],
    18: [("insert_stav", 131, 4, None, [1])],
    19: [("replace_art", 17, None, None, [1])],
    20: [("add_stav_end", 13, None, None, [1])],
    21: [("replace_art", 15, None, None, [1])],
    22: [("replace_alinea", 84, 1, 5, [1])],
    23: [("replace_stav", 89, 3, None, [1])],
    24: [("replace_alinea", 91, 1, 12, [1])],
    25: [("replace_art", 98, None, None, [1])],
    26: [("replace_stav", 99, 3, None, [1])],
    27: [("replace_stav", 100, 3, None, [2]), ("replace_stav", 100, 2, None, [1])],
    28: [("replace_art", 104, None, None, [1])],
    29: [("replace_art", 105, None, None, [1]), ("delete_alinea", 68, 1, 15, [])],
    30: [("replace_stavs", 106, (2, 3), None, [1]), ("delete_art", 107, None, None, [])],
    31: [("replace_stav", 81, 5, None, [1])],
    32: [("replace_stav", 4, 2, None, [1])],
    34: [("preamble_edits", None, None, None, [1])],
    35: [("add_stav_end", 3, None, None, [1])],
    36: [("replace_art", 49, None, None, [1])],
    33: [("rename", None, None, None, [1])],  # applied last on 2019-02-12, after XXXIV-XXXVI
}

DECISIONS = [
    "Amendment I point 1, XX and XXXV 'supplement' (се дополнува) an article without saying where: their text is added as new paragraphs at the end of the article.",
    "Amendments II, XIII and XIV supplement a paragraph (став) with a sentence: the sentence is appended to that paragraph, verbatim, after one space.",
    "Amendment VI supplements alinea 2 of Article 8 with a phrase that is not a sentence of that alinea: it is inserted verbatim as its own line right after that alinea; no words are composed.",
    "Amendment XI point 2 'supplements' Article 77(2) but restates the whole paragraph with the new sentence; it replaces that paragraph, so the old wording is not duplicated.",
    "Amendment XVIII adds 'a new paragraph 4' to Article 131: inserted as paragraph 4, the old paragraph 4 becoming 5 (later decisions cite 'член 131 став 5' for promulgation, which confirms it).",
    "Amendment XXXIV's edits to the Preamble (as replaced by Amendment IV) are applied as the exact string deletions, replacement and insertion it quotes; double spaces left by a deletion collapse to one.",
    "Amendment XXXIII renames 'Република Македонија' to 'Република Северна Македонија' and 'Македонија' to 'Северна Македонија' everywhere except Article 36, in headings too (upper-case forms included).",
    "Amendments XXXIII-XXXVI were adopted on 2019-01-11 and published on 2019-01-12 but took effect on 2019-02-12 (Government notice, Службен весник 36/2019, no. 623); they are dated 2019-02-12.",
    "Every other amendment says it takes effect on the day of its promulgation, and the 1991 Constitution (Art. 134) on the day the Assembly promulgated it (1991-11-17).",
]


def stavs_from(paras):
    st = []
    for p in paras:
        if re.match(r"^[-–—]\s*", p):
            if not st:
                st.append([])
            st[-1].append(re.sub(r"^[-–—]\s*", "- ", p))
        else:
            st.append([p])
    return st


class Replay:
    def __init__(self, pre, arts, heads, order):
        self.pre = list(pre)
        self.arts = {n: stavs_from(ps) for n, ps in arts.items()}
        self.heads = dict(heads)
        self.order = order
        self.log = []

    def _check(self, cond, msg):
        if not cond:
            raise ValueError(msg)

    def apply(self, am, points, instr):
        for kind, art, stav, al, pts in OPS[am]:
            content = [p for k in pts for p in points[k]]
            # the instruction must name the numbers we use
            for num, word in ((art, "член"), (stav if not isinstance(stav, tuple) else None, "став"), (al, "алинеја")):
                if num is not None and kind != "add_stav_end" and not (word == "став" and "alinea" in kind and not re.search(r"\bстав", instr)):
                    self._check(re.search(rf"{word}\w*\s+{num}\b|{num}\s+на\s+{word}|\b{num}\b", instr),
                                f"amendment {am}: instruction lacks {word} {num}: {instr}")
            if kind == "replace_art":
                old = self.arts[art]; self.arts[art] = stavs_from(content)
            elif kind == "delete_art":
                self._check(art in self.arts, f"{am}: no art {art}"); old = self.arts.pop(art)
            elif kind == "replace_stav":
                a = self.arts[art]; self._check(len(a) >= stav, f"{am}: art {art} has {len(a)} stavs, wants {stav}")
                old = a[stav - 1]; a[stav - 1:stav] = stavs_from(content)
            elif kind == "replace_stavs":
                a = self.arts[art]; s0, s1 = stav; self._check(len(a) >= s1, f"{am}: art {art} has {len(a)} stavs")
                old = a[s0 - 1:s1]; a[s0 - 1:s1] = stavs_from(content)
            elif kind == "insert_stav":
                a = self.arts[art]; self._check(len(a) >= stav, f"{am}: art {art} short"); old = None
                a[stav - 1:stav - 1] = stavs_from(content)
            elif kind == "add_stav_end":
                old = None; self.arts[art].extend(stavs_from(content))
            elif kind == "append_to_stav":
                a = self.arts[art]; self._check(len(a) >= stav and len(content) == 1, f"{am}: append target")
                old = a[stav - 1][0]; a[stav - 1][0] = a[stav - 1][0].rstrip() + " " + content[0]
            elif kind in ("insert_after_alinea", "replace_alinea", "delete_alinea"):
                s = self.arts[art][stav - 1]
                self._check(len(s) - 1 >= al and s[al].startswith("- "), f"{am}: art {art} stav {stav} has {len(s) - 1} alineas, wants {al}")
                old = s[al]
                if kind == "insert_after_alinea":
                    s[al + 1:al + 1] = content
                elif kind == "replace_alinea":
                    self._check(len(content) == 1, f"{am}: alinea content"); s[al] = "- " + content[0]
                else:
                    del s[al]
            elif kind == "replace_preamble":
                old = self.pre; self.pre = content
            elif kind == "preamble_edits":
                old = list(self.pre); self.preamble_edits(content[0])
            elif kind == "rename":
                old = None; self.rename()
            else:
                raise ValueError(kind)
            self.log.append(f"amendment {am} {kind} art {art} stav {stav} alinea {al}: was {str(old)[:90]!r}")

    def preamble_edits(self, instr):
        q = re.findall(r"„([^“]+)“", instr)
        # quoted: delete q0; replace q1 by q2; after q3 add q4; delete q5
        self._check(len(q) == 6, f"XXXIV quotes {q}")
        text = "\n".join(self.pre)
        norm = lambda s: s.replace("кaко", "како")  # Latin 'a' in the gazette's quotation
        for old, new in ((norm(q[0]), ""), (q[1], q[2]), (q[5], "")):
            self._check(text.count(old) == 1, f"XXXIV: '{old}' found {text.count(old)} times")
            text = text.replace(old, new)
        self._check(text.count(q[3]) == 1, f"XXXIV: '{q[3]}' found {text.count(q[3])} times")
        text = text.replace(q[3], q[3] + " " + q[4])
        text = re.sub(r" {2,}", " ", text).replace(" ,", ",")
        self.pre = text.split("\n")

    def rename(self):
        def ren(s):
            s = s.replace("Република Македонија", "Република Северна Македонија")
            s = re.sub(r"(?<!Северна )Македонија", "Северна Македонија", s)
            s = s.replace("РЕПУБЛИКА МАКЕДОНИЈА", "РЕПУБЛИКА СЕВЕРНА МАКЕДОНИЈА")
            s = re.sub(r"(?<!СЕВЕРНА )МАКЕДОНИЈА", "СЕВЕРНА МАКЕДОНИЈА", s)
            return s
        self.pre = [ren(p) for p in self.pre]
        self.heads = {k: ren(v) for k, v in self.heads.items()}
        for n in self.arts:
            if n != 36:
                self.arts[n] = [[ren(l) for l in s] for s in self.arts[n]]

    def units(self):
        u = {"preambula": {"label": "Преамбула", "kind": "preamble", "text": "\n".join(self.pre) + "\n"}}
        for kind, k in self.order:
            if kind == "head":
                u[k] = {"label": self.heads[k], "kind": "heading", "text": self.heads[k] + "\n"}
            elif k in self.arts:
                lines = [f"# Член {k}"] + [l for s in self.arts[k] for l in s]
                u[f"clen-{k:03d}"] = {"label": f"Член {k}", "kind": "art", "text": "\n".join(lines) + "\n"}
        return u
