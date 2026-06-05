# DIN 276:2018-12 — where to get the full catalogue

## What we already have (free, parsed)

| File | Source | Content |
|------|--------|---------|
| `din276_weka_sirados_2018_full.txt` | [WEKA/SIRADOS PDF](https://www.weka.de/bi/sirados/download/Download_DIN276.pdf) | **320** unique 3-digit codes with 2018 titles (levels 1–3) |
| `din276_weka_2008_to_2018_map.tsv` | Same PDF | 278 dual-column migration rows (2008→2018) |
| `din276_kg300_weka_2018.txt` | WEKA slice | **78** codes KG 300–399 only |
| `din276_ocerp_partials_kg300.tsv` | [OpenConstructionERP](https://github.com/datadrivenconstruction/OpenConstructionERP) | Partial L1–L2 + seed examples (not norm-complete) |
| `din276_ocerp_bim_revit_kg300.tsv` | OCERP `classification_mapper.py` | Revit/IFC category → DIN 276 hints |
| `din276_ocerp_cwicr_kg300_prefixes.txt` | OCERP `golden_set.yaml` | CWICR `KG.LL.PPP` prefixes for matcher eval |

Regenerate WEKA: `python3 scripts/parse_din276_weka_pdf.py`

Regenerate KG 300 + OCERP partials:

```bash
git clone --depth 1 https://github.com/datadrivenconstruction/OpenConstructionERP /tmp/OpenConstructionERP
python3 scripts/extract_din276_ocerp_kg300.py --ocerp /tmp/OpenConstructionERP
```

**Assessment:** The WEKA file is a **comparison overview**, not the Beuth norm text, but the 2018 column appears to list the complete **Tabelle 1** code set (BKI and vendors cite ~300+ KG positions for 2018-12; we have 320). It does **not** include per-code **Anmerkungen** (normative notes) or Tables 2–4 (Mengen/Bezugseinheiten).

---

## Authoritative full norm (paid)

| Source | URL | Notes |
|--------|-----|--------|
| **DIN Media / Beuth** | https://www.dinmedia.de/de/norm/din-276/293154016 | **DIN 276:2018-12** — 56 pages, German; replaces DIN 276-1, 276-4, 277-3 |
| DOI | https://dx.doi.org/10.31030/2873248 | Citation / library link |
| English edition | https://www.dinmedia.de/en/standard/din-276/293154016 | Same content, translated |

After purchase (PDF download or org licence):

1. Save as `custom_recipes/mappings/reference/_source_DIN276_official.pdf`
2. Run: `python3 scripts/parse_din276_weka_pdf.py --pdf _source_DIN276_official.pdf --out din276_official_2018_full.txt`

*(Parser uses `pdftotext -layout` + table heuristics; official layout may need tuning.)*

**Do not** use Scribd / random upload sites — copyright infringement and often incomplete OCR.

---

## Free / legal supplements (not full norm)

| Source | What you get |
|--------|----------------|
| [Bauprofessor Lexikon](https://www.bauprofessor.de/kostengliederung-nach-din-276/) | Structure explanation + short excerpts |
| [AKBW — DIN 276 neu 2018-12](https://www.akbw.de/berufspraxis/planungsinfos-und-themen/kosten-flaechen-rauminhalte/din-276-neu-ausgabe-2018-12) | Official chamber summary of changes |
| [BKI Bildkommentar Leseprobe](https://bki-files.de/downloads/bildkommentar/Leseprobe_BKI_Bildkommentar_DIN276_DIN277.pdf) | Commentary **sample** only (not full Table 1) |
| [Normsplash sample](https://www.normsplash.com/Samples/DIN/117299186/DIN-276-2018-en.pdf) | TOC + foreword preview only |
| Excel templates (registration) | [Capmo](https://www.capmo.com/vorlagen/vorlage-din-276-excel), [Phase0](https://www.phase0.com/blog/din-276-excel-vorlage) — good for **cross-check** against WEKA extract |

---

## Commercial tools (if org already licenses)

- **Baunormenlexikon** — full norm text + Table 1 with Anmerkungen (subscription)
- **BKI Formulare / Bildkommentar DIN 276** — Excel + commented tables
- **AVA software** (RIB, CaliforniaX, DBD-BIM, etc.) — built-in KG trees (export if licence allows)

---

## OpenConstructionERP (partial, KG 300 focus)

[OpenConstructionERP](https://github.com/datadrivenconstruction/OpenConstructionERP) does **not** ship a full 320-code DIN 276 table. Useful for **BIM → KG** routing and **CWICR** cost-line prefixes under KG 300:

| OCERP source | KG 300 content |
|--------------|----------------|
| `dach_pack/config.py` | 9 codes (300 + eight L2); no L3; 370 label differs from WEKA |
| `de-din276-exchange/deTemplate.ts` | UI picker L1–L2 (includes 380; config omits 380) |
| `classification_mapper.py` | Revit coarse map + material refinements (`330.10`, `331.10`, …) |
| `tests/eval/golden_set.yaml` | CWICR hierarchical codes (`330.10.020`, …) |

Extracted into `din276_ocerp_*_kg300.*` — regenerate with `extract_din276_ocerp_kg300.py`.

---

## Recommended path for ifcpipeline

1. **Use `din276_weka_sirados_2018_full.txt` now** for Kostengruppe → prefix lookup and mapping (320 codes).
2. **Cross-check** one free Excel template row count vs our 320 codes if you need confidence.
3. **Purchase DIN 276:2018-12** once for Anmerkungen + Mengen tables and HOAI/legal wording.
4. Drop official PDF into `reference/` and extend parser output to `din276_official_2018_full.txt` (with Anmerkungen column if extractable).

---

## Obscure channels searched (May 2026)

Searched beyond obvious Beuth/WEKA pages: archives, gov open data, GitHub/GitLab, Zenodo, FragDenStaat, university repos, GAEB/AVA ecosystems, buildingSMART, Austrian standards, slide decks, FOI leaks.

### No full free norm found

| Channel | Result |
|---------|--------|
| [archive.org](https://archive.org) | No DIN 276:2018-12 text |
| [govdata.de](https://www.govdata.de) | DIN 18960 / Baupreisindizes only, not DIN 276 KG tree |
| [FragDenStaat](https://fragdenstaat.de) | No norm PDFs; agencies point to Beuth |
| [Zenodo](https://zenodo.org) | No DIN 276 catalogue datasets |
| Scribd / silo.tips / academia.edu | Pirated or unrelated PDFs — **do not use** |
| [Normsplash sample](https://www.normsplash.com/Samples/DIN/117299186/DIN-276-2018-en.pdf) | Preview pages only (TOC), not Tabelle 1 |
| cosoba.de “Vergleich aller Kostengruppen” | **404** (formerly linked from eLCA docs) |

### Partial but useful (legal)

| Source | Depth | URL / file |
|--------|-------|------------|
| **WEKA/SIRADOS PDF** (parsed) | **320 codes**, levels 1–3 titles | Already in `din276_weka_sirados_2018_full.txt` |
| [DeWiki](https://dewiki.de/Lexikon/DIN_276) / [Wikipedia DE](https://de.wikipedia.org/wiki/DIN_276) | **~50 codes** (levels 1–2 only, no xxx detail) | Good sanity check for Hauptgruppen |
| **BBSR research report** (Bund) | Tables 11–14 excerpts (e.g. full KG 330 third level + Bezugseinheiten) | [bim-kostenplanung/endbericht.pdf](https://www.bbsr.bund.de/BBSR/DE/forschung/programme/zb/Auftragsforschung/3Rahmenbedingungen/2021/bim-kostenplanung/endbericht.pdf) — cached as `_source_DIN276_pdftotext.txt` is separate; download and grep `KG 3` |
| [buildingSMART FGK / bSDD](https://search.bsdd.buildingsmart.org/uri/buildingsmart-de/QToFGK300/1.0) | IFC classification for **FGK 300** (DIN 276 level 1–3 in use cases), not full 100–800 export | Public API returned 404 for direct class URI in May 2026 — dictionary may need UI browse |
| [CTB AVA wiki](https://www.ctb.de/_wiki/ava/DIN_276_2018_12_(AVAnce_XML).php) | Documents `DIN276_18` catalog in GAEB/XML tooling | Confirms 2018 catalog exists in commercial AVA stacks |
| [NLW AVA-Pflichtenheft](https://www.nlbl.niedersachsen.de/download/208208/AVA-Pflichtenheft_des_SBN_Version_2024-01.pdf) | Requires **3rd level** `DIN276_18` on all LV positions; STLB auto-assigns | Explains where full lists live: **STLB-Bau / RIB iTWO** Kostengliederungskatalog |
| [RIB Datenbibliothek PDF](https://www.rib-software.com/pdf/de/rib-datenbibliothek-fuer-bauplanung-und-betrieb.pdf) | STLB positions carry **DIN 276-08 and 276-18** per row | Export via licensed AVA only |
| [ÖNORM B 1801-1 supplements](https://www.austrian-standards.at/de/produkte-loesungen/service-extras/download-library/supplements-oenorm-b-1801-1) | Free **xlsx** tables — **Austrian** system, not 1:1 DIN 276 | `Tabelle_1_2022-03.xlsx` (~87 KB) |
| [SIRADOS gratis landing](https://www.sirados.de/neue-din276-gratis-download) | Same WEKA comparison PDF behind email form | Duplicate of WEKA URL |
| [OpenConstructionERP](https://github.com/datadrivenconstruction/OpenConstructionERP) | Claims native **DIN 276** validation in BOQ — worth cloning for seed data if AGPL fits | Search repo for `din_276` / cost group JSON |

### Best obscure cross-check (still not “full norm”)

1. Register for **Capmo** or **Phase0** free Excel → export sheet to CSV → diff code list vs our 320 rows.
2. If you have **RIB / DBD / STLB** licence → export Kostengliederungskatalog `DIN276_18` from GAEB/XML (see CTB wiki).
3. Parse **BBSR endbericht.pdf** for Bezugseinheiten (Table 14 style) per KG block — complements titles with m²/m rules.

### Conclusion from deep search

There is **no** legitimate open PDF of the full 56-page DIN 276:2018-12 on the public internet. The closest **free complete code+title list** remains the **WEKA 2018 column** (320 codes). For **Anmerkungen**, **Mengen/Bezugseinheiten** (Tables 2–4), and legal wording → **Beuth purchase** or **Baunormenlexikon** subscription.

---

## HOAI note

Honorar calculation may still reference **DIN 276-1:2008-12** per contract/HOAI §4 — agree the DIN 276 **edition** in Architekten-/Ingenieurvertrag (see AKBW / CMS articles on 2018 vs 2008).
