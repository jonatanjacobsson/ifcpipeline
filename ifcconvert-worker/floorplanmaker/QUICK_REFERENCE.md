# Floor Plan Maker - Quick Reference Card

## 📁 File Locations

```
floorplanmaker/
├── scripts/generation/    → 11 scripts - Generate floor plans
├── scripts/testing/       → 5 scripts  - Test & validate
├── scripts/processing/    → 9 scripts  - Post-process SVGs
├── scripts/utilities/     → 5 scripts  - Helper tools
├── config/templates/      → YAML/JSON configs
├── config/styles/         → CSS styling
├── docs/guides/           → User documentation
└── docs/implementation/   → Technical docs
```

## 🚀 Most Common Commands

### Generate Single Floor Plan
```bash
cd ifcconvert-worker/floorplanmaker

# Coordinated (all disciplines)
./scripts/generation/generate-coordinated-floorplan.sh "020 Mezzanine +5.40m" 5.40

# MEP only
./scripts/generation/generate-mep-floorplan.sh electrical "020 Mezzanine +5.40m" 5.40
```

### Generate All Levels (Batch)
```bash
# All coordinated plans
./scripts/generation/generate-all-coordinated.sh

# From config file
./scripts/generation/generate-all-levels-complete.sh
```

### Run Tests
```bash
# Comprehensive test
./scripts/testing/test-svg-focused.sh

# Check alignment
./scripts/testing/diagnose-alignment.sh
```

## 📝 Configuration Files

| File | Location | Purpose |
|------|----------|---------|
| Main config | `config/templates/floorplan-config.yaml` | Project settings, models, storeys |
| CSS styling | `config/styles/floorplan-styles.css` | Visual appearance, colors, labels |

## 📚 Key Documentation

| Topic | File |
|-------|------|
| Getting Started | `README.md` |
| CSS Styling | `docs/guides/FLOORPLAN_CSS_GUIDE.md` |
| Alignment Issues | `docs/implementation/ALIGNMENT_SOLUTION.md` |
| Complete Docs | `docs/guides/CSS_DOCUMENTATION_INDEX.md` |

## 🔧 Utility Commands

```bash
# List all storeys in a model
./scripts/utilities/list-storeys.sh

# Calculate bounds for alignment
./scripts/utilities/calculate-bounds.py model1.ifc model2.ifc 5.40 IfcWall IfcDoor

# Detect MEP storeys
./scripts/utilities/detect-mep-storeys.py
```

## 📂 Output Locations

- **Final floor plans:** `/output/converted/floorplans/`
- **Temporary files:** `/output/converted/temp/`
- **Generation logs:** `logs/`

## 🎨 File Naming Convention

```
{prefix}_{storey_slug}.svg

Examples:
coord_all_020_mezzanine_5_40m.svg     → Coordinated plan
FP_ELEC_040_first_floor_14_23m.svg    → Electrical plan
arch_030_slussen_level_8_90m.svg      → Architecture only
```

## 💡 Tips

1. **Always from floorplanmaker dir**: Scripts use relative paths
2. **Check logs first**: Generation logs are in `logs/` folder
3. **Test alignment**: Run `diagnose-alignment.sh` if layers don't align
4. **Use YAML config**: More readable than JSON
5. **Edit CSS for styling**: Don't modify SVG files directly

## ⚡ Quick Path Aliases (Optional)

Add to `~/.bashrc`:

```bash
# Floorplan aliases (adjust base path to your project location)
alias fp='cd ifcconvert-worker/floorplanmaker'
alias fpgen='cd ifcconvert-worker/floorplanmaker/scripts/generation'
alias fptest='cd ifcconvert-worker/floorplanmaker/scripts/testing'
```

Then use:
```bash
fp        # Jump to floorplanmaker
fpgen     # Jump to generation scripts
fptest    # Jump to testing scripts
```

## 🆘 Troubleshooting

| Problem | Solution |
|---------|----------|
| Misaligned layers | Run `scripts/testing/diagnose-alignment.sh` |
| Missing elements | Check section height and element types in config |
| Slow generation | Use `-j 8` flag for parallel processing |
| Can't find config | Use absolute path: `config/templates/floorplan-config.yaml` |

## 📞 Help Commands

```bash
# Script usage help
./scripts/generation/generate-coordinated-floorplan.sh --help

# View script content
less ./scripts/generation/generate-coordinated-floorplan.sh

# List all available scripts
ls scripts/generation/
ls scripts/testing/
ls scripts/processing/
ls scripts/utilities/
```

---

**Quick Reference v1.0** | For detailed docs see `README.md`

