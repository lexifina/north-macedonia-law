"""How current is an act here? Reads the freshness files (data/freshness/) of this repository. Standard library only;
it works offline and never connects anywhere: to check an act yourself, open the official link it prints.

    python tools/freshness.py <act id>                 # one act, by id (data/paths.json lists them)
    python tools/freshness.py <kind>/<year>/<id>       # the same, by folder
    python tools/freshness.py --stale                  # every act not checked within its threshold
    python tools/freshness.py --stale --kind <kind>    # the same, for one kind
    python tools/freshness.py --status pending-change --kind <kind>
    python tools/freshness.py --summary                # the overall summary

Statuses: current, pending-change, stale, unverified, source-unavailable (data/freshness.json explains each). The
files give each act's status on the day they were written (`as_of`); this tool also reads each act's `check_due`
against today (or --as-of), so an act whose check has fallen due since then shows as stale.
"""
import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUSES = ('current', 'pending-change', 'stale', 'unverified', 'source-unavailable')


def paths_file(root):
    """data/paths.json, or registry/data/paths.json in a repository whose act indexes sit under registry/ (Albania)."""
    return 'registry/data/paths.json' if (root / 'registry' / 'data' / 'paths.json').exists() else 'data/paths.json'


def load_json(root, rel):
    try:
        return json.loads((root / rel).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        sys.exit(f'{rel} not found or unreadable in {root}: this checkout has no freshness data')


def read_shard(root, rel):
    try:
        with open(root / rel, encoding='utf-8', newline='') as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def day(s):
    try:
        return dt.date.fromisoformat((s or '')[:10])
    except ValueError:
        return None


def status_on(row, today):
    """The status on `today`: a current act whose check fell due before today is stale. A repealed act has no
    check_due and stays as it is."""
    due = day(row.get('check_due'))
    if row['status'] == 'current' and due and today > due:
        return 'stale'
    return row['status']


def candidate_shards(folder, index):
    """The shard files an act in `folder` can be in, the likeliest first, then every other shard of its kind."""
    parts = folder.split('/')
    kind = parts[0]
    years = [p for p in parts[1:] if len(p) == 4 and p.isdigit()]
    names = []
    if kind == 'local' and len(parts) >= 4:
        names.append(f'local/{parts[1]}/{parts[2]}/{years[0] if years else "undated"}')
    names.append(f'{kind}/{years[0] if years else "undated"}')
    files = {s['shard']: s['file'] for s in index.get('shards', [])}
    first = [files[n] for n in names if n in files]
    rest = [s['file'] for s in index.get('shards', []) if s['kind'] == kind and s['file'] not in first]
    return first + rest


def find(root, target, index, paths):
    """(row, folder, shard file) for an act id or folder; (None, folder, None) when there is no row."""
    target = target.strip().strip('/')
    if '/' in target:
        folder = target
        act = next((a for a, p in paths.items() if p == folder), None)
        if act is None:
            return None, folder, None
    else:
        act, folder = target, paths.get(target)
    files = candidate_shards(folder, index) if folder else [s['file'] for s in index.get('shards', [])]
    for rel in files:
        for row in read_shard(root, rel):
            if row['act'] == act:
                return row, folder, rel
    return None, folder, None



def describe(row, folder, rel, summary, today):
    st = status_on(row, today)
    last, due = day(row.get('last_verified')), day(row.get('check_due'))
    lines = [row['act'] + (f' ({folder})' if folder else '')]
    if last:
        n = (today - last).days
        when = f'last verified {last}, ' + ('today' if n == 0 else f'{n} day ago' if n == 1 else f'{n} days ago')
        when += f'; next check due {due}' if due else '; repealed, no further check needed'
    else:
        when = 'never verified against the official source for later changes'
    lines.append(f'  status:         {st} ({when})')
    if st != row['status']:
        lines.append(f"                  ({row['status']} when the data was written, {summary.get('as_of')})")
    lines.append(f"  meaning:        {summary.get('statuses', {}).get(st, '')}")
    if row.get('method'):
        lines.append(f"  method:         {row['method']}: {summary.get('methods', {}).get(row['method'], '')}")
    if row.get('newest_change') or row.get('newest_change_date'):
        where = {'yes': 'in this repository', 'no': 'NOT yet in this repository'}.get(row.get('change_in_repo'), '')
        lines.append(f"  newest change:  {row.get('newest_change') or '(not named)'}"
                     + (f" ({row['newest_change_date']})" if row.get('newest_change_date') else '')
                     + (f', {where}' if where else ''))
    if row.get('official_url'):
        lines.append(f"  check by hand:  {row['official_url']}")
    lines.append(f"  data:           {rel} (as of {summary.get('as_of')})")
    return '\n'.join(lines)


def print_summary(summary, kind=None):
    t = summary.get('totals', {})
    print(f"Checked against the official sources: {summary.get('checked_against_official_source')}; "
          f"{t.get('pct_verified_7d')}% of {t.get('acts')} acts verified in the last 7 days, "
          f"{t.get('pct_verified_30d')}% in the last 30 (data as of {summary.get('as_of')})")
    print()
    print(f"{'':12}{'acts':>9}{'current':>9}{'pending':>9}{'stale':>9}{'unverif.':>9}{'unavail.':>9}{'7 days':>9}")
    rows = [('all', t)] + list(summary.get('by_group', {}).items())
    if kind:
        rows = [(kind, summary.get('by_kind', {}).get(kind, {}))]
    for name, b in rows:
        s = b.get('by_status', {})
        print(f"{name:12}{b.get('acts', 0):>9}{s.get('current', 0):>9}{s.get('pending-change', 0):>9}"
              f"{s.get('stale', 0):>9}{s.get('unverified', 0):>9}{s.get('source-unavailable', 0):>9}"
              f"{str(b.get('pct_verified_7d', '-')) + '%':>9}")
    print()
    for src in summary.get('sources', []):
        sites = f", {src['sites']} sites" if src.get('sites') else ''
        down = f", unavailable since {src['unavailable_since']}" if src.get('unavailable_since') else ''
        print(f"- {src['source']}: last successful check {src.get('last_successful_check') or 'never'}{sites}{down}")
    print()
    for g, v in summary.get('thresholds', {}).items():
        print(f"threshold {v['days']} days: {v['label']}")
    print()
    for st, text in summary.get('statuses', {}).items():
        print(f'{st}: {text}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('act', nargs='?', help='an act id or its folder (<kind>/<year>/<id>)')
    ap.add_argument('--stale', action='store_true', help='list the acts not checked within their threshold')
    ap.add_argument('--status', choices=STATUSES, help='list the acts with this status')
    ap.add_argument('--kind', help='only this kind (the first part of a folder)')
    ap.add_argument('--summary', action='store_true', help='the overall summary')
    ap.add_argument('--as-of', help='judge each check_due against this day (YYYY-MM-DD) instead of today')
    ap.add_argument('--repo', default=str(ROOT), help='the repository checkout (default: the one holding this tool)')
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # a console that cannot show a letter gets '?', not a crash
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass
    root = Path(a.repo)
    today = day(a.as_of) if a.as_of else dt.datetime.now(dt.timezone.utc).date()
    if today is None:
        ap.error('--as-of takes a day, YYYY-MM-DD')
    summary = load_json(root, 'data/freshness.json')
    index = load_json(root, 'data/freshness/index.json')

    if a.summary:
        print_summary(summary, a.kind)
        return 0
    if a.stale or a.status:
        want = 'stale' if a.stale else a.status
        n = 0
        for s in index.get('shards', []):
            if a.kind and s['kind'] != a.kind:
                continue
            for row in read_shard(root, s['file']):
                if status_on(row, today) == want:
                    n += 1
                    print(f"{row['act']}\t{row.get('last_verified') or '-'}\t{row.get('method') or '-'}\t"
                          f"{row.get('official_url') or '-'}")
        print(f'{n} acts {want}' + (f' of kind {a.kind}' if a.kind else '') + f' on {today}', file=sys.stderr)
        return 0
    if not a.act:
        ap.print_help()
        return 2
    paths = load_json(root, paths_file(root)).get('paths', {})
    row, folder, rel = find(root, a.act, index, paths)
    if not row:
        print(f'{a.act}: no freshness record' + (f' (folder {folder})' if folder else ' (unknown act id)'),
              file=sys.stderr)
        return 1
    print(describe(row, folder, rel, summary, today))
    return 0


if __name__ == '__main__':
    sys.exit(main())
