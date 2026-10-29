# How this repository was made (North Macedonia)

This file is written by the build on 2026-10-29, the day the source data was retrieved.

## Sources

| Source | Address | Retrieved | Licence |
| --- | --- | --- | --- |
| Службен весник на Република Северна Македонија (free issues) | <https://slvesnik.com.mk/besplaten-pristap-do-izdanija.nspx> | 2026-10-29 | Official texts, not protected by copyright (Закон за авторското право и сродните права) |
| Собрание на Република Северна Македонија: Устав | <https://www.sobranie.mk/ustav-na-rm.nspx> | 2026-10-29 | Official texts, not protected by copyright (Закон за авторското право и сродните права) |

Every word of law in this repository comes from these sources. Lexifina reads them and records each act's dated versions, and the laws that made each change, in one corpus file. This repository is built from that file.

## What is in it

| Act | Title | Versions | Span |
| --- | --- | ---: | --- |
| `ustav` | Устав на Република Северна Македонија | 9 | 1991-11-17 to 2019-02-12 |
| `zakon-za-zastita-na-licnite-podatoci` | Закон за заштита на личните податоци | 3 | 2020-02-24 to 2025-05-21 |

The build made 40 commits:

| Commits | Kind |
| ---: | --- |
| 2 | first version of an act |
| 38 | one law on its in-force day |
| 0 | changes no amendment note explains, credited to the publisher |

Other counts:
- 0 commits on the branch `idno`: laws not yet in force on 2026-10-29.
- 0 commits dated 1 January 1970: in force before 1970, with the real day in their `In-force` trailer.
- 40 laws in the record.
- 52 tags.

## Dates

Each commit is dated at noon (Europe/Skopje) on the day the change took effect. The other dates, enacted and published, are trailers.

When a law gives no date of its own, the default rule applies: Устав чл. 52 ст. 4: a law takes effect no earlier than the eighth day after its publication in the Official Gazette, unless for especially justified reasons it sets an earlier day; each act's own final article states its day. The Constitution and its amendments state their own day. https://www.sobranie.mk/ustav-na-rm.nspx

## How the history is built

`tools/build.py` turns the corpus into this history in one pass: one commit per law, on the day it took effect. The same corpus always gives the same commit hashes. `tools/check_repo.py` reads every version back from git and compares it with the corpus. `tools/on.py` shows the law as it stood on any date. The corpus format is described in `tools/SCHEMA.md`.

- Corpus: sha256 `8da3a10c48acd4b74c95f8c3ce21a5929bd4f617ee0737309b39e4336e0a2e70` (2276018 bytes).
- Builder blob hashes (`git hash-object`): `build.py` `a4faa25af85facb64b956b372f6b528aafdd7ec1`, `check.py` `3d00541d258745461ab52b1b0cb4e1716a27a95d`, `on.py` `644a6c0857fbcc72562032b58d57aa113fee0641`.
- Python 3.12.10; git version 2.46.0.windows.1.

## Checks

12 versions of 2 acts read back from git on the day each took effect and on the day before: 0 mismatches. Trailers: 0 commits whose trailers git cannot parse. Health: 0 merge commits on main, 0 commit dates that go backwards, 0 commits dated after the build.

No second build of this corpus has been compared yet.

## Decisions made by hand

- There is no official consolidated text of the Constitution: officially it is the 1991 text plus Amendments I-XXXVI, each 'a constituent part of the Constitution'. The history here applies each amendment, by its own instruction point, to the provisions it names.
- Words of the 1991 text and of Amendments I-XVIII come from the Assembly's certified copy (UstavnaRmizmeni.pdf), whose text layer is clean; the gazette issues of 1991-2001 are scans whose publisher-made OCR layer has errors (e.g. 'делот' for 'денот').
- The Assembly copy merges some paragraphs (Art. 3 and 4 show 2 paragraphs where the gazette has 3, and Amendment XXXII replaces 'став 2' of Art. 4). Paragraph breaks are therefore taken from the gazette layout (an indented first line), aligned word by word with the Assembly copy, plus the copy's own line-final breaks and dash alineas.
- Amendments XIX-XXXVI are read from the born-digital gazette issues (84/2003, 107/2005, 3/2009, 49/2011, 6/2019); for XIX-XXXII the words were compared with the Assembly copy.
- One glyph is lost in the Assembly copy ('с#' in Amendment IV's preamble); it is read as 'сè', the word in that phrase ('зачувување и развој на сè што е вредно').
- Latin look-alike letters inside Cyrillic words (typesetting) are mapped to Cyrillic.
- Articles are files clen-NNN.md with the article heading as the first line; one paragraph (став) or alinea per line. Chapter and section headings are their own files (glava-NN, glava-NN-NN).
- Amendment I point 1, XX and XXXV 'supplement' (се дополнува) an article without saying where: their text is added as new paragraphs at the end of the article.
- Amendments II, XIII and XIV supplement a paragraph (став) with a sentence: the sentence is appended to that paragraph, verbatim, after one space.
- Amendment VI supplements alinea 2 of Article 8 with a phrase that is not a sentence of that alinea: it is inserted verbatim as its own line right after that alinea; no words are composed.
- Amendment XI point 2 'supplements' Article 77(2) but restates the whole paragraph with the new sentence; it replaces that paragraph, so the old wording is not duplicated.
- Amendment XVIII adds 'a new paragraph 4' to Article 131: inserted as paragraph 4, the old paragraph 4 becoming 5 (later decisions cite 'член 131 став 5' for promulgation, which confirms it).
- Amendment XXXIV's edits to the Preamble (as replaced by Amendment IV) are applied as the exact string deletions, replacement and insertion it quotes; double spaces left by a deletion collapse to one.
- Amendment XXXIII renames 'Република Македонија' to 'Република Северна Македонија' and 'Македонија' to 'Северна Македонија' everywhere except Article 36, in headings too (upper-case forms included).
- Amendments XXXIII-XXXVI were adopted on 2019-01-11 and published on 2019-01-12 but took effect on 2019-02-12 (Government notice, Службен весник 36/2019, no. 623); they are dated 2019-02-12.
- Every other amendment says it takes effect on the day of its promulgation, and the 1991 Constitution (Art. 134) on the day the Assembly promulgated it (1991-11-17).
- Закон за заштита на личните податоци: in force on the eighth day after publication (its Art. 124): published 2020-02-16, in force 2020-02-24. Arts. 119-121 give controllers and regulations 18 months to conform; that is not a separate commencement of any provision.
- Сл. весник 294/2021 takes effect on the eighth day after publication (its Art. 6): published 2021-12-27, in force 2022-01-04. Сл. весник 101/2025 takes effect on publication (its Art. 4): 2025-05-21.
- 101/2025 quotes 'Европската Унија' with a capital У; the 2020 text writes 'Европската унија' in Art. 48(3) and Art. 56 (and 'Унија' in Art. 48(2)). The insertion points are matched ignoring the case of that one letter; the words of the law are not recased.
- 101/2025 says 'a comma is added and the words ... are added' after the quoted words: that is a comma, a space and the quoted words. In Art. 48(3) the quoted anchor already ends in a comma, so the insertion is a space, the quoted words and their own closing comma.
- Article titles are kept as a second line ('## title'); an article without its own title (Arts. 76 and 82) falls under the title before it in the gazette.

## What AI did and did not do

The scripts that read the sources and build this repository were written by Claude Code running Claude Opus 5.5 (`claude-opus-5-5`) under Lexifina's direction.

No model wrote, translated or corrected legal text. No model runs at build time. Every word of law in this repository comes from the official sources above.

The commit messages are written by `build.py` from the source data.

Model use at data-build time:
- none: no model produced or edited any text or data; parsing is rule-based Python.
