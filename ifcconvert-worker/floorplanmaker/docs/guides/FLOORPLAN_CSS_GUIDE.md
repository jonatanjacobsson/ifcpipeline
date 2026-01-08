# Floor Plan CSS Styling Guide

This guide explains the improved CSS styling system for IFC floor plans, based on BH90 architectural standards.

## Overview

The new CSS template (`floorplan-styles.css`) provides:

1. **Variable-based styling** - Easy to adjust all line widths globally
2. **Distinct ceiling vs floor styling** - Ceiling MEP uses dashed lines and thinner strokes
3. **Layer-based organization** - Clear separation between disciplines
4. **BH90-compliant line weights** - Follows architectural standards for line hierarchy

---

## Key Features

### 1. CSS Variables (`:root`)

All styling is controlled through CSS variables for easy customization:

```css
:root {
  --stroke-base: 1.5px;           /* Base line width */
  
  /* System colors */
  --col-electrical: #FF6600;
  --col-mechanical: #00CC66;
  --col-plumbing: #0099CC;
  
  /* Relative line widths (BH90) */
  --w-spillwater: calc(var(--stroke-base) * 2.0);  /* Thickest */
  --w-pipe: calc(var(--stroke-base) * 1.0);        /* Standard */
  --w-duct: calc(var(--stroke-base) * 1.0);
  --w-appliance: calc(var(--stroke-base) * 1.0);
  
  /* Ceiling elements - thinner */
  --w-ceiling-pipe: calc(var(--stroke-base) * 0.8);
  --w-ceiling-duct: calc(var(--stroke-base) * 0.8);
}
```

**To adjust all line widths globally:** Change `--stroke-base` value (e.g., `2.0px` for thicker lines).

---

### 2. Layer Classes

Each discipline gets its own layer class:

- `.architecture-layer` - Buildings, walls, doors
- `.structural-layer` - Columns, beams, slabs
- `.electrical-layer` - Cables, lights, panels
- `.mechanical-layer` - Ducts, fans, terminals
- `.plumbing-layer` - Pipes, valves, tanks
- `.spaces-layer` - Room boundaries and labels

**Ceiling layers** use separate classes:
- `.electrical-ceiling-layer`
- `.mechanical-ceiling-layer`
- `.plumbing-ceiling-layer`

---

### 3. Ceiling vs Floor Styling

#### Floor Level MEP (Solid Lines)
```css
.plumbing-layer path {
  stroke: #0099CC;
  stroke-width: var(--w-pipe);
  fill: none;
  opacity: 1.0;
}
```

#### Ceiling Level MEP (Dashed Lines)
```css
.plumbing-ceiling-layer path {
  stroke: #0099CC;
  stroke-width: var(--w-ceiling-pipe);  /* Thinner */
  stroke-dasharray: 6 4;                /* Dashed */
  fill: none;
  opacity: 0.85;                        /* Slightly transparent */
}
```

**Visual Difference:**
- **Floor MEP:** Solid lines, normal weight
- **Ceiling MEP:** Dashed lines (6px dash, 4px gap), 20% thinner, slightly transparent

---

### 4. Element-Specific Styling

#### Spillwater Pipes (Thicker)
Per BH90 standards, spillwater (drainage) pipes are shown with 2× thickness:

```css
.plumbing-layer .spillwater,
.plumbing-layer .IfcPipeSegment.spill {
  stroke-width: var(--w-spillwater);  /* 2× base width */
}
```

#### Appliances & Equipment
```css
.mechanical-layer .IfcFan,
.mechanical-layer .IfcCoil,
.plumbing-layer .IfcPump {
  stroke-width: var(--w-appliance);
  fill: none;
}
```

#### Fittings (Filled)
```css
.plumbing-layer .IfcPipeFitting {
  stroke-width: var(--w-fitting);
  fill: currentColor;  /* Filled with layer color */
}
```

---

### 5. Status Modifiers

Show element status (existing, new, demolition):

```css
.is-existing {
  stroke: #666666;              /* Gray */
  stroke-width: var(--w-existing);  /* Thinner */
  opacity: 0.7;
}

.is-demolition {
  stroke: #CC0000;              /* Red */
  stroke-dasharray: 2 2;        /* Short dashes */
  opacity: 0.6;
}
```

**Usage in SVG:**
```xml
<g class="plumbing-layer is-existing">
  <!-- Existing plumbing elements -->
</g>
```

---

### 6. Height Level Classes

Alternative approach for elements at different heights:

```css
.above-ceiling {
  stroke-dasharray: 6 4;
  opacity: 0.85;
}

.at-floor {
  stroke-dasharray: none;
  opacity: 1;
}

.below-floor {
  stroke-dasharray: 2 3;
  opacity: 0.7;
}
```

---

## Configuration Integration

### In `floorplan-config.yaml`

The config defines ceiling models separately:

```yaml
models:
  # Floor-level mechanical
  mechanical:
    file: "/uploads/M1_2b_BIM_XXX_5700_00.v12.0.ifc"
    section_heights:
      default_offset: 2.8
      ceiling_level: 2.8
  
  # Ceiling-level mechanical (separate entry)
  mechanical_ceiling:
    file: "/uploads/M1_2b_BIM_XXX_5700_00.v12.0.ifc"
    section_heights:
      default_offset: "ceiling_mid_range"
    css:
      stroke: "#00CC66"
      stroke_width: "0.8px"
      fill: "none"
```

### In Coordinated Views

```yaml
view_templates:
  coordinated_all:
    layers:
      # Floor MEP
      - model: "mechanical"
        section_offset_type: "floor_level"
      
      # Ceiling MEP (dashed)
      - model: "mechanical_ceiling"
        section_offset_type: "ceiling_level"
        css_override:
          stroke_dasharray: "2,2"
```

---

## Usage Examples

### Example 1: Adjusting All Line Weights

To make all lines 50% thicker, edit `floorplan-styles.css`:

```css
:root {
  --stroke-base: 2.25px;  /* Was 1.5px */
}
```

All relative weights update automatically.

### Example 2: Changing MEP Color

```css
:root {
  --col-mechanical: #00AA55;  /* Darker green */
}
```

### Example 3: Custom Ceiling Dash Pattern

```css
.mechanical-ceiling-layer path {
  stroke-dasharray: 8 3;  /* Longer dashes, shorter gaps */
}
```

### Example 4: Highlighting Critical Systems

Add to your SVG:

```xml
<g class="plumbing-layer">
  <g class="spillwater is-highlight">
    <!-- Highlighted spillwater pipes -->
  </g>
</g>
```

The `.is-highlight` class adds a yellow glow:

```css
.is-highlight {
  filter: drop-shadow(0 0 2px rgba(255, 255, 0, 0.9));
}
```

---

## BH90 Line Weight Standards

The CSS follows BH90 (Swedish construction standard) principles:

| Element Type | Relative Weight | Reason |
|-------------|----------------|--------|
| Spillwater pipes | 2.0× | Most critical drainage |
| Regular pipes | 1.0× | Standard distribution |
| Ducts | 1.0× | Standard distribution |
| Cable trays | 1.0× | Standard distribution |
| Appliances | 1.0× | Equipment outlines |
| Fittings | 0.8× | Secondary connections |
| Ceiling elements | 0.8× | Less prominent (above view) |
| Existing | 0.5× | Background context |
| Reference lines | 0.5× | Annotations |

---

## Coordinated View Hierarchy

When all disciplines are shown together:

1. **Architecture (lightest)** - `opacity: 0.4`, very thin lines
2. **Structural** - `opacity: 0.7`, thin lines
3. **Floor MEP (solid)** - `opacity: 0.85`, standard weight
4. **Ceiling MEP (dashed)** - `opacity: 0.7`, thinner weight
5. **Spaces & Text (top)** - `opacity: 1.0`, full prominence

This creates clear visual hierarchy:
```
Spaces/Text (most visible)
  ↓
Floor MEP (prominent, solid)
  ↓
Ceiling MEP (secondary, dashed)
  ↓
Structural (context)
  ↓
Architecture (background)
```

---

## Advanced: Print Optimization

For high-quality PDF output:

```css
@media print {
  svg {
    shape-rendering: crispEdges;
  }
  
  .architecture-layer.underlay {
    opacity: 0.3 !important;  /* Even lighter for print */
  }
}
```

---

## Troubleshooting

### Lines Too Thick/Thin

Adjust base width in `:root`:
```css
--stroke-base: 1.2px;  /* Thinner */
--stroke-base: 2.0px;  /* Thicker */
```

### Ceiling Elements Not Dashed

Check if layer has correct class:
```xml
<g class="mechanical-ceiling-layer">  <!-- Not just "mechanical-layer" -->
```

### Colors Not Showing

Ensure CSS is loaded in SVG:
```xml
<svg>
  <style>
    /* CSS content from floorplan-styles.css */
  </style>
  <!-- Content -->
</svg>
```

### Text Not Readable

Increase stroke contrast:
```css
text {
  stroke-width: 1px;  /* Was 0.5px */
}
```

---

## Files

- **`floorplan-styles.css`** - Main CSS template
- **`floorplan-config.yaml`** - Model and view configuration
- **`generate-coordinated-floorplan.sh`** - Generation script (loads CSS)
- **`FLOORPLAN_CSS_GUIDE.md`** - This guide

---

## Quick Reference Card

```css
/* Global adjustments */
--stroke-base: 1.5px;            /* Scale all lines */
--col-electrical: #FF6600;       /* Change MEP colors */
--w-spillwater: calc(...* 2.0);  /* Relative weights */

/* Layer classes */
.electrical-layer                /* Floor level, solid */
.electrical-ceiling-layer        /* Ceiling level, dashed */

/* Status */
.is-existing                     /* Gray, thin */
.is-new                          /* Normal */
.is-demolition                   /* Red, dashed */

/* Height */
.above-ceiling                   /* Dashed, 85% opacity */
.at-floor                        /* Solid, full opacity */
.below-floor                     /* Short dash, 70% opacity */
```

---

**Need help?** Check the [FLOORPLAN_EXPORT.md](./FLOORPLAN_EXPORT.md) for generation instructions.

