# AssignLubekalkPset — ventilation models, Lubekalk-ready

Turns every element of a ventilation IFC into the rows Lubekalk (Elecosoft's
ventilation estimating program) imports: **Kod, Rev, Handling, P-del,
Beteckning, Dim 1, Dim 2, Montageläge, Kanalmaterial, Antal, Längd, Höjd**.
The values are written on the element as a `Lubekalk` property set, so they
travel with the model (StreamBIM, CDE, ifccsv) and the import sheet is just an
export of them.

## Why

Reza Ahmadi, *Total BIM in Swedish installation contracting; a comparative
study of model-based and traditional ventilation contracting* (Chalmers,
ACEX30, 2024), [hdl.handle.net/20.500.12380/309248](https://hdl.handle.net/20.500.12380/309248):

* model-based quantity take-off (StreamBIM filtered on BIP codes) was **40x
  faster** than take-off from drawings;
* the traditional take-off **underestimated cost by ~8 %** (77 899 SEK on one
  zone, ~294 000 SEK on the project);
* but the model quantities still went through Excel and were **typed into
  Lubekalk by hand** — "it was not possible to import the data into cost
  estimation program". His first future-research item: calculation programs
  should import data from IFC files.

This recipe is that missing step, done on the model side: the model carries
Lubekalk's own vocabulary, so nothing is re-keyed.

Supporting sources: SBUF 13833 *Upphandling och produktion via modell* (2021)
— BIP properties for VVS-kalkyl, the ventilation height bands, and the
"Excelark Mall för MF kalkyl Ventilation"; IN *Virtuella installationer 2014,
Leveransspecifikation Ventilation* — Lubekalk as the installer's calculation
system, and "a mapping between the objects' designations and the calculation
system's designations is normally needed".

## What the model graph supplies

A MagiCAD-for-Revit export puts `BIP.Diameter` and length on duct segments
only. Everything else is derived, and each element records how:

| Value | From | Notes |
|---|---|---|
| Fitting size | the port graph | propagated through `IfcRelConnectsPorts` and across same-size fittings (bends, joints, caps, a tee's through-run) |
| Product size | its designation (`FTCU-400`, `BSKC6-200`) | only for ports the ducts left unsized |
| Remaining ends | the fitting's own mesh ring at the port | snapped to the project's duct series; MagiCAD spigots are 0.85–0.92x nominal. Validated on 397 ports of known size: 366 exact, 13 refused, 0 wrong |
| Bend angle | the two ports' directions | exported axes point in *and* out; each is oriented away from the fitting first |
| Rectangular section | `IfcRectangleProfileDef` of the extrusion | the axis equal to the length is dropped (Revit sometimes extrudes along the width) |
| Montageläge | height of the element's centre above the floor below; the space name first (fan room, shaft, roof) | bands per Normtid VVS / SBUF 13833: <4 m, <6 m, <8 m, >8 m |
| Kanalmaterial | type description | varmförzinkad → Galvplåt, rostfri → Rostfritt … |
| Insulation / hangers | `BIP.InsulationType` on the host | fire class chooses the RI code (and CU/RU hanger code when `include_hangers`); insulation parts are `Ingår` |

## Status per element

| `Lubekalk.Status` | Meaning |
|---|---|
| `Klar` | complete Lubekalk row(s) |
| `Ingår` | priced through another element (insulation parts → the host's rows) |
| `Utanför` | not ventilation calculation scope (ceilings, walls, furniture in the model) — `Orsak` says which |
| `Saknas` | in scope but unresolved — `Orsak` says what is missing |

`Källa` records the evidence (reference, angle, which size tier), `Säkerhet`
(hög / medel / låg) how firm the code is. Review medel/låg, not everything.

## Validation model: Nobel M1-570 (MagiCAD for Revit, IFC2X3)

| Version | Elements | Klar | Ingår | Utanför | Saknas | Import rows | CK in CSV / `BIP.Length` |
|---|---|---|---|---|---|---|---|
| v43 (2026-09-23) | 11 665 | 8 079 | 3 533 | 53 | 0 | 1 791 | 5 016.82 / 5 016.90 m |
| v46 (2026-09-26) | 11 755 | 8 052 | 3 546 | 157 | 0 | 1 836 | 5 051.25 / 5 051.34 m |
| M1-570-SM-Voids.ifc | 50 | – | – | 50 (håltagning not priced) | 0 | – | – |

Default arguments, in the ifcpatch worker: ~35 s for the complete model. Every
Revit export moves something between classes, and the recipe follows the
element, not the class:

* v42 → v43: duct insulation went from `IfcBuildingElementPart` to
  `IfcCovering`; both are `Ingår`.
* v43 → v46: BRNF roof hoods went from terminals to one-port, unconnected
  `IfcFlowFitting`s; they stay `SAK` by their designation. 119 products became
  `IfcElementAssembly` without parts and are now `Utanför` ("Ingen
  ventilationskalkylpost för klassen"): 100 Valk Pro+ solar mounts, 11 VG01
  heating manifolds, 8 Prema shunt units (SRBX/SRUX/VÅU, `SAK` in v43). Whether
  the shunt units belong in the ventilation calculation is the kalkylator's call.

## Lubekalk's import, as it behaved on this file (2026-09-23)

Imported through *Importera från Bluebeam* with **Kanallängd exkl detaljer**
(right for this file: CK metres are straight duct only, fittings are their own
rows).

* `Dim 1` / `Dim 2` are integers: `177,8` fails the row ("is not a valid
  integer value"). The recipe writes whole millimetres, half rounded up.
* `Beteckning`: 15 characters. An import row took 30 (33+ failed with "The
  field is too small"), but a SAK designation is also saved into the article
  register, which holds 15 (`BDEP-46-040-230` saved). The export cuts longer
  names (Revit type names such as `SAL_35 inverted 2 slot_shallow_JR_1500 dummy
  box opposite` → `SAL_35 inver~V7`) and adds a tag computed from the full
  name, so a product keeps the same short name in every export and does not
  become a new register article per revision. *Förkortade beteckningar* lists
  each next to its full name; the element keeps the full name. Reductions
  carry only the small end (`→1050x800`); Dim 1 × Dim 2 is the big end.
* Codes are checked against the installation's register, not the template:
  `CU1` was *Okänd kod* although the template lists it. `--omit-codes` moves such
  rows to *Utelämnade koder* with their metres and counts.
* New SAK designations are saved into the register on import ("Sparat SAK …").
* `--lubekalk-csv` (and the recipe's `lubekalk_import` artifact) is the file the
  import reads: semicolons, decimal commas, Windows-1252, CRLF (`→` is spelled `>`).
* With those fixes the import went through: 1 791 rows read, **804 mängder and
  921 detaljer saved**, 169 rows failed (log lines not yet seen).
* Lubekalk hangs every detail under a duct row (mängd). A detail whose
  `Beteckning` differs from its duct's gets a new, empty duct row (Mängd 0):
  the 23 RÖC transitions showed up as `RK 600×500 "Ø500"`. The recipe puts notes
  there (round end, small end, insulation type), which is the likely reason for
  804 mängder against 374 duct rows. `keep_beteckning_only=SAK` blanks them —
  under test; it becomes the default if the empty duct rows disappear.

## Run

**As a tool (n8n IfcPatch node / API).** Recipe `AssignLubekalkPset`, custom
(`use_custom: true`); it is listed by `POST /patch/recipes/list` with its
parameters. Arguments in order, all optional:

| # | Argument | Default | |
|---|---|---|---|
| 1 | `mapping_module` | `lubekalk_magicad` | module in `mappings/` |
| 2 | `overwrite` | `true` | replace an existing `Lubekalk` pset |
| 3 | `dry_run` | `false` | resolve and count only |
| 4 | `handling_source` | `BIP.StoreyName` | Handling column |
| 5 | `pdel_source` | `BIP.SystemName` | P-del column |
| 6 | `include_hangers` | `false` | CU/RU per metre |
| 7 | `include_voids` | `false` | CH/RH håltagning |
| 8 | `export_csv` | `true` | return the import CSV + report |
| 9 | `beteckning_max` | `15` | longest Beteckning |
| 10 | `keep_beteckning_only` | *(empty: all)* | e.g. `SAK` |

The output is the patched IFC. With S3 keys, the worker uploads the artifacts
next to it — `<output>_lubekalk_import.csv` (import it as is) and
`<output>_lubekalk_report.json` (readiness, rows to review, insulation without a
code, shortened designations) — and returns their keys as
`lubekalk_import_key` / `lubekalk_report_key`, with the readiness counts in the
job result's summary.

Upphängning and håltagning are opt-in: the kalkylator prices neither in
Lubekalk. With the defaults, ducts carry no CU/RU rows and provisions for voids
are `Utanför` ("Håltagning tas inte med i kalkylen"); pass `true` to get them.

**A project that differs** (room names, product families, duct series): add
`mappings/<project>_lubekalk.py` (gitignored), start it with
`from mappings.lubekalk_magicad import *`, override what differs, and pass its
name as `mapping_module`.

**From the command line**, over one or more patched models (e.g. the
complete model and its voids), with an .xlsx of review sheets:

```bash
python scripts/export_lubekalk_import.py patched.ifc [voids.ifc] --out lubekalk_import.xlsx \
    --lubekalk-csv lubekalk_import.csv [--omit-codes CODE,…]
```

Sheets: **Lubekalk import** (paste/import as is), **Isolering att koda**
(insulation without a code yet, with lengths), **Granska** (medel/låg and any
Saknas), **Förkortade beteckningar**, **Utelämnade koder** (with
`--omit-codes`), **Status**. Without openpyxl it writes semicolon CSVs with decimal
commas.

## For the kalkylator: assumptions to confirm (`ANTAGANDE` in the mapping)

* Insulation fire classes: B30/B40/B50/B80/K80 → A30, B100 → A60 (hanger CU3/CU4,
  RU3/RU4; rectangular RI7/RI8). The project's TB decides the real class.
* Thermal insulation (V20/V30/V50) and circular-duct insulation have **no code**
  in the import template's lists; they are reported, with lengths, not guessed.
* Joints `magicirc_joint_001` → CIS (Iskjut), `magirect_joint1_001` → RPB (Passbit).
* Cleaning covers EPFH → CR2 (A15) unless on fire-insulated duct → CR1 (A60).
* Rectangular tee LTTR → RA1; LORU → RÖC; TVU45 (3 ports) → CT.
* `Montageläge` strings must match Lubekalk's list character for character,
  padding included — they are copied from the template.
* With `include_voids`: provisions for voids → RH/CH (Håltagning), sized from
  `Pset_ProvisionForVoid` Width × Height (Depth is the wall). On Nobel 15 of 50
  have a side under 20 mm — the provision is the sliver where a duct grazes a
  wall, not a penetration.
* `Höjd` is exported as 0; the computed centre height is on the element as
  `Lubekalk.Montagehöjd` for review.

## Montageläge — adversarial review (2026-09-22, not yet fixed)

**Why it matters.** Plåt & Ventföretagen's *Riktlinjer för Mängdning*
(2025-01-31) prices montageläge as labour surcharges: kryputrymme (≤ 1.8 m)
+25 %, höjdmontage 4–8 m +10 %, > 8 m +20 %, schakt +20 %, fläktrum +15 %
(plus +20 % for plastic/stainless/painted duct, +5–15 % for hangers on fire
insulation). SBUF 13833 asks for installation rooms (fläktrum, apparatrum,
schakt, kulvert) to be identified "då kostnaden … behöver hanteras annorlunda",
for room data to come from the architect's model with room volumes that let
ceiling-void components inherit the room, and for nodes at walls/slabs so long
runs get the right room.

**Three definitions of height, and the recipe picks one silently.** SBUF:
centreline (implemented). Normtid VVS: underside of the duct. PVF: *the
fastening point* ("avser infästningsstället"). On Nobel M1: underside moves
145 elements (1.8 %) down to Normalmontage; fastening point (next slab soffit)
moves 1 206 (15 %) up to Höjdmontage. Which one Lubekalk's `th. <4.0 m` bands
assume is not documented here — ask the kalkylator/Elecosoft.

**Room labels are not physical rooms.** Tested against the architect's 241
room volumes (the rooms CDE injects, from `A1_2b_BIM_XXX_0003_00.ifc` v1,
2026-06-09 — three months older than M1 v42):

* 4 153 of 7 977 positioned elements (52 %) are inside **no** room: rooms are
  modelled to the suspended ceiling, and the ducts sit 0.6–0.8 m above it. SBUF's
  room-volume requirement is not met.
* Where an element is inside a room, `BIP.SpaceLongName` names that room 55.8 %
  of the time. For ceiling-void elements it names the room *below* only 10.2 %
  of the time — it mostly names a room on the plan above.
* Category errors from labels, inside rooms: 195 in shaft rooms not labelled
  (missed Schakt +20 %), 160 in fan rooms not labelled (missed Fläktrum +15 %),
  31 labelled fan room but elsewhere. Four fans in `MEP riser 2.1` are labelled
  "Activation space"; three fans stand in `MEP 3.2`, which is not treated as a
  fan room.
* 1 885 elements are in double-height rooms (AHU plant room, MEP 1, Delivery
  bay, MEP 3.x) whose floor is a storey below the level the recipe measures from.
* 50 vertical segments spanning > 3 m (232 m) are banded by their centre height.

**Planned fixes** (need the architect's room model as a second input):
room by physical containment, with ceiling-void elements assigned to the room
below; height above that room's floor; a declared `height_reference`
(centre | underside | fastening) with all three heights recorded on the
element; fan rooms also by function (a room containing fans/AHUs); vertical
runs in shaft rooms → Schakt, others flagged; kryputrymme from room free height
< 1.8 m (SBUF), not from names.
