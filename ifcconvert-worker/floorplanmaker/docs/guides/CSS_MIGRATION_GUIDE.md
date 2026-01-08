# CSS Migration Guide - Old to New System

This guide helps you transition from the old inline CSS to the new BH90-inspired styling system.

---

## What's Changed?

### Old System
- ❌ Hardcoded pixel values
- ❌ No distinction between floor/ceiling MEP
- ❌ Inconsistent line weights
- ❌ Inline CSS in Python script
- ❌ Difficult to adjust globally

### New System
- ✅ CSS variables for easy scaling
- ✅ Clear floor/ceiling distinction (solid vs dashed)
- ✅ BH90-compliant relative line weights
- ✅ External CSS file (`floorplan-styles.css`)
- ✅ One-line adjustments affect entire system

---

## Quick Migration Checklist

### ✅ Step 1: Add CSS File

The new CSS file has been created:
```
config/styles/floorplan-styles.css
```

**Action:** None needed, file is ready to use.

---

### ✅ Step 2: Update Generation Script

The `generate-coordinated-floorplan.sh` script now loads CSS from the external file:

```bash
# Line ~310-316 in generate-coordinated-floorplan.sh
css_file = 'config/styles/floorplan-styles.css'
try:
    with open(css_file, 'r') as f:
        coord_css = f.read()
except FileNotFoundError:
    # Falls back to minimal CSS
```

**Action:** Already updated! Test with:
```bash
./generate-coordinated-floorplan.sh "020 Mezzanine +5.40m" 5.40
```

---

### ✅ Step 3: Update Config for Ceiling Models

Your `floorplan-config.yaml` already has ceiling model definitions:

```yaml
models:
  electrical_ceiling:    # Lines 212-244
  mechanical_ceiling:    # Lines 246-275
  plumbing_ceiling:      # Lines 277-300
```

**Action:** Verify ceiling models use correct CSS classes:

**BEFORE (in config):**
```yaml
mechanical_ceiling:
  css:
    stroke: "#00CC66"
    stroke_width: "0.8px"
```

**AFTER (now handled by CSS file):**
```yaml
mechanical_ceiling:
  # CSS is now in floorplan-styles.css
  # Layer class: mechanical-ceiling-layer
```

---

### 📋 Step 4: Verify Layer Class Assignment

Ensure the Python merging script assigns correct classes:

**Current code (generate-coordinated-floorplan.sh, line ~238):**
```python
def apply_layer_style(element, layer_name, opacity, stroke_color, stroke_width='0.5px'):
    for group in element.findall('svg:g', ns):
        group.set('class', f'{layer_name}-layer')
        group.set('opacity', str(opacity))
```

**For ceiling models, update to:**
```python
def apply_layer_style(element, layer_name, opacity, stroke_color, stroke_width='0.5px', is_ceiling=False):
    for group in element.findall('svg:g', ns):
        if is_ceiling:
            group.set('class', f'{layer_name}-ceiling-layer')
        else:
            group.set('class', f'{layer_name}-layer')
        group.set('opacity', str(opacity))
```

---

### 📋 Step 5: Add Ceiling Layer Exports

The script currently exports 6 layers. To fully use ceiling styling, add 3 more ceiling layers:

**Add to generate-coordinated-floorplan.sh after line 140:**

```bash
# NEW: Ceiling-level exports with higher section heights

# Calculate ceiling heights
PLUMB_CEIL_HEIGHT=$(python3 $PARSER --section-height plumbing_ceiling $STOREY_ELEVATION ceiling_level)
MECH_CEIL_HEIGHT=$(python3 $PARSER --section-height mechanical_ceiling $STOREY_ELEVATION ceiling_level)
ELEC_CEIL_HEIGHT=$(python3 $PARSER --section-height electrical_ceiling $STOREY_ELEVATION ceiling_level)

# Temp files for ceiling layers
PLUMB_CEIL_SVG="${TEMP_DIR}/coord_plumb_ceil_${STOREY_SAFE}.svg"
MECH_CEIL_SVG="${TEMP_DIR}/coord_mech_ceil_${STOREY_SAFE}.svg"
ELEC_CEIL_SVG="${TEMP_DIR}/coord_elec_ceil_${STOREY_SAFE}.svg"

# Layer 7: Plumbing Ceiling
echo "  [7/9] Exporting plumbing ceiling..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --section-height "$PLUMB_CEIL_HEIGHT" \
  --include entities IfcPipeSegment IfcPipeFitting IfcFlowSegment IfcFlowFitting IfcFlowTerminal \
  --no-progress \
  --svg-no-css \
  /uploads/P1_2b_BIM_XXX_5000_00.v12.0.ifc \
  "$PLUMB_CEIL_SVG" 2>&1 | grep -E "(Done|Creating)" || true

# Layer 8: Mechanical Ceiling
echo "  [8/9] Exporting mechanical ceiling..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --section-height "$MECH_CEIL_HEIGHT" \
  --include entities IfcDuctSegment IfcDuctFitting IfcFlowSegment IfcFlowFitting IfcAirTerminal \
  --no-progress \
  --svg-no-css \
  /uploads/M1_2b_BIM_XXX_5700_00.v12.0.ifc \
  "$MECH_CEIL_SVG" 2>&1 | grep -E "(Done|Creating)" || true

# Layer 9: Electrical Ceiling
echo "  [9/9] Exporting electrical ceiling..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --section-height "$ELEC_CEIL_HEIGHT" \
  --include entities IfcCableCarrierSegment IfcLightFixture IfcFlowSegment \
  --no-progress \
  --svg-no-css \
  /uploads/E1_2b_BIM_XXX_600_00.v183.0.ifc \
  "$ELEC_CEIL_SVG" 2>&1 | grep -E "(Done|Creating)" || true
```

Then update the Python merging section to include these 3 additional layers.

---

## Mapping: Old CSS → New CSS

### Architecture Layer

**OLD:**
```css
.architecture-layer path {
    stroke: #CCCCCC !important;
    stroke-width: 0.3px;
    fill: #EEEEEE;
    opacity: 0.4;
}
```

**NEW:**
```css
.architecture-layer.underlay path {
  stroke: #CCCCCC;
  stroke-width: var(--w-wall-underlay);  /* 0.45px with base 1.5 */
  fill: #EEEEEE;
  opacity: 0.4;
}
```

**Benefits:** Adjusts proportionally with `--stroke-base`

---

### Plumbing Layer

**OLD:**
```css
.plumbing-layer path {
    stroke: #0099CC !important;
    stroke-width: 0.6px;
    fill: none;
    opacity: 0.85;
}
```

**NEW:**
```css
/* Floor level */
.plumbing-layer path {
  stroke: var(--col-plumbing);  /* #0099CC */
  stroke-width: var(--w-pipe);  /* 1.5px */
  fill: none;
}

/* Ceiling level */
.plumbing-ceiling-layer path {
  stroke: var(--col-plumbing);
  stroke-width: var(--w-ceiling-pipe);  /* 1.2px */
  stroke-dasharray: 6 4;                /* NEW: Dashed! */
  fill: none;
  opacity: 0.85;
}
```

**Benefits:** 
- Distinct floor vs ceiling styling
- BH90-compliant relative weights
- Spillwater pipes automatically 2× thicker

---

### Text Styling

**OLD:**
```css
text {
    font-family: Arial, Helvetica, sans-serif;
    font-size: 16pt;
    font-weight: bold;
    fill: white !important;
    stroke: black;
    stroke-width: 0.5px;
    paint-order: stroke fill;
    letter-spacing: 1px;
}

text tspan:first-child {
    font-size: 12pt;
    font-weight: bold;
    letter-spacing: 1.5px;
}
```

**NEW:**
```css
text {
  font-family: Arial, Helvetica, sans-serif;
  font-size: 16pt;
  font-weight: 700;
  fill: var(--col-text-fill);     /* White */
  stroke: var(--col-text-stroke); /* Black */
  stroke-width: 0.5px;
  paint-order: stroke fill;
  letter-spacing: 1px;
  text-transform: uppercase;      /* NEW: Auto uppercase */
}

text tspan:first-child {
  font-size: 12pt;
  font-weight: 700;
  letter-spacing: 1.5px;
}
```

**Benefits:** 
- Uses CSS variables for colors
- Auto-uppercase via CSS
- More semantic hierarchy (nth-child selectors)

---

## Testing the Migration

### Test 1: Generate Single Floor Plan

```bash
./generate-coordinated-floorplan.sh "020 Mezzanine +5.40m" 5.40
```

**Expected output:**
```
✓ Loaded CSS from config/styles/floorplan-styles.css
```

**Verify:**
- CSS file loaded successfully
- No CSS errors in output
- SVG file created

---

### Test 2: Visual Inspection

Open the generated SVG in a browser or Inkscape:

```bash
# Linux
xdg-open ../../shared/output/converted/floorplans/coord_all_020_mezzanine_5_40m.svg

# Or copy to local machine and open
```

**Check:**
- ✅ Architecture is light gray background
- ✅ MEP systems use correct colors (orange/green/cyan)
- ✅ If ceiling layers present, they should be dashed
- ✅ Text is white with black outline
- ✅ Line weights look proportional

---

### Test 3: Batch Generation

```bash
./generate-all-coordinated.sh
```

**Expected:** All 8 storeys generated with new styling

---

### Test 4: Adjust Line Weights

Edit `floorplan-styles.css`:

```css
:root {
  --stroke-base: 2.0px;  /* Increase from 1.5px */
}
```

Regenerate:
```bash
./generate-coordinated-floorplan.sh "020 Mezzanine +5.40m" 5.40
```

**Verify:** All lines are proportionally thicker (33% increase)

---

## Common Issues & Solutions

### Issue 1: CSS Not Applied

**Symptom:** SVG looks like old styling

**Solution:**
```bash
# Check if CSS file exists
ls -l config/styles/floorplan-styles.css

# Check script is loading it
grep "floorplan-styles.css" generate-coordinated-floorplan.sh
```

**Fix:** Ensure the CSS file path in the script is correct.

---

### Issue 2: Ceiling Layers Not Dashed

**Symptom:** Ceiling MEP looks solid like floor MEP

**Solution:** Check layer class assignment in SVG:

```bash
# Extract layer classes from generated SVG
grep 'class=' /output/converted/floorplans/coord_all_020_mezzanine_5_40m.svg | head -20
```

**Expected:**
```xml
<g class="architecture-layer" ...>
<g class="plumbing-layer" ...>
<g class="plumbing-ceiling-layer" ...>  <!-- Should be "-ceiling-layer" -->
```

**Fix:** Update `apply_layer_style()` function to add `-ceiling` suffix for ceiling models.

---

### Issue 3: Lines Too Thin/Thick

**Symptom:** All lines are too thin or too thick

**Solution:**
```css
/* In floorplan-styles.css */
:root {
  --stroke-base: 1.5px;  /* Default */
  
  /* Adjust this value:
     1.0px = thinner
     2.0px = thicker
     2.5px = very thick
  */
}
```

---

### Issue 4: Colors Look Different

**Symptom:** MEP colors don't match old system

**Solution:** Check color definitions match:

**Old colors (from config):**
```yaml
electrical:
  css:
    stroke: "#FF6600"  # Orange
mechanical:
  css:
    stroke: "#00CC66"  # Green
plumbing:
  css:
    stroke: "#0099CC"  # Cyan
```

**New colors (in CSS):**
```css
:root {
  --col-electrical: #FF6600;  /* Should match */
  --col-mechanical: #00CC66;  /* Should match */
  --col-plumbing: #0099CC;    /* Should match */
}
```

**Fix:** Adjust CSS variables to match your preferred colors.

---

## Rollback Plan

If you need to revert to the old system:

### 1. Keep Backup of Old Script

```bash
cp generate-coordinated-floorplan.sh generate-coordinated-floorplan.sh.new
# Restore from git or backup
```

### 2. Minimal CSS Fallback

The script already has fallback CSS if `floorplan-styles.css` is not found (lines 318-374).

### 3. Temporarily Disable External CSS

Comment out CSS file loading:

```python
# try:
#     with open(css_file, 'r') as f:
#         coord_css = f.read()
# except FileNotFoundError:
coord_css = """
    /* Old inline CSS */
    .plumbing-layer path {
        stroke: #0099CC !important;
        stroke-width: 0.6px;
    }
"""
```

---

## Next Steps After Migration

### 1. Customize Colors for Your Project

```css
:root {
  --col-electrical: #FF8800;  /* Adjust to project colors */
  --col-mechanical: #00AA55;
  --col-plumbing: #0088BB;
}
```

### 2. Add Custom Element Classes

Tag special elements in your models:
- Fire protection systems: `.fire-protection`
- Critical equipment: `.critical`
- Temporary systems: `.temporary`

### 3. Create View-Specific CSS

Add specialized CSS for different view types:
```css
/* Electrical-only view */
.electrical-view .plumbing-layer,
.electrical-view .mechanical-layer {
  opacity: 0.2;  /* Dim other systems */
}
```

### 4. Print Optimization

Test print output and adjust:
```css
@media print {
  :root {
    --stroke-base: 1.8px;  /* Slightly thicker for print */
  }
}
```

---

## Documentation References

- **[FLOORPLAN_CSS_GUIDE.md](./FLOORPLAN_CSS_GUIDE.md)** - Complete CSS documentation
- **[CSS_STYLING_EXAMPLES.md](./CSS_STYLING_EXAMPLES.md)** - Visual examples
- **[FLOORPLAN_EXPORT.md](./FLOORPLAN_EXPORT.md)** - Generation workflow
- **[floorplan-styles.css](./floorplan-styles.css)** - Main stylesheet

---

## Support

If you encounter issues:

1. Check the CSS file is loaded (look for "Loaded CSS from..." in output)
2. Verify layer classes in SVG (should be `*-layer` or `*-ceiling-layer`)
3. Test with simple single-layer export first
4. Compare generated SVG with examples in `CSS_STYLING_EXAMPLES.md`

**Migration complete!** Your floor plans now use the improved BH90-inspired styling system. 🎉

