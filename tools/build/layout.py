"""Where each act lives in the repository: <kind>/<year>/dv<issue>-<id>/ (zakon/2010/dv029-9940/).

Bulgarian acts carry no running number: an act is identified by its State Gazette (Държавен вестник) issue and year.
The folder is the kind, the Gazette year, the zero-padded issue and the National Assembly's record id (which keeps two
acts of one issue apart). Act slugs (zakon-2010-dv029-9940, konstitutsiya) stay the act ids for tags and tools.
Library used by append.py; `python -X utf8 layout.py move <repo> --when <epoch>` appends the layout commit.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

KIND_NAMES: dict = {}
KIND_ORDER: list = []
import os as _os
_KINDS = [k for k in _os.environ.get('GITLAW_LAYOUT_KINDS', '').split(',') if k]
SLUG_RE = re.compile(r'^(' + '|'.join(map(re.escape, _KINDS or ['zzznone'])) + r')-(\d{4})-([a-z0-9][a-z0-9-]*)$')
FIXED: dict = {}
AMENDING = re.compile(r'\b(?:amend|izmen|ndryshim|τροποπο|измен|dopun|spremem)', re.I)
CODE_NAME = re.compile(r'^$a')


def act_path(slug: str, title: str = '') -> str:
    """<kind>/<year>/<id> for new acts named <kind>-<year>-<id>; any other slug keeps its own folder."""
    if slug in FIXED:
        return FIXED[slug]
    m = SLUG_RE.match(slug)
    if m:
        return f'{m.group(1)}/{m.group(2)}/{m.group(3)}'
    return slug


def parts_of(path: str) -> tuple[str, str, str]:
    bits = path.split('/')
    return bits[0], (bits[1] if len(bits) > 1 else ''), (bits[2] if len(bits) > 2 else '')


def paths_at(repo: Path, ref: str = 'main') -> dict[str, str]:
    """{slug: path} recorded on ref (data/paths.json), or {} before the layout move."""
    out = subprocess.run(['git', 'show', f'{ref}:data/paths.json'], cwd=repo, capture_output=True, text=True,
                         encoding='utf-8')
    if out.returncode:
        return {}
    return json.loads(out.stdout).get('paths', {})


def assign(c: dict, known: dict[str, str]) -> None:
    """Give every act its folder: the recorded one, else its official identifier. Two acts may never share one."""
    seen: dict[str, str] = {}
    for a in c['acts']:
        p = known.get(a['slug']) or act_path(a['slug'], a.get('title', ''))
        if p in seen and seen[p] != a['slug']:
            # Two corpus acts claim one identifier (a duplicate entry): keep publishing, the later one under alte/.
            print(f'layout: {a["slug"]} and {seen[p]} both claim {p}; {a["slug"]} filed under alte/', file=sys.stderr)
            p = f'alte/{a["slug"]}'
        seen[p] = a['slug']
        a['path'] = p


def status(a: dict) -> str:
    if a.get('_withdrawn'):
        return 'withdrawn'
    last = a['versions'][-1] if a['versions'] else {}
    if 'units' in last and not last['units']:
        return f'repealed {last["from"]}'
    if AMENDING.search(a.get('title', '')):
        return 'amending act'
    return 'in force'


def code_name(a: dict) -> str | None:
    if a['slug'] == 'konstitutsiya':
        return 'Конституция на Република България'
    if AMENDING.search(a.get('title', '')):
        return None
    m = CODE_NAME.search(a.get('title', ''))
    if not m:
        return None
    name = (m.group(1) or m.group(2) or '').strip()
    return name if name.lower().startswith('cod') else None


def acts_rows(c: dict) -> list[dict]:
    rows = []
    for a in c['acts']:
        p = a.get('path') or a['slug']
        kind, year, number = parts_of(p if not a.get('_withdrawn') else act_path(a['slug'], a.get('title', '')))
        vs = a['versions']
        rows.append({'slug': a['slug'], 'path': None if a.get('_withdrawn') else p, 'kind': kind, 'year': year,
                     'number': number, 'withdrawn_by': a.get('_withdrawn_by'), 'reason': a.get('_reason'),
                     'title': a.get('title', ''), 'short': a.get('short', ''), 'code': code_name(a), 'status': status(a),
                     'versions': len(vs), 'from': vs[0]['from'] if vs else None, 'latest': vs[-1]['from'] if vs else None,
                     'portal': a.get('url')})
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    rows.sort(key=lambda r: (order.get(r['kind'], 99), r['year'], int(r['number']) if r['number'].isdigit() else 0))
    return rows


def acts_json(c: dict) -> str:
    return json.dumps({'country': c['country']['code'], 'acts': acts_rows(c)}, ensure_ascii=False, indent=0) + '\n'


def paths_json(c: dict, since: str) -> str:
    return json.dumps({'layout': '<kind>/<year>/dv<issue>-<id>', 'since': since,
                       'note': 'Act folders before the layout move were named by slug; tools/on.py reads both.',
                       'paths': {a['slug']: a['path'] for a in sorted(c['acts'], key=lambda a: a['slug'])
                                 if a.get('path') and not a.get('_withdrawn')}},
                      ensure_ascii=False, indent=0) + '\n'


def acts_md(c: dict) -> str:
    rows = acts_rows(c)
    esc = lambda s: (s or '').replace('|', '/')  # noqa: E731
    out = ['# Every act in this repository', '',
           f'{len(rows)} acts, {sum(r["versions"] for r in rows):,} versions. Each act lives in a folder named by its '
           'official identifier, `<kind>/<year>/<number>/` (the law in State Gazette issue 29 of 2010, National Assembly record 9940, is `zakon/2010/dv029-9940/`); its README.md '
           'lists its articles and the decisions made by hand. Dates are in-force days. Generated on every publish; '
           'the same list is in [data/acts.json](data/acts.json).', '']
    codes = [r for r in rows if r['code']]
    if codes:
        out += ['## Codes and the Constitution, by name', '', '| Code | Act | Folder | Versions | Latest |',
                '| --- | --- | --- | ---: | --- |']
        for r in sorted(codes, key=lambda r: r['code']):
            out.append(f'| {esc(r["code"])} | {esc(r["short"] or r["title"])} | [`{r["path"]}/`]({r["path"]}/) | '
                       f'{r["versions"]} | {r["latest"]} |')
        out.append('')
    gone = [r for r in rows if r['status'] == 'withdrawn']
    rows = [r for r in rows if r['status'] != 'withdrawn']
    for kind in KIND_ORDER + sorted({r['kind'] for r in rows} - set(KIND_ORDER)):
        group = [r for r in rows if r['kind'] == kind]
        if not group:
            continue
        n_amend = sum(1 for r in group if r['status'] == 'amending act')
        out += [f'## {KIND_NAMES.get(kind, kind)} ({len(group)}' + (f', of which {n_amend} amending acts' if n_amend else '')
                + ')', '', '| Act | Folder | Status | Versions | From | Latest |', '| --- | --- | --- | ---: | --- | --- |']
        for r in group:
            out.append(f'| {esc(r["title"])} | [`{r["path"]}/`]({r["path"]}/) | {r["status"]} | {r["versions"]} | '
                       f'{r["from"]} | {r["latest"]} |')
        out.append('')
    if gone:
        out += [f'## Withdrawn from the record ({len(gone)})', '',
                'Acts published here and later found not to belong (for example, titled as Government decisions but '
                'issued by another body). Their folders were removed by a correction commit; the commits that added '
                'them stay in the history. Their ids are retired: a later act with the same number gets the id with '
                '`-2` appended.', '', '| Act id | Reason | Removed by |', '| --- | --- | --- |']
        for r in sorted(gone, key=lambda r: r['slug']):
            out.append(f'| `{r["slug"]}` | {esc(r.get("reason") or "")} | {r.get("withdrawn_by") or ""} |')
        out.append('')
    return '\n'.join(out)


def move(repo: Path, when: int) -> str:
    """Append the layout commit to main: rename every act folder on main from its slug to its official identifier."""
    git = lambda *a: subprocess.run(['git', *a], cwd=repo, capture_output=True, text=True, encoding='utf-8',  # noqa: E731
                                    check=True).stdout
    if paths_at(repo, 'main'):
        raise SystemExit('main already has data/paths.json: the layout move is done')
    vj = json.loads(git('show', 'main:data/versions.json'))
    top = set(git('ls-tree', '--name-only', 'main').split())
    # Only acts whose folder is on main move; an act whose folder a correction removed (withdrawn) gets no path.
    c = {'country': {'code': vj.get('country', '')}, 'acts': [a for a in vj['acts'] if a['slug'] in top]}
    for a in c['acts']:
        a['path'] = a['slug']
    head = git('rev-parse', 'main').strip()
    ct = max(int(git('log', '-1', '--format=%ct', 'main').strip()), when)
    moves = [(a["slug"], a["path"]) for a in c["acts"] if a["slug"] in top and a["slug"] != a["path"]]
    since = __import__('datetime').datetime.fromtimestamp(ct, __import__('datetime').timezone.utc).strftime('%Y-%m-%d')
    msg = (f'Layout: record every act folder (data/paths.json)\n\n'
           f'Each act moves from a folder named by its id to <kind>/<year>/dv<issue>-<id>/.\n'
           f'No text changes. Act ids, tags and history are unchanged; before this commit the folders have their old\n'
           f'names (git log --follow, or tools/on.py, which reads both). data/paths.json maps every id to its folder.\n\n'
           f'Kind: layout\nMoved: {len(moves)} acts\nPaths: data/paths.json\n')
    pj = paths_json(c, since).encode('utf-8')
    m = msg.encode('utf-8')
    who = 'Lexifina <' + _os.environ.get('GITLAW_EMAIL', 'law@lexifina.com') + '>'
    body = (f'commit refs/heads/main\nauthor {who} {ct} +0000\ncommitter {who} {ct} +0000\n'.encode('utf-8')
            + f'data {len(m)}\n'.encode() + m + b'\n' + f'from {head}\n'.encode())
    for old, new in moves:
        body += f'R "{old}" "{new}"\n'.encode('utf-8')
    body += f'M 100644 inline data/paths.json\ndata {len(pj)}\n'.encode() + pj + b'\n' + b'done\n'
    p = subprocess.run(['git', 'fast-import', '--quiet', '--done', '--date-format=raw'], cwd=repo, input=body,
                       capture_output=True)
    if p.returncode:
        raise SystemExit(f'fast-import failed: {p.stderr.decode("utf-8", "replace")[-800:]}')
    subprocess.run(['git', '-c', 'checkout.workers=0', 'checkout', '-q', '-f', 'main'], cwd=repo, check=True)
    return git('rev-parse', 'main').strip()


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'move':
        when = int(sys.argv[sys.argv.index('--when') + 1]) if '--when' in sys.argv else int(__import__('time').time())
        print(move(Path(sys.argv[2]), when))
    else:
        sys.exit(__doc__)
