"""Counts the "до <date>" and "заклучно со <date>" phrases in this repository's law texts, and how many of them the
lapsed-time-limit scan (ingest/textscan.py) reads, with the forms of those it does not read.

    python -X utf8 mk_gap_check.py <scripts dir>

A phrase is "до", "заклучно со" or "најдоцна до", then a day, a month (in words or digits) and a year
("до 31 декември 2020", "заклучно со 30.06.2004"), or "до крајот на <year>"; month and year without a day
("до април 2020 година", "заклучно со месец јули 2019") are counted apart, as the scan does not read them.
Files are read at the pinned commit (pins.json), law texts only (not data/, tools/, the root files or README.md)."""
import collections, json, os, re, subprocess, sys

S = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(S, "ingest"))
os.chdir(S)
import textscan as t  # noqa: E402

c = json.load(open("pins.json", encoding="utf-8"))["corpora"][0]
repo = os.path.join("sources", "corpus", c["repo"] + ".git")
M = "јануари|февруари|март|април|мај|јуни|јули|август|септември|октомври|ноември|декември"
PHRASE = re.compile(rf"(?i)(?<![\w])(?:до|заклучно\s+со|најдоцна\s+до)\s+(?:(?:\d{{1,2}}\s*\.?\s*(?:{M})|\d{{1,2}}\.\s?\d{{1,2}}\.)\s*\d{{4}}"
                    rf"|крајот\s+на\s+\d{{4}})")
NODAY = re.compile(rf"(?i)(?<![\w])(?:до|заклучно\s+со)\s+(?:крајот\s+на\s+)?(?:месец\s+)?(?:{M})\s+\d{{4}}")
ls = subprocess.run(["git", "--git-dir", repo, "ls-tree", "-r", "--name-only", c["sha"]], capture_output=True, text=True,
                    encoding="utf-8").stdout.split("\n")
files = [p for p in ls if p.endswith(".md") and "/" in p and not p.startswith(("data/", "tools/")) and not p.endswith("/README.md")]
p = subprocess.Popen(["git", "--git-dir", repo, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
read = missed = noday = 0
forms = collections.Counter()
for f in files:
    p.stdin.write(f"{c['sha']}:{f}\n".encode()); p.stdin.flush()
    size = int(p.stdout.readline().split()[2])
    text = p.stdout.read(size).decode("utf-8", "replace"); p.stdout.read(1)
    for line in text.splitlines():
        for m in PHRASE.finditer(line):
            if list(t.limits(line[m.start():m.end() + 1])):
                read += 1
            else:
                missed += 1; forms[re.sub(r"\d", "9", m.group(0))] += 1
        noday += len(NODAY.findall(line))
p.stdin.close()
print(f"{len(files)} law files; phrases with a day: {read + missed}, read {read}, not read {missed}")
for k, v in forms.most_common(15):
    print(f"  {v:5d}  {k}")
print(f"month and year without a day (not read): {noday}")
