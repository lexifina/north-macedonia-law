# North Macedonia: law history in git

The law of North Macedonia as a git history: one file per provision, one commit per amendment or law on the day it took effect, and the amendment or law that made each change as the commit's author. `git blame` names the amendment behind every paragraph; `git log` lists every change to an article; picking a date gives the text in force that day.

It follows the shared design of [lexifina/global-law](https://github.com/lexifina/global-law), which places it at `mk/`. The texts are in Macedonian (Cyrillic), the official language.

**What is in it now**

| Folder | Act | Versions | History |
| --- | --- | ---: | --- |
| `ustav/` | Устав на Република Северна Македонија (the Constitution) | 9 | As promulgated on 17 November 1991, through all 36 amendments (I-XXXVI, 1992-2019) |
| `zakon-za-zastita-na-licnite-podatoci/` | Закон за заштита на личните податоци (the Law on Personal Data Protection, 2020) | 3 | As enacted (in force 24 February 2020), through both amending laws (294/2021, 101/2025) |

**The Constitution is never officially consolidated.** Officially it is the 1991 text plus Amendments I-XXXVI, each of which says it is "a constituent part of the Constitution" and names the provision it replaces, supplements or deletes ("Со овој амандман се заменува ставот 2 на членот 4"). This repository applies each amendment to the provision it names, so every article file reads as it stood on each day, and each changed paragraph is credited to its amendment.

## A lawyer's questions as git commands

| Question | Command |
| --- | --- |
| What did Article 4 say on 1 January 2010? | `python tools/on.py 2010-01-01 ustav clen-004` |
| The same, with raw git (bash) | `git show $(git rev-list -1 --before="2010-01-01T23:59:59Z" main -- ustav/clen-004.md):ustav/clen-004.md` |
| The same, in PowerShell | `$r = git rev-list -1 --before="2010-01-01T23:59:59Z" main -- ustav/clen-004.md; git show "$($r):ustav/clen-004.md"` |
| Which amendment wrote each paragraph of Article 3? | `git blame ustav/clen-003.md` |
| Every change to Article 104, word by word | `git log -p --word-diff ustav/clen-104.md` |
| Every amendment in order | `git log --reverse --format="%ad %an" --date=short main` |
| Everything Amendment XXXIII changed (the name change) | `git log --author="Амандман XXXIII" --stat` |
| The whole Constitution on a date | `python tools/on.py 2005-01-01 ustav` |
| Article 48 of the data protection law before and after 2025 | `python tools/on.py 2025-05-20 zakon-za-zastita-na-licnite-podatoci clen-048`, then `2025-05-21` |

After a rebuild, refresh tags with `git fetch --force --tags`: a plain fetch keeps stale tags.

## Dates

- Each commit is dated at **noon, Europe/Skopje, on the day the change took effect**. The adoption date and the Official Gazette (Службен весник) publication date are trailers; the in-force day is in `In-force:`, which `tools/on.py` reads.
- **The 1991 Constitution** took effect on the day the Assembly promulgated it, 17 November 1991 (its Article 134); it was published in Службен весник 52/1991 on 22 November 1991.
- **Amendments I-XXXII** each say they take effect on the day of their promulgation by the Assembly: 6 January 1992 (I-II), 1 July 1998 (III), 16 November 2001 (IV-XVIII, the Ohrid Framework Agreement amendments), 26 December 2003 (XIX), 7 December 2005 (XX-XXX), 9 January 2009 (XXXI) and 12 April 2011 (XXXII). Amendments promulgated together are separate commits on the same day, in number order.
- **Amendments XXXIII-XXXVI** (the Prespa agreement amendments, including the new name) were adopted on 11 January 2019 and published on 12 January 2019 (Службен весник 6/2019), but took effect only with the Final Agreement and NATO accession protocol. The Government's notice in Службен весник 36/2019 (no. 623) states they took effect on **12 February 2019**, which is the commit date.
- **Ordinary laws** state their own day in their final article. The Law on Personal Data Protection (Службен весник 42/2020, 16 February 2020) took effect on the eighth day after publication, 24 February 2020; its amendment in 294/2021 (27 December 2021) on the eighth day, 4 January 2022; the one in 101/2025 on its publication day, 21 May 2025. The eighth day is counted from the day after publication (the Constitution, Art. 52, sets the eighth day as the earliest).
- `--before="YYYY-MM-DD"` alone uses your clock's time of day; use `T23:59:59Z` as above, or the tool.

## How it is checked

- **Every amendment's instruction is checked against what it was applied to.** The operation for each amendment is transcribed from its own final point ("Со овој амандман се заменува ..."); the build stops if the article, paragraph or alinea it names does not exist, or if the instruction does not name the numbers used. The replaced text of every operation is logged (`tools/parse.py` writes `parse.log`), and each one matches the subject of its amendment.
- **The words of the amendments were compared in two official copies.** For Amendments XIX-XXXII the born-digital Official Gazette and the Assembly's certified copy agree word for word except in Amendment XIX (four words: the gazette text is used) and two words the copy runs together in Amendment XXVI.
- **The data protection law as enacted was compared word for word with the publisher's own single-act copy** of Службен весник 42/2020 (`tools/verify.py`, result in `verification.json`): 30,387 words, no difference in the law's text. The five differences the comparison lists are the copy's page furniture and typography (the GDPR footnote, a heading set with a title, the copy's "Член  50" with two spaces, its "веб страница" for the gazette's "веб-страница", and a title hyphen).
- **Every edit of an amending law applies at exactly the place it names.** The data protection amendments are word-level ("the words „до“ are replaced by „со“"): each quoted string must appear in the amending law's own gazette text, and its target must occur exactly once in the paragraph it names (twice where the law says "the first and second sentence"), or the build stops.
- Every version is read back from git on the day it took effect and on the day before (`tools/check_repo.py`); both must match `corpus.json`. Results are in `REPRODUCE.md`.
- Health checks: no merge commits on `main`, commit dates never go backwards, nothing dated in the future, and every commit's trailers parse with `git interpret-trailers --parse`.
- The repository is rebuilt from the same data a second time and must give identical hashes.

## What "author" means

The author of a commit is the amendment or law that made the change, with an e-mail address that cannot exist (`amandman-05@slvesnik.invalid`), so `git blame` and `git log --author` name it. The committer is always Lexifina.

- **The first commit** is the Constitution as promulgated in 1991, credited to it.
- **Credits come only from the source**: each changed provision is credited to the amendment whose instruction names it. Amendment XXXIII renames "Република Македонија" to "Република Северна Македонија" (and "Македонија" to "Северна Македонија") throughout except Article 36, so it is credited in 59 files.
- Blame credits whole lines (one paragraph or alinea per line). Where an amendment appended a sentence to a paragraph (II, XIII, XIV), use `git log -p --word-diff` to see which words it added.

## What it does not show

- **How the amendments read on their own.** Each amendment is applied to the provision it names; its own numbered points and its promulgation decision are not files. Where an amendment "supplements" an article without saying where, its text is added at the end of the article (I, XX, XXXV); Amendment VI's phrase supplementing alinea 2 of Article 8 is its own line after that alinea, since joining it would mean composing words. Every such choice is listed in `REPRODUCE.md`.
- **The Constitutional Law on the implementation of the Constitution** (1991) and the Constitutional Law on the implementation of Amendments XXXIII-XXXVI (2019) are separate acts and are not here yet.
- **The Criminal Code** (Кривичен законик, Службен весник 37/1996). Its 1996 text exists online only as a scanned gazette issue whose text layer is OCR output with errors, it has been amended by 31 laws (80/1999 to 188/2023), and no official consolidated text exists, so neither its history nor a verified current text can be built from official sources yet. The same holds for the Law of Obligations (18/2001; the Ministry of Justice's consolidated file is unofficial and undated).
- **The footnote** printed with the data protection law (that it harmonises the law with Regulation (EU) 2016/679) is not law text and is not in the files; each article's title is the second line of its file.
- **Court rulings.** The Constitutional Court has never struck words from the Constitution.

## How it is built

`tools/fetch.py` downloads the sources once through curl (cached by the sha256 of the URL, every request logged). `tools/parse.py` turns the cache into `corpus.json`: `tools/pdftext.py` reads the PDFs' text layers, `tools/ustav.py` reads the Constitution and amendments, and `tools/ustav_replay.py` applies the amendments, `tools/zakon.py` reads ordinary laws from the born-digital gazette, and `tools/zzlp.py` applies the data protection law's amending laws. The shared builder `tools/build.py` writes the history with `git fast-import`; the same corpus always gives the same hashes. `REPRODUCE.md` records every command, tool version, data hash and decision.

- **Words** of the 1991 text and Amendments I-XVIII come from the Assembly's certified copy (`UstavnaRmizmeni.pdf`, made from Word): the Gazette issues of 1991-2001 are scans whose publisher-made text layer has OCR errors. Words of Amendments XIX-XXXVI come from the born-digital Gazette issues.
- **Paragraph breaks** come from the Gazette layout (each paragraph's first line is indented), aligned word by word with the Assembly copy, because the copy runs some paragraphs together (in the Gazette, Article 4 has three paragraphs, and Amendment XXXII replaces the second).
- No OCR was run, and no AI model wrote or changed legal text. Claude Opus 5.5 wrote the scripts and chose the sources; see `REPRODUCE.md`.

## Sources

| Source | Kind | Past versions | Names the amending act | Bulk | Notes |
| --- | --- | --- | --- | --- | --- |
| [Службен весник на Република Северна Македонија](https://slvesnik.com.mk/besplaten-pristap-do-izdanija.nspx) | Official Gazette | every issue since 1945, free (PDF) | yes (each amendment names what it changes) | issue lists by year (JSON) | 1945-2001 issues are scans with an OCR text layer; later issues are born-digital |
| [Собрание: Устав](https://www.sobranie.mk/ustav-na-rm.nspx) | Assembly | 1991 text + Amendments I-XXXII (certified copy); English text | yes | no | The 2019 copy adds XXXIII-XXXVI only as scanned pages |
| [LDBIS](https://ldbis.pravda.gov.mk/) (Ministry of Justice) | Legal database | metadata: publication, in-force and application dates, linked amending laws | yes | no | Used for the planned ordinary laws |

Research guides: [GlobaLex](https://www.nyulawglobal.org/globalex/) and the [Law Library of Congress Guide to Law Online](https://www.loc.gov/research-centers/law-library-of-congress/researcher-resources/guide-to-law-online/).

## Licence

The texts are official texts of the Republic of North Macedonia, which the Law on Copyright and Related Rights (Закон за авторското право и сродните права) excludes from copyright protection. They are taken from Службен весник на Република Северна Македонија and the Assembly of the Republic of North Macedonia. Lexifina's own contributions are dedicated to the public domain under CC0 1.0; see `LICENSE`.

This repository is not legal advice and is not an official publication.

---

## Македонски

Уставот на Република Северна Македонија како историја во git: една датотека по член, еден commit по амандман, на денот кога влегол во сила, со амандманот како автор. Уставот од 1991 година е применет со сите амандмани I-XXXVI (1992-2019), секој на одредбата што ја заменува, дополнува или брише. Законот за заштита на личните податоци (2020) е со двата закона за негово изменување (294/2021 и 101/2025). Текстовите се од „Службен весник на Република Северна Македонија“ и од заверениот препис на Собранието. Ова не е службено пречистен текст, ниту правен совет.
