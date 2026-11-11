"""Grow a published git-for-law history append-only: new acts and newer versions go on top; nothing is rewritten.

    python -X utf8 append.py <scripts/<slug>-law> --repo <clone of the published repo> --docs-date YYYY-MM-DD

The clone's main must equal origin/main. What main already holds is read from git itself: for each act, the newest
In-force day it carries, and that the text in force on that day equals the corpus version. Then:

- an act main does not hold yet is appended as one block: its first version, then one commit per law per in-force day,
  in in-force order;
- a version of an act main already holds, in force after the newest one there, is appended as a change, as the full
  build would make it;
- a version in force on or before the newest one there that main lacks (a back-filled form), or a version whose text
  differs from main (a correction), stops the run: those need a labelled correction commit, decided by hand;
- versions in force after --docs-date are left for a later run (they are appended when their day comes).

Author date: noon local time on the in-force day, as in the full build. Committer date: the published head's committer
time plus one second per appended commit, so committer dates never go backwards and the same head and corpus always
give the same hashes. Tags that already exist are never moved; new ones are added. A documentation commit is appended
last (README, REPRODUCE.md, data/), never replacing the earlier one. The result is pushed with a plain `git push`.

Readers choose versions by the In-force trailer (tools/on.py, tools/check_repo.py), so an appended block reads the same
as a full build does.
"""
import argparse
import csv
import io
import json
import re
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build as B  # noqa: E402
import check_repo as K  # noqa: E402
import layout as L  # noqa: E402

# Foreign-repo mode: a repository first built by another builder keeps its own README, REPRODUCE.md and tools;
# this builder's documentation, indexes and tools then live under this prefix (e.g. 'registry/').
DP = os.environ.get('GITLAW_DOCS_PREFIX', '')


def git(repo: Path, *args: str) -> str:
    return B.git(repo, *args)


def published_state(repo: Path, c: dict) -> tuple[dict[str, str], dict[str, tuple[str, list[str]]], dict, dict]:
    """{act slug: newest In-force day on main} for every act main holds, and the acts whose published text differs
    from the corpus on some day main covers: {slug: (first such day, messages)}. Also the per-act commit rows and
    folders, for a correction."""
    # Each act's folder now (assigned from data/paths.json or its official identifier, so an act missing from
    # paths.json is still found), then its folder before the layout move.
    dirs = {a['slug']: list(dict.fromkeys([a.get('path') or a['slug'], a['slug']])) for a in c['acts']}
    rows = K.all_commits_by_act(repo, 'main', dirs)
    cat = K.Cat(repo)
    trees: dict = {}
    state = {}
    problems: dict[str, tuple[str, list[str]]] = {}
    for act in c['acts']:
        # No history found, yet files in its folder: appending it as new would write its first version over a later
        # text. Never; hold it (or refuse) for a look by hand.
        # (A folder holding only a README.md, which a documentation commit wrote, has no text: it doesn't count.)
        if not rows[act['slug']] and act.get('path') and act['versions'] and K.tree_oids(cat, 'main', act['path'], {}):
            problems[act['slug']] = (act['versions'][0]['from'], [
                f'{act["slug"]}: its folder {act["path"]} is on main but no commit with In-force touches it '
                '(an index lost the act?); not appended as new'])
    # Acts whose part file is unchanged since the last verified publish (listed by the caller) need no re-reading:
    # main only grows, so their published text can't have moved. Everything else is read back in full.
    trusted_path = os.environ.get('GITLAW_TRUSTED_ACTS')
    trusted = set(Path(trusted_path).read_text(encoding='utf-8').split()) if trusted_path and Path(trusted_path).exists() else set()
    # An act listed for correction is always read back: trust from a stale record would skip the very difference the
    # correction is for (codul-procedura-civila-1865, 2026-11-02).
    trusted -= set(approved_corrections())
    try:
        for act in c['acts']:
            r = rows[act['slug']]
            if not r:
                continue
            last = max(d for _, d in r)
            state[act['slug']] = last
            if act['slug'] in trusted:
                continue
            # On every day that starts a version, in the corpus or on main, up to `last`, main must serve the corpus's
            # text (else: correction or back-fill). Main's own days matter too: a repeal main dates too early is a day
            # only main has, on which the corpus still has the earlier text in force.
            for day in version_days(act, r, last):
                v = in_force_on(act, day)
                h = K.state_on(r, day)
                files = K.tree_oids(cat, h, dirs[act['slug']], trees) if h else {}
                want = {f'{k}.md': K.blob_oid(u['text']) for k, u in v['units'].items()} if v else {}
                if files != want or (v is None) != (h is None):
                    bad = sorted(f for f in set(files) | set(want) if files.get(f) != want.get(f))
                    first, msgs = problems.get(act['slug'], (day, []))
                    problems[act['slug']] = (min(first, day), msgs)
                    msgs.append(f'{act["slug"]} {day}: main differs from the corpus in {len(bad)} file(s), '
                                f'e.g. {bad[:3]} (a correction or a back-filled form: decide by hand)')
    finally:
        cat.close()
    return state, problems, rows, dirs


def in_force_on(act: dict, day: str) -> dict | None:
    """The corpus version in force on `day` (the last one of the latest day on or before it), or None before the first."""
    hit = None
    for v in act['versions']:
        if v['from'] > day:
            break
        hit = v
    return hit


def version_days(act: dict, rows: list, last: str) -> list[str]:
    """Every day on or before `last` that starts a version in the corpus or on main (main's In-force days)."""
    return sorted({v['from'] for v in act['versions'] if v['from'] <= last} | {d for _, d in rows if d <= last})


def absent_acts(repo: Path, head: str, listed: set[str], known_paths: dict[str, str], c: dict):
    """Acts main holds that this publish's corpus does not: withdrawn ones (a "Withdraws: <id>" trailer) and ones whose
    part is held back this cycle. Returns (index entries, their versions.json rows, recovered {id: folder})."""
    def versions_at(ref: str) -> dict[str, dict]:
        out = subprocess.run(['git', 'show', f'{ref}:{DP}data/versions.json'], cwd=repo, capture_output=True, text=True,
                             encoding='utf-8')
        try:
            return {a['slug']: a for a in json.loads(out.stdout).get('acts', [])} if not out.returncode else {}
        except ValueError:
            return {}
    rows_for = {s: a for s, a in versions_at(head).items() if s not in listed}
    # 2026-11-09 (alan-dell): GITLAW_RECOVER_FROM=<sha> restores the index rows and folders of acts an earlier
    # documentation commit dropped (croatia-law 728be25 lost 1,589 rows: a partial corpus and no <id>/<day> tags).
    rec = os.environ.get('GITLAW_RECOVER_FROM')
    if rec:
        _pj = subprocess.run(['git', 'show', f'{rec}:{DP}data/paths.json'], cwd=repo, capture_output=True, text=True,
                             encoding='utf-8')
        old_paths = json.loads(_pj.stdout).get('paths', {}) if not _pj.returncode and _pj.stdout else {}
        for s, a in versions_at(rec).items():
            if s not in listed and s not in rows_for:
                rows_for[s] = a
            if s not in listed and s in old_paths:
                known_paths.setdefault(s, old_paths[s])
    # Withdrawn: the trailer names the act; the "- <id>: <reason>" lines of the same commit give the reason.
    withdrawn: dict[str, tuple[str, str]] = {}
    for rec in git(repo, 'log', '--format=%x1e%h%x1f%B', '--grep=^Withdraws:', head).split('\x1e')[1:]:
        h, _, body = rec.partition('\x1f')
        lines = [ln.strip() for ln in body.splitlines()]
        reasons = {m.group(1): m.group(2) for m in (re.match(r'^- ([a-z0-9-]+): (.+)$', ln) for ln in lines) if m}
        for m in (re.match(r'^Withdraws:\s*([a-z0-9-]+)\s*$', ln) for ln in lines):
            if m and m.group(1) not in withdrawn:
                withdrawn[m.group(1)] = (h.strip(), reasons.get(m.group(1), ''))
                if m.group(1) not in rows_for and m.group(1) not in listed:  # its row from just before the withdrawal
                    row = versions_at(f'{h.strip()}^').get(m.group(1))
                    if row:
                        rows_for[m.group(1)] = row
    # Folders on main that no index names (an earlier publish dropped them): the act is recovered from the last
    # paths.json that had the folder, and its row from the versions.json of the same commit.
    path_for: dict[str, str] = {}
    have = set(known_paths.values()) | {a['path'] for a in c['acts'] if a.get('path')}
    folders = {f[:-len('/README.md')] for f in git(repo, 'ls-tree', '-r', '--name-only', head).splitlines()
               if f.endswith('/README.md') and f.split('/')[0] not in ('tools', 'data', DP.rstrip('/'))}
    # (2026-11-09: the docs prefix folder itself, e.g. registry/README.md, is no act; without this every publish ran
    # a whole-history pickaxe search for it)
    for f in sorted(folders - have):
        if '/' not in f and f in rows_for:  # a folder still named by its id (an act from before the layout move)
            continue
        hits = git(repo, 'log', '--format=%H', f'-S"{f}"', head, '--', 'data/paths.json').split()
        for h in hits:  # newest first: the commit that dropped it; its parent still names it
            old = L.paths_at(repo, f'{h}^')
            s = next((k for k, v in old.items() if v == f), None)
            if s:
                path_for[s] = f
                if s not in listed and s not in rows_for:
                    row = versions_at(f'{h}^').get(s)
                    if row:
                        rows_for[s] = row
                break
        else:
            print(f'append: folder {f} on main is in no index, and no paths.json ever named it', file=sys.stderr)
    extra = []
    for s, a in sorted(rows_for.items()):
        if s in withdrawn:
            extra.append({**a, '_withdrawn': True, 'path': None, '_withdrawn_by': withdrawn[s][0],
                          '_reason': withdrawn[s][1] or 'not in the current record'})
        else:  # held back from this publish: listed as it stands on main
            p = known_paths.get(s) or path_for.get(s) or a.get('path')
            extra.append({**a, 'path': p})
            rows_for[s] = {**a, 'path': p}
    return extra, rows_for, path_for


CREDIT_COLUMNS = ['act', 'in_force', 'provision', 'law', 'was_law', 'was_commit']


def credit_ledger(repo: Path, ref: str) -> list[dict]:
    """data/credits.csv on ref: every credit corrected so far, oldest first ([] before the first)."""
    out = subprocess.run(['git', 'show', f'{ref}:{DP}data/credits.csv'], cwd=repo, capture_output=True, text=True,
                         encoding='utf-8')
    return list(csv.DictReader(io.StringIO(out.stdout))) if not out.returncode and out.stdout else []


def main_credits(repo: Path, c: dict, acts: list[dict]) -> dict[str, dict[tuple[str, str], tuple[str, str]]]:
    """{act: {(in-force day, provision): (law key or '', commit)}} as main credits each change, for several acts from
    one history walk. A law commit is authored at the law's own address (<key>@<publisher domain>, build.law_email);
    a publisher or correction commit credits no law."""
    if not acts:
        return {}
    key_of = {B.law_email(c, k).lower(): k for k in c['laws']}
    owner = {f: a['slug'] for a in acts for f in dict.fromkeys([a.get('path') or a['slug'], a['slug']])}
    out = git(repo, 'log', '--no-merges', '--reverse', '--name-only',
              '--format=%x00%H%x09%(trailers:key=In-force,valueonly,separator=)%x09%(trailers:key=Kind,valueonly,'
              'separator=)%x09%ae', 'main', '--', *owner)
    have: dict[str, dict] = {a['slug']: {} for a in acts}
    for rec in out.split('\x00')[1:]:
        head, _, names = rec.partition('\n')
        h, d, kind, email = (head.split('\t') + ['', '', ''])[:4]
        d, kind, law = d.strip(), kind.strip(), key_of.get(email.strip().lower(), '')
        if not d or kind == 'layout':
            continue
        for name in names.splitlines():
            if not name.endswith('.md') or name.endswith('/README.md'):
                continue
            folder = name.rsplit('/', 1)[0]
            while folder and folder not in owner:  # a provision in a sub-folder of its act
                folder = folder.rsplit('/', 1)[0] if '/' in folder else ''
            if folder:
                have[owner[folder]][(d, name[len(folder) + 1:-3])] = ('' if kind == 'correction' else law, h)
    return have


def credit_fixes(repo: Path, c: dict, act: dict, last: str, ledger: list[dict],
                 have: dict[tuple[str, str], tuple[str, str]] | None = None) -> list[dict]:
    """Provisions whose change on a published day main credits to a different law (or to none) than the corpus now
    does, when the words are the same. Main's credit for (day, provision) is the Law trailer of the last commit with
    that In-force day that wrote the provision (none for a publisher commit or a text correction), overridden by the
    ledger; the corpus's is what build.plan would credit (the latest law named for it). Versions after `last` are
    appended as usual and need no correction."""
    have = dict(have if have is not None else main_credits(repo, c, [act])[act['slug']])
    for r in ledger:
        if r['act'] == act['slug']:
            have[(r['in_force'], r['provision'])] = (r['law'], have.get((r['in_force'], r['provision']), ('', ''))[1])
    want: dict[tuple[str, str], str] = {}
    for prev, cur in zip(act['versions'], act['versions'][1:]):
        if cur['from'] > last:
            break
        credits = cur.get('credits', {})
        for k in set(prev['units']) | set(cur['units']):
            if prev['units'].get(k, {}).get('text') != cur['units'].get(k, {}).get('text'):
                ls = sorted(set(credits.get(k, [])), key=lambda l: B.law_order(c['laws'], l))
                want[(cur['from'], k)] = ls[-1] if ls else ''
    return [{'act': act['slug'], 'in_force': d, 'provision': k, 'law': law, 'was_law': have[(d, k)][0],
             'was_commit': have[(d, k)][1]}
            for (d, k), law in sorted(want.items()) if (d, k) in have and have[(d, k)][0] != law]


def approved_corrections() -> dict[str, str]:
    """{act slug: public reason} from the file GITLAW_CORRECTIONS names (JSON). Only these acts are corrected; a
    listed act whose published text already matches the corpus needs nothing, so the list can stay in place."""
    p = os.environ.get('GITLAW_CORRECTIONS')
    if not p or not Path(p).exists():
        return {}
    return {k: str(v) for k, v in json.loads(Path(p).read_text(encoding='utf-8')).items()}


def correction_block(repo: Path, c: dict, act: dict, first_day: str, last: str, rows: list, reason: str) -> list[dict]:
    """The commits that correct one act append-only, from `first_day` (the first day main's text differs) to `last`
    (main's newest day for the act). Readers take the newest commit, in log order, that touches the act's folder with
    In-force on or before their day. So the block restates every version from `first_day` on, oldest first: the
    corrected one, then each later one again, so every day from `first_day` reads the corrected history; days before
    it still read the original commits. Nothing published is rewritten."""
    vs = act['versions']
    # (In-force day, the corpus text in force from it): the first wrong day with whatever version is in force then
    # (it may have started earlier: a repeal main dated too early leaves the earlier text in force on main's day),
    # then each later version day up to `last`, a day's last version being its text.
    start = in_force_on(act, first_day)
    if start is None:
        raise B.CorpusError(f'correction of {act["slug"]}: main has the act in force on {first_day}, before its first '
                            'version in the corpus; a correction can only restate text, so decide by hand')
    days = [(first_day, start)] + [(v['from'], v) for i, v in enumerate(vs) if first_day < v['from'] <= last
                                   and not (i + 1 < len(vs) and vs[i + 1]['from'] == v['from'])]
    path = act.get('path') or act['slug']
    cat = K.Cat(repo)
    try:
        head = git(repo, 'rev-parse', 'main').strip()
        have = K.tree_oids(cat, head, path, {})  # the act's files on main now: {name: blob}
        served = []
        trees: dict = {}
        for day, v in days:
            h = K.state_on(rows, day)
            want = {f'{k}.md': K.blob_oid(u['text']) for k, u in v['units'].items()}
            served.append((h, bool(h) and K.tree_oids(cat, h, [path, act['slug']], trees) == want))
    finally:
        cat.close()
    first = start
    if {f'{k}.md': K.blob_oid(u['text']) for k, u in first['units'].items()} == have:
        # A commit that changes no file under the folder is invisible to the reader rule: the original commits would
        # still be served for the days it should correct.
        raise B.CorpusError(f'correction of {act["slug"]}: the text in force from {first_day} equals the text on main '
                            'now, so no commit can correct it by touching the folder; decide by hand')
    out, prev = [], None
    for (day, v), (h, same) in zip(days, served):
        if prev is None:
            files = {f'{path}/{k}.md': u['text'] for k, u in v['units'].items()}
            files.update({f'{path}/{n}': None for n in have if n[:-3] not in v['units']})
        else:
            files = B.act_files(act, v['units'], prev['units'])
        label = act.get('short') or act.get('title') or act['slug']
        if prev is None:
            since = '' if v['from'] == day else f' (the text in force since {v["from"]}, still in force on this day)'
            msg = [f'Correction: {label} as in force on {day}', '',
                   f'{reason}'.rstrip() + (' ' if reason else '') +
                   f'The text served for this day {"was" if h else "was missing"} '
                   f'{"in commit " + h[:12] if h else "from main"}; this commit carries the official text{since}.']
        else:
            msg = [f'Correction: {label} as in force from {day}, restated', '',
                   f'Restates the text in force from {day} after the correction of {first_day}, so that every '
                   'later day reads the corrected history.' + ('' if same else ' The text served for this day '
                                                                  'differed from the official one as well.')]
        trailers = ['Kind: correction', f'Act: {act["slug"]}', f'In-force: {day}', f'Corrected: {day}']
        if h:
            trailers.append(f'{"Re-asserts" if same and prev is not None else "Corrects"}: {h}')
        out.append({'day': day, 'act': act, 'files': files, 'message': msg, 'trailers': trailers})
        prev = v
    return out


def to_append(c: dict, state: dict[str, str], docs_date: str) -> tuple[dict, set[str]]:
    """A corpus of only what is new. A held act keeps its newest held version as the base, so the first new version
    is planned as a change from it; that base's own 'start' commit is dropped (main has it)."""
    out = dict(c)
    acts, based = [], set()
    for act in c['acts']:
        vs = [v for v in act['versions'] if v['from'] <= docs_date]
        last = state.get(act['slug'])
        if last is None:
            if vs:
                acts.append({**act, 'versions': vs})
            continue
        held = [v for v in vs if v['from'] <= last]
        new = [v for v in vs if v['from'] > last]
        if new:
            acts.append({**act, 'versions': [held[-1]] + new})
            based.add(act['slug'])
    out['acts'] = acts
    return out, based


def published_log(repo: Path) -> list[dict]:
    text = subprocess.run(['git', 'show', f'main:{DP}data/commits.csv'], cwd=repo, capture_output=True, text=True,
                          encoding='utf-8').stdout
    log = []
    for r in csv.DictReader(io.StringIO(text)):
        log.append({'day': r['day'], 'kind': r['kind'], 'law': r['law'] or None, 'acts': r['acts'].split(),
                    'units': int(r['files'] or 0), 'branch': r['branch'], 'clamped': r['day'] < '1970-01-01'})
    return log


def acts_index(c: dict) -> str:
    """ACTS.md: every act in the corpus, grouped by kind, with its version count and span (generated each publish)."""
    import os as _os
    kinds = [k for k in _os.environ.get('GITLAW_LAYOUT_KINDS', '').split(',') if k]
    groups = [(k, (lambda kk: (lambda s: s.startswith(kk + '-')))(k)) for k in kinds]
    acts = sorted(c['acts'], key=lambda a: a['slug'])
    seen: set[str] = set()
    out = ['# Every act in this repository', '',
           f'{len(acts)} acts, {sum(len(a["versions"]) for a in acts):,} versions. Generated on every publish from the '
           'corpus; each folder\'s README.md lists its articles. Dates are in-force days.', '']
    for name, test in groups + [('Other acts', lambda s: True)]:
        rows = [a for a in acts if a['slug'] not in seen and test(a['slug'])]
        if not rows:
            continue
        seen |= {a['slug'] for a in rows}
        out += [f'## {name} ({len(rows)})', '', '| Folder | Act | Versions | From | Latest |', '| --- | --- | ---: | --- | --- |']
        for a in rows:
            vs = a['versions']
            title = a.get('title', a['slug']).replace('|', '/')
            out.append(f'| [`{a["slug"]}/`]({a["slug"]}/) | {title} | {len(vs)} | {vs[0]["from"]} | {vs[-1]["from"]} |')
        out.append('')
    return '\n'.join(out)


class Appender:
    """`git fast-import` onto an existing main; separate author and committer dates."""

    def __init__(self, repo: Path, head: str, head_ct: int, head_tz: str, cap: int | None = None):
        self.p = subprocess.Popen(['git', 'fast-import', '--quiet', '--done', '--date-format=raw'], cwd=repo,
                                  stdin=subprocess.PIPE)
        self.mark, self.parent, self.ct, self.tz = 0, head, head_ct, head_tz
        # Never date a commit after `cap` (the publish time): once the batch reaches it, commits share that second
        # (git and check_repo allow equal times; only a time going backwards or into the future is an error).
        self.cap = cap

    def _w(self, b: bytes):
        self.p.stdin.write(b)

    def _data(self, s: str):
        b = s.encode('utf-8')
        self._w(f'data {len(b)}\n'.encode() + b + b'\n')

    def commit(self, author: tuple[str, str], when: str, committer: tuple[str, str], message: str,
               files: dict[str, str | None]) -> int:
        self.mark += 1
        self.ct = self.ct + 1 if self.cap is None else max(self.ct, min(self.ct + 1, self.cap))
        self._w(f'commit refs/heads/main\nmark :{self.mark}\n'.encode())
        self._w(f'author {author[0]} <{author[1]}> {when}\n'.encode('utf-8'))
        self._w(f'committer {committer[0]} <{committer[1]}> {self.ct} {self.tz}\n'.encode('utf-8'))
        self._data(message)
        self._w((f'from {self.parent}\n' if isinstance(self.parent, str) else f'from :{self.parent}\n').encode())
        for path in sorted(files):
            if files[path] is None:
                self._w(f'D {path}\n'.encode('utf-8'))
            else:
                self._w(f'M 100644 inline {path}\n'.encode('utf-8'))
                self._data(files[path])
        self.parent = self.mark
        return self.mark

    def tag(self, name: str, mark: int):
        self._w(f'reset refs/tags/{name}\nfrom :{mark}\n\n'.encode('utf-8'))

    def finish(self):
        self._w(b'done\n')
        self.p.stdin.close()
        if self.p.wait():
            raise B.CorpusError('git fast-import failed')


def append(folder: Path, repo: Path, docs_date: str, dry_run: bool = False, committer_time: int | None = None,
           refresh_docs: bool = False) -> dict:
    c, raw = B.load(folder)
    errs = B.validate(c)
    if errs:
        raise B.CorpusError('corpus has problems:\n  ' + '\n  '.join(errs[:60]))
    c['_docs_date'] = docs_date
    # After the layout move (data/paths.json on main), every act lives in its official-identifier folder; new acts
    # get theirs from the same rule. Before it, folders are slugs (act['path'] unset).
    if DP:
        _pj = subprocess.run(['git', 'show', f'main:{DP}data/paths.json'], cwd=repo, capture_output=True, text=True,
                             encoding='utf-8')
        known_paths = json.loads(_pj.stdout).get('paths', {}) if not _pj.returncode and _pj.stdout else {}
        L.assign(c, known_paths)
    else:
        known_paths = L.paths_at(repo, 'main')
        if known_paths:
            L.assign(c, known_paths)
    if git(repo, 'status', '--porcelain', '--untracked-files=no').strip():
        raise B.CorpusError(f'{repo} has uncommitted changes; refusing')
    head = git(repo, 'rev-parse', 'main').strip()
    origin = subprocess.run(['git', 'rev-parse', '--verify', '-q', 'origin/main'], cwd=repo, capture_output=True,
                            text=True).stdout.strip()
    if origin and origin != head:
        # Only the layout cutover appends onto its own unpushed layout commit (GITLAW_ALLOW_AHEAD=1), and only when
        # main is origin/main plus commits of its own.
        ahead_ok = os.environ.get('GITLAW_ALLOW_AHEAD') == '1' and subprocess.run(
            ['git', 'merge-base', '--is-ancestor', origin, head], cwd=repo).returncode == 0
        if not ahead_ok:
            raise B.CorpusError(f'main {head[:12]} is not origin/main {origin[:12]}: fetch and reset to the published head first')
    head_ct, head_tz = git(repo, 'log', '-1', '--format=%ct %ci', 'main').split()[0::3]
    head_ct = int(head_ct)
    state, problems, rows, dirs = published_state(repo, c)
    # An act whose published text differs from the corpus is corrected only when listed in GITLAW_CORRECTIONS. Any
    # other stops the run as before, or with GITLAW_HOLD_MISMATCHED=1 is left out of this append (its part is
    # reported in held_parts, for the caller to leave out of its check too) so one act can't stop every publish.
    approved = approved_corrections()
    # A correction needs the act's published history; an act with none (its folder found without it) is never one.
    unapproved = {s: p for s, p in problems.items() if s not in approved or s not in state}
    if unapproved and os.environ.get('GITLAW_HOLD_MISMATCHED') != '1':
        msgs = [m for _, ms in unapproved.values() for m in ms]
        raise B.CorpusError('main does not match the corpus for versions it already holds:\n  ' + '\n  '.join(msgs[:40]))
    by_slug = {a['slug']: a for a in c['acts']}
    corrections = []
    for s in sorted(set(problems) - set(unapproved)):
        corrections += correction_block(repo, c, by_slug[s], problems[s][0], state[s], rows[s],
                                        B.public_note(approved[s]) or '')
    # Credits: a listed act whose words already match main may still credit a change to the wrong law. Text first:
    # an act corrected for its text this publish gets its credits checked on the next.
    ledger = credit_ledger(repo, head)
    credit_rows: dict[str, list[dict]] = {}
    listed = sorted(set(approved) & set(state) - set(problems))
    credited = main_credits(repo, c, [by_slug[s] for s in listed])  # one history walk for all of them
    for s in listed:
        rows_s = credit_fixes(repo, c, by_slug[s], state[s], ledger, credited[s])
        if rows_s:
            credit_rows[s] = rows_s
    fixes_by_act: dict[str, list[dict]] = {}
    for r in ledger + [r for rs in credit_rows.values() for r in rs]:
        fixes_by_act.setdefault(r['act'], []).append(r)
    for a in c['acts']:
        a['_credit_fixes'] = fixes_by_act.get(a['slug'], [])  # shown in the act's README
    sub, based = to_append({**c, 'acts': [a for a in c['acts'] if a['slug'] not in unapproved]}, state, docs_date)
    # Batch cap: at most GITLAW_MAX_NEW_ACTS new acts per append (in slug order; new versions of acts main already holds
    # always go through), so a large backlog streams out in many short, fully checked pushes instead of one huge one.
    max_new = int(os.environ.get('GITLAW_MAX_NEW_ACTS', '0') or 0)
    if max_new:
        new_slugs = sorted(a['slug'] for a in sub['acts'] if a['slug'] not in state)
        keep_new = set(new_slugs[:max_new])
        sub = {**sub, 'acts': [a for a in sub['acts'] if a['slug'] in state or a['slug'] in keep_new]}
    commits = B.plan(sub) if sub['acts'] else []
    commits = [x for x in commits if not (x['kind'] == 'start' and x['act']['slug'] in based)]
    existing_tags = set(git(repo, 'tag', '--list').split())
    co = c['country']
    committer = ('Lexifina', f'{co["slug"]}-law@lexifina.com')
    clock = B.Clock(co['tz'])
    summary = {'head': head, 'new_acts': sorted(a['slug'] for a in sub['acts'] if a['slug'] not in based),
               'extended_acts': sorted(based), 'commits': len(commits) + len(corrections),
               'corrected_acts': sorted({x['act']['slug'] for x in corrections}),
               'credit_corrections': {s: len(rs) for s, rs in credit_rows.items()},
               'held_acts': sorted(unapproved),
               'held_parts': sorted({by_slug[s].get('_part') for s in unapproved} - {None})}
    if dry_run or (not commits and not corrections and not credit_rows and not refresh_docs):
        summary['main'] = head
        return summary
    # Committer time: the publish time when given (so the host shows when the law was added), never earlier than the
    # published head; each appended commit one second after the last. The batch log (data/appends.csv) records it, so
    # a rebuild replays the same times and the same hashes.
    # The batch ends at the publish time, not after it: check_repo rejects commits dated in the future.
    start_ct = max(head_ct, (committer_time - len(commits) - len(corrections) - len(credit_rows) - 2)
                   if committer_time else 0)
    w = Appender(repo, head, start_ct, '+0000' if committer_time else head_tz, cap=committer_time)
    tags: dict[str, int] = {}
    log = []
    # Corrections go first, so the extensions planned from the corrected text follow them.
    fixed_when = f'{committer_time} +0000' if committer_time else clock.stamp(docs_date)
    for x in corrections:
        B.check_trailers(x['trailers'])
        text = '\n'.join(x['message']).rstrip('\n') + '\n\n' + '\n'.join(x['trailers']) + '\n'
        w.commit(committer, fixed_when, committer, text, x['files'])
        log.append({'day': x['day'], 'kind': 'correction', 'law': None, 'acts': [x['act']['slug']],
                    'units': len(x['files']), 'branch': 'main', 'clamped': x['day'] < '1970-01-01'})
    # Credit corrections: one commit per act, with no In-force trailer, so no reader ever takes text from it. It adds
    # the rows to data/credits.csv and rewrites the act's README, so `git log` on the act's folder shows it too.
    ledger_rows = list(ledger)
    names = {k: l.get('label', k) for k, l in c['laws'].items()}
    def law_name(k: str) -> str:
        return names.get(k, k) if k else "no law (a change the publisher's record leaves unexplained)"
    for s, rows_s in credit_rows.items():
        act = by_slug[s]
        ledger_rows += rows_s
        buf = io.StringIO()
        wr = csv.DictWriter(buf, fieldnames=CREDIT_COLUMNS, lineterminator='\n')
        wr.writeheader()
        wr.writerows(ledger_rows)
        readme = f'{act.get("path") or s}/README.md'
        files = {f'{DP}data/credits.csv': buf.getvalue(), readme: B.tables({**c, 'acts': [act]}, [])[readme]}
        lines = [f'- In force {r["in_force"]}, {r["provision"]}: {law_name(r["law"])} (was {law_name(r["was_law"])}).'
                 for r in rows_s]
        reason = B.public_note(approved[s]) or ''
        msg = [f'Credit correction: {act.get("short") or s}', ''] + ([reason, ''] if reason else []) + \
              ['The words are unchanged; only the law each change is credited to. git blame still names the commit that '
               'wrote the words; data/credits.csv records the corrected credit.', ''] + \
              lines[:60] + ([f'- and {len(lines) - 60} more (data/credits.csv)'] if len(lines) > 60 else [])
        trailers = ['Kind: correction', f'Act: {s}', f'Credits: {len(rows_s)}'] + \
                   [f'Corrects: {h}' for h in dict.fromkeys(r['was_commit'] for r in rows_s) if h]
        B.check_trailers(trailers)
        w.commit(committer, fixed_when, committer, '\n'.join(msg).rstrip('\n') + '\n\n' + '\n'.join(trailers) + '\n',
                 files)
        log.append({'day': docs_date, 'kind': 'correction', 'law': None, 'acts': [s], 'units': len(rows_s),
                    'branch': 'main', 'clamped': False})
    for x in commits:
        if x['kind'] == 'start':
            author, msg, trailers = B.start_message(c, x)
            files = B.act_files(x['act'], x['version']['units'], None)
            touched = [x['act']['slug']]
        else:
            author, msg, trailers = B.change_message(c, x)
            files = {}
            for p in x['parts']:
                files.update(B.act_files(p['act'], p['cur']['units'], p['prev']['units'], p['keys']))
            touched = [p['act']['slug'] for p in x['parts']]
        B.check_trailers(trailers)
        text = '\n'.join(msg).rstrip('\n') + '\n\n' + '\n'.join(trailers) + '\n'
        mark = w.commit(author, clock.stamp(x['day']), committer, text, files)
        for s in touched:
            tags.setdefault(f'{s}/{x["day"]}', mark)
        if x['kind'] == 'law':
            tags.setdefault(f'law/{x["law"]}', mark)
        elif x['kind'] == 'start' and x['act'].get('first_law'):
            tags.setdefault(f'law/{x["act"]["first_law"]}', mark)
        log.append({'day': x['day'], 'kind': x['kind'],
                    'law': x.get('law') or (x['act'].get('first_law') if x['kind'] == 'start' else None),
                    'acts': touched, 'units': len(files), 'branch': 'main', 'clamped': x['day'] < '1970-01-01'})
    full_log = published_log(repo) + log
    all_tags = {t: 0 for t in existing_tags} | {t: m for t, m in tags.items()}
    # Documentation covers acts with text history on main after this append: those main held before it and those it
    # appends. An act in the corpus that is not appended (it takes effect later, or is held back) gets no README and
    # no index row yet; a README-only folder would otherwise read as a published act with no text.
    on_main = set(state) | {a['slug'] for a in sub['acts']}
    tagged = {t.split('/', 1)[0] for t in existing_tags if '/' in t and not t.startswith('law/')}  # <id>/<day> tags
    # 2026-11-09 (alan-dell): a repository whose acts carry only law/<id> tags (croatia-law) lost every act this
    # publish's corpus did not hold from its indexes; an act data/paths.json places on main has text there too.
    tagged |= set(known_paths)
    c_pub = {**c, 'acts': [a for a in c['acts'] if a['slug'] in on_main]}
    docs = B.docs_files(c_pub, folder, raw, full_log, all_tags, docs_date)
    # Ship this script beside build.py, so the repository says how its appended commits were made.
    for name in ('append.py', 'layout.py'):
        docs[f'tools/build/{name}'] = (HERE / name).read_text(encoding='utf-8').replace('\r\n', '\n')
    # The indexes (ACTS.md, data/acts.json, data/versions.json, data/paths.json) list every act main holds, not only the
    # acts in this publish's corpus: a part held back for a cycle must not drop its acts from them, and only a
    # correction commit's "Withdraws: <id>" trailer marks an act withdrawn. Rows for acts not in this corpus come from
    # main: head's versions.json, else the last one that had them.
    listed = {a['slug'] for a in c['acts']}
    extra, rows_for, path_for = absent_acts(repo, head, listed, known_paths, c)
    tagged |= set(path_for) | set(known_paths)  # acts an earlier publish dropped from the indexes: their folders are still on main
    # Of the acts this corpus doesn't have, keep the withdrawn and those with history (held back this cycle).
    no_text = [a for a in extra if not a.get('_withdrawn') and a['slug'] not in tagged]
    extra = [a for a in extra if a not in no_text]
    # 2026-11-10: an act this corpus does not hold keeps the label main's data/acts.json gives it ("amended, not
    # consolidated", "repealed, not consolidated", "cited later, not consolidated", written by a parser or a label
    # commit), with its amended_by / cited_by; acts with no such label are listed exactly as before.
    _aj = subprocess.run(['git', 'show', f'main:{DP}data/acts.json'], cwd=repo, capture_output=True, text=True,
                         encoding='utf-8')
    try:
        _main_rows = {r['slug']: r for r in json.loads(_aj.stdout).get('acts', [])} if not _aj.returncode else {}
    except ValueError:
        _main_rows = {}
    for a in extra:
        r = _main_rows.get(a['slug']) or {}
        if str(r.get('status', '')).endswith(', not consolidated') and not a.get('_withdrawn'):
            a['label'] = r['status']
            for k in ('amended_by', 'cited_by'):
                if r.get(k):
                    a[k] = r[k]
    index = {**c, 'acts': c_pub['acts'] + extra}
    # READMEs that earlier documentation commits wrote into folders with no text are removed (documentation, not
    # history: each documentation commit regenerates it).
    readmes = {f for f in git(repo, 'ls-tree', '-r', '--name-only', 'main').splitlines() if f.endswith('/README.md')}
    for a in [a for a in c['acts'] if a['slug'] not in on_main and a['slug'] not in tagged] + no_text:
        p = f'{a.get("path") or known_paths.get(a["slug"]) or a["slug"]}/README.md'
        if p in readmes:
            docs[p] = None
    docs['ACTS.md'] = L.acts_md(index) if known_paths else acts_index(c_pub)
    docs['data/acts.json'] = L.acts_json(index)
    vj = json.loads(docs['data/versions.json'])
    vj['acts'] += [rows_for[a['slug']] for a in extra]  # kept, so the next publish still knows them
    docs['data/versions.json'] = json.dumps(vj, ensure_ascii=False, indent=1) + '\n'
    if known_paths or DP:
        _sp = subprocess.run(['git', 'show', f'main:{DP}data/paths.json'], cwd=repo, capture_output=True, text=True,
                             encoding='utf-8')
        since = json.loads(_sp.stdout).get('since', docs_date) if not _sp.returncode and _sp.stdout else docs_date
        pj = json.loads(L.paths_json(index, since))
        # paths.json never drops a folder: an entry main has stays, whatever this corpus holds.
        for s, p in {**known_paths, **path_for}.items():
            if (s in on_main or s in tagged) and not any(a['slug'] == s and a.get('_withdrawn') for a in extra):
                pj['paths'].setdefault(s, p)
        pj['paths'] = dict(sorted(pj['paths'].items()))
        docs['data/paths.json'] = json.dumps(pj, ensure_ascii=False, indent=0) + '\n'
    # Tools that moved or are no longer shipped (the old flat tools/ layout, tools/acts/) are removed, not left stale.
    if DP:  # everything but the acts' own READMEs goes under the prefix; nothing outside it is touched
        act_dirs = {(a.get('path') or a['slug']) for a in index['acts']}
        docs = {(k if (k.endswith('/README.md') and k.rsplit('/', 1)[0] in act_dirs) else DP + k): v
                for k, v in docs.items()}
    shipped_tools = {p for p in git(repo, 'ls-tree', '-r', '--name-only', 'main', f'{DP}tools/').splitlines() if p}
    for p in shipped_tools - set(docs):
        docs[p] = None
    # One row per append batch: which head it started from, its first committer time, its commit count and its acts.
    prev_log = subprocess.run(['git', 'show', f'{head}:{DP}data/appends.csv'], cwd=repo, capture_output=True,
                              text=True, encoding='utf-8').stdout or 'batch,base,committer_start,commits,acts\n'
    n_batch = max(len(prev_log.strip().splitlines()) - 1, 0) + 1
    new_acts = ' '.join(sorted({a['slug'] for a in sub['acts']}))
    docs[f'{DP}data/appends.csv'] = prev_log + f'{n_batch},{head},{start_ct + 1}{f'..{committer_time}' if committer_time else ''},{len(log) + 1},{new_acts}\n'
    # The documentation commit is ours, made at publish time: its author date is the publish time too (the host shows
    # the head commit's author date as "updated N minutes ago"). Law commits keep their in-force day as author date.
    docs_when = f'{max(w.ct, min(w.ct + 1, committer_time))} +0000' if committer_time else clock.stamp(docs_date)
    w.commit(committer, docs_when, committer,
             f'Documentation, tables, tools and REPRODUCE.md\n\nAppended {len(log)} commits to {head[:12]}; corpus '
             f'sha256 {B.hashlib.sha256(raw).hexdigest()}, data retrieved {docs_date}.\n', docs)
    for name, mark in sorted(tags.items()):
        if name not in existing_tags:
            w.tag(name, mark)
    w.finish()
    git(repo, '-c', 'checkout.workers=0', 'checkout', '-q', '-f', 'main')
    # fast-import writes each new tag as a loose file; tens of thousands of them made every `git tag --list` take
    # seconds. Packing the new ones each run keeps it cheap (local refs only; nothing published changes).
    git(repo, 'pack-refs', '--all')
    new_head = git(repo, 'rev-parse', 'main').strip()
    if subprocess.run(['git', 'merge-base', '--is-ancestor', head, new_head], cwd=repo).returncode:
        raise B.CorpusError('internal: the published head is not an ancestor of the new main')
    summary.update({'main': new_head, 'tags_added': sum(1 for t in tags if t not in existing_tags)})
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('folder')
    ap.add_argument('--repo', required=True)
    ap.add_argument('--docs-date', required=True)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--refresh-docs', action='store_true', help='append a documentation commit even with no new law commit')
    ap.add_argument('--committer-time', type=int, help='epoch seconds to date the appended commits from (the publish time)')
    a = ap.parse_args()
    print(append(Path(a.folder), Path(a.repo), a.docs_date, a.dry_run, a.committer_time, a.refresh_docs))


if __name__ == '__main__':
    main()
