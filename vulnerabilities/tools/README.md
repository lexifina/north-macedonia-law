# Tools

The scripts behind this North Macedonia-only run of global-law's vulnerability detectors. They run inside a copy of
[global-law/vulnerabilities/scripts](https://github.com/lexifina/global-law/tree/main/vulnerabilities/scripts) (Python 3.11+,
`pip install duckdb pyarrow`), with a bare clone of this repository at `sources/corpus/north-macedonia-law.git`, a blobless
bare clone of global-law at `sources/global-law.git`, and a `pins.json` that names only north-macedonia-law and its commit
(language `mk`). `build_vulns.py`, `flags_chart.py`, `late_ext_select.py`, `late_ext_rebut.py`, `mk_gap_check.py` and
`config.json` sit two folders above the scripts.

| Script | What it does |
| --- | --- |
| `run.sh` | Indexes the history and texts, maps paths to each act's folder (`map_moved.py`), adds this repository's history rows, and runs the detectors (`bash run.sh <shards>`) |
| `map_moved.py` | Rewrites the paths in the history table to each act's folder after a repository moves its act folders (`data/paths.json`); this repository's map names 20,062 folders, and no path in its history needed rewriting |
| `add_root.py` | Builds the history rows from this repository's own commits (global-law replays it only up to an earlier commit), so the history detectors read every change |
| `run_triage.sh` | The model review: the 40 strongest flags per category, the 40 strongest lapsed time limits in law texts, and the empty periods (there were none here) |
| `late_ext_select.py` | Chooses the empty periods for global-law's review of that category (there were none here) |
| `late_ext_rebut.py` | Adds the second, arguing-against pass to the empty-period review, as `triage.py` does for the other categories |
| `build_vulns.py` | Builds `data/flags.csv`, `data/reviewed.csv` and `data/by_category.csv` from the findings and the model review |
| `flags_chart.py` | Draws the chart at the top of the READMEs, in Macedonian and English, light and dark |
| `config.json` | The repository, the Macedonian category names and chart labels, and what is not law: `data/`, `tools/`, the root files and each act's `README.md` |
| `mk_gap_check.py` | Counts the "до", "заклучно со" and "најдоцна до" phrases before a date in law texts, how many the time-limit scan reads, and the phrases with a month and year but no day (the numbers in the READMEs; `python -X utf8 mk_gap_check.py <scripts dir>`) |
| `apply_patches.py` | Applies the patches below to a copy of the scripts, each once, in a fixed order, and records them in `.kit_patches.json` (`python -X utf8 apply_patches.py <scripts dir>`); later patches edit text earlier ones inserted, so a patch is never run twice on the same copy. `patch_mk.py` is last in its order |
| `patch_detectors.py` | Fixes blind spots found in runs on several country repositories in the copy of global-law's scripts |
| `patch_detectors_vi.py`, `patch_es.py`, `patch_ca.py`, `patch_en.py`, `patch_de.py`, `patch_en_et.py`, `patch_co.py`, `patch_sv.py`, `patch_pt.py`, `patch_ru.py`, `patch_r18.py`, `patch_xk.py`, `patch_mo.py`, `patch_lu.py`, `patch_li.py`, `patch_mx.py`, `patch_mn.py`, `patch_mt.py`, `patch_np.py`, `patch_en_nr.py`, `patch_dv.py`, `patch_az.py` | The same for the dates, references and currency names of other languages and jurisdictions; they change nothing here |
| `patch_mk.py` | The Macedonian fixes made for this repository, in the same form (`python -X utf8 patch_mk.py <scripts dir>`; a fix already in place is skipped; see below) |
| `patch_build_r7.py` | Brings a country's `build_vulns.py` up to date: lapsed-limit reviews matched on 400 characters of context, not 80, and a file deleted before a folder move linked under its old folder (already applied to the `build_vulns.py` here) |
| `redo.py` | Runs `apply_patches.py`, then again only the scans and detectors the fixes change, the model review of lapsed time limits, references to deleted provisions and empty periods, and the build (`python -X utf8 redo.py <run folder> [--keep-reviews] [--full]`; `--full` runs every step again through `run.sh`, `map_moved.py` among them, sends every category to the model and keeps every review already made; `EMBED_FONTS` names the script that embeds the chart's fonts); not needed here, as the fixes were in place before the last review |

The model review needs `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` and `LLM_AUTH` set for an OpenAI-compatible Responses API.
It was run as `bash run_triage.sh '^(data|tools)/|^(README|ACTS\.md|REPRODUCE\.md|LICENSE|jurisdiction\.json)|/README\.md$'`.

Changes made for this repository:

- `add_root.py` counts the `Kind` value `court` as a change to the law, with `law`, `decree` and `publisher`, which it
  already counted: here it marks six acts' texts (three decrees with the force of law amending another, and three
  decisions, two of them on the Constitutional Court), not court decisions. 22,823 commits count as law changes; the 42
  `correction` commits that write a file, which change only the act a change is credited to, keep their own kind.
- `patch_mk.py`, Macedonian dates, references and currencies, made after testing the scan on this repository's text:
  - `ingest/textscan.py`: the month names "јануари", "мај", "јуни" and "јули" (the other eight are spelled as in
    Bulgarian, which the scan already read), "до крајот на <year>" (31 December), "заклучно со" (up to and including)
    before a date, and "најдоцна" as a deadline. Lapsed time limits went from 1,373 flags to 1,917: 366 dates with one of
    the four months, 149 "заклучно со" and 27 "до крајот на" among the 544 added.
  - `ingest/textscan.py`: "по" before a date is "after" in Macedonian ("по 1 јануари 2009"), not the Russian "up to",
    so in a corpus whose language is `mk` it no longer starts a time limit: 90 flags went (1,917 to 1,827). The second
    run sent the three flags that took their place among the strongest to the model, keeping the 80 reviews already made;
    3 reviews of "по" flags no longer match a flag ("80 of 83 reviews matched").
  - `ingest/refscan.py`: the `clen-N` files of the Constitution and the Law on Personal Data Protection are read as
    articles; no reference to a deleted provision was found before or after.
  - `detectors/currency_scan.py`: in a corpus whose language is `mk`, amounts in dinars ("динари") and Deutsche Marks
    ("германски марки") written in Cyrillic are dead currencies; the detector read only the Latin "dinar". Dead
    currencies went from 0 to 137; the denar ("денари") is not touched.
  `mk_gap_check.py` finds 1,885 "до", "заклучно со" and "најдоцна до" phrases with a day, or "до крајот на" with a year,
  in law texts at this commit, all of them read, and 90 with only a month and a year, which the scan does not read.
- `flags_chart.py` draws the legend for flags outside law texts only when there are any; here there are none.
- `patch_dv.py` and `patch_az.py` joined the kit after this copy was set up; they were applied before the last run and
  change nothing here.
