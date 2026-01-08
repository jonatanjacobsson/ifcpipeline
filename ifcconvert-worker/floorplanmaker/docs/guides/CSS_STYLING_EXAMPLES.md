# CSS Styling Examples - Visual Reference

This document shows practical examples of the new CSS styling system with side-by-side comparisons.

---

## 1. Floor vs Ceiling MEP - Visual Comparison

### BEFORE (Old Approach)
```css
/* All MEP at same weight, no distinction */
.mechanical-layer path {
  stroke: #00CC66;
  stroke-width: 0.6px;
  fill: none;
}
```

**Result:** Floor and ceiling ducts look identical

### AFTER (New Approach)
```css
/* Floor level - solid, standard weight */
.mechanical-layer path {
  stroke: #00CC66;
  stroke-width: var(--w-duct);  /* 1.5px */
  fill: none;
}

/* Ceiling level - dashed, thinner */
.mechanical-ceiling-layer path {
  stroke: #00CC66;
  stroke-width: var(--w-ceiling-duct);  /* 1.2px */
  stroke-dasharray: 6 4;
  opacity: 0.85;
}
```

**Result:** Clear visual distinction between heights

**ASCII Representation:**
```
Floor Level:   ━━━━━━━━━━━━━━━  (solid, thick)
Ceiling Level: ╍╍╍╍╍╍╍╍╍╍╍╍╍╍╍  (dashed, thinner)
```

---

## 2. BH90 Line Weight Hierarchy

### Plumbing System Example

```css
:root {
  --stroke-base: 1.5px;
  --w-spillwater: calc(1.5px * 2.0);  /* = 3.0px */
  --w-pipe: calc(1.5px * 1.0);        /* = 1.5px */
  --w-fitting: calc(1.5px * 0.8);     /* = 1.2px */
}
```

**Visual Hierarchy:**
```
Spillwater:    ━━━━━━━━━━━━━━━  (thickest - most critical)
Regular pipe:  ━━━━━━━━━━━━━━━  (standard weight)
Fittings:      ●━━━━━━━━━━●     (thinner, filled markers)
```

**Example SVG:**
```xml
<g class="plumbing-layer">
  <path class="spillwater" d="M0,0 L100,0"/>     <!-- 3.0px thick -->
  <path class="IfcPipeSegment" d="M0,10 L100,10"/> <!-- 1.5px thick -->
  <circle class="IfcPipeFitting" cx="50" cy="10" r="2"/> <!-- 1.2px, filled -->
</g>
```

---

## 3. Status Visualization

### Existing vs New vs Demolition

```css
.is-existing {
  stroke: #666666;           /* Gray */
  stroke-width: 0.75px;      /* Thinner (0.5×) */
  opacity: 0.7;              /* Faded */
}

.is-new {
  /* Uses system color */
  opacity: 1.0;              /* Full prominence */
}

.is-demolition {
  stroke: #CC0000;           /* Red */
  stroke-dasharray: 2 2;     /* Short dashes */
  opacity: 0.6;              /* Faded */
}
```

**Visual Comparison:**
```
Existing:   ╌╌╌╌╌╌╌╌╌╌╌╌╌  (gray, thin, faded)
New:        ━━━━━━━━━━━━━  (blue, standard, bright)
Demolition: ╍╍╍╍╍╍╍╍╍╍╍╍╍  (red, dashed, faded)
```

---

## 4. Coordinated View Layer Stack

### Layer Order (Bottom to Top)

```yaml
layers:
  1. Architecture (underlay)      # opacity: 0.4, thin gray
  2. Structural                   # opacity: 0.7, blue
  3. Plumbing (floor)            # opacity: 0.85, cyan, solid
  4. Mechanical (floor)          # opacity: 0.85, green, solid
  5. Electrical (floor)          # opacity: 0.85, orange, solid
  6. Plumbing (ceiling)          # opacity: 0.7, cyan, dashed
  7. Mechanical (ceiling)        # opacity: 0.7, green, dashed
  8. Electrical (ceiling)        # opacity: 0.7, orange, dashed
  9. Spaces & Text               # opacity: 1.0, white text
```

**Visual Stack (Side View):**
```
┌─────────────────────┐
│  9. Text (top)      │ ← Most visible
├─────────────────────┤
│  8. Elec Ceiling ╍╍ │ ← Dashed
│  7. Mech Ceiling ╍╍ │
│  6. Plumb Ceiling╍╍ │
├─────────────────────┤
│  5. Elec Floor ━━━━ │ ← Solid, prominent
│  4. Mech Floor ━━━━ │
│  3. Plumb Floor ━━━ │
├─────────────────────┤
│  2. Structural ──── │ ← Context
│  1. Architecture ‥‥ │ ← Background
└─────────────────────┘
```

---

## 5. Real-World SVG Examples

### Example A: Simple Plumbing Floor Plan

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">
  <style>
    /* Include floorplan-styles.css content */
  </style>
  
  <defs>
    <marker id="arrow" markerWidth="6" markerHeight="6" refX="6" refY="3" orient="auto">
      <path d="M0,0 L0,6 L6,3 z" fill="currentColor"/>
    </marker>
  </defs>
  
  <!-- Architecture underlay -->
  <g class="architecture-layer underlay">
    <path d="M10,10 L790,10 L790,590 L10,590 Z" stroke="#CCC" fill="#EEE"/>
  </g>
  
  <!-- Plumbing floor level -->
  <g class="plumbing-layer">
    <!-- Spillwater (thick) -->
    <path class="spillwater" d="M50,300 L750,300" stroke="#0099CC" stroke-width="3"/>
    
    <!-- Regular pipes (standard) -->
    <path class="IfcPipeSegment" d="M50,350 L750,350" stroke="#0099CC" stroke-width="1.5"/>
    
    <!-- Fittings (filled) -->
    <circle class="IfcPipeFitting" cx="400" cy="350" r="3" fill="#0099CC" stroke="#0099CC"/>
    
    <!-- Valve (appliance) -->
    <rect class="IfcFlowController" x="395" y="295" width="10" height="10" 
          stroke="#0099CC" fill="none" stroke-width="1.5"/>
  </g>
  
  <!-- Space label -->
  <g class="spaces-layer">
    <text x="400" y="200" text-anchor="middle">
      <tspan x="400" dy="0">PLANT ROOM</tspan>
      <tspan x="400" dy="20">45.2 m²</tspan>
    </text>
  </g>
</svg>
```

### Example B: Ceiling Plan with Dashed MEP

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">
  <style>
    /* Include floorplan-styles.css content */
  </style>
  
  <!-- Architecture underlay (very light) -->
  <g class="architecture-layer underlay" opacity="0.2">
    <path d="M10,10 L790,10 L790,590 L10,590 Z" stroke="#DDD" fill="#F5F5F5"/>
  </g>
  
  <!-- Mechanical ceiling (dashed) -->
  <g class="mechanical-ceiling-layer">
    <!-- Main duct (dashed) -->
    <path class="IfcDuctSegment" 
          d="M100,300 L700,300" 
          stroke="#00CC66" 
          stroke-width="1.2" 
          stroke-dasharray="6 4"
          opacity="0.85"/>
    
    <!-- Branch ducts -->
    <path class="IfcDuctSegment" 
          d="M300,100 L300,500" 
          stroke="#00CC66" 
          stroke-width="1.2" 
          stroke-dasharray="6 4"
          opacity="0.85"/>
    
    <!-- Air terminals (supply diffusers) -->
    <circle class="IfcAirTerminal" cx="300" cy="200" r="5" 
            stroke="#00CC66" fill="none" stroke-width="1.2"/>
    <circle class="IfcAirTerminal" cx="300" cy="400" r="5" 
            stroke="#00CC66" fill="none" stroke-width="1.2"/>
  </g>
</svg>
```

---

## 6. Color Palette Reference

```css
:root {
  /* MEP System Colors */
  --col-electrical: #FF6600;   /* Orange */
  --col-mechanical: #00CC66;   /* Green */
  --col-plumbing: #0099CC;     /* Cyan/Blue */
  
  /* Architecture & Structure */
  --col-architecture: #222222; /* Dark gray */
  --col-structural: #0066CC;   /* Blue */
  
  /* Status Colors */
  --col-existing: #666666;     /* Medium gray */
  --col-demolition: #CC0000;   /* Red */
  --col-reference: #888888;    /* Light gray */
  
  /* Text */
  --col-text-fill: #FFFFFF;    /* White fill */
  --col-text-stroke: #000000;  /* Black outline */
}
```

**Color Swatches:**
```
Electrical:   ████ #FF6600 (Orange)
Mechanical:   ████ #00CC66 (Green)
Plumbing:     ████ #0099CC (Cyan)
Architecture: ████ #222222 (Dark Gray)
Structural:   ████ #0066CC (Blue)
Existing:     ████ #666666 (Medium Gray)
Demolition:   ████ #CC0000 (Red)
```

---

## 7. Line Width Scaling Examples

### Small Scale (1:100) - Thin Lines
```css
:root {
  --stroke-base: 1.0px;
}
```
**Result:** All lines scale down proportionally
- Spillwater: 2.0px
- Pipes: 1.0px
- Fittings: 0.8px

### Medium Scale (1:50) - Standard
```css
:root {
  --stroke-base: 1.5px;  /* Default */
}
```
**Result:** Balanced for most uses
- Spillwater: 3.0px
- Pipes: 1.5px
- Fittings: 1.2px

### Large Scale (1:20) - Thick Lines
```css
:root {
  --stroke-base: 2.5px;
}
```
**Result:** Clear for detailed views
- Spillwater: 5.0px
- Pipes: 2.5px
- Fittings: 2.0px

---

## 8. Text Styling Hierarchy

```css
/* Primary space name */
text tspan:first-child {
  font-size: 12pt;
  font-weight: 700;
  letter-spacing: 1.5px;
}

/* Secondary info (area) */
text tspan:nth-child(2) {
  font-size: 8pt;
  font-weight: 400;
  letter-spacing: 1px;
}

/* Tertiary info (function) */
text tspan:nth-child(3) {
  font-size: 6pt;
  font-weight: 400;
  letter-spacing: 0.8px;
}
```

**Example:**
```xml
<text x="100" y="100">
  <tspan x="100" dy="0">MECHANICAL ROOM</tspan>    <!-- Large, bold -->
  <tspan x="100" dy="16">Area: 28.5 m²</tspan>     <!-- Medium -->
  <tspan x="100" dy="12">Equipment Space</tspan>   <!-- Small -->
</text>
```

**Rendered:**
```
MECHANICAL ROOM
Area: 28.5 m²
Equipment Space
```

---

## 9. Advanced: Custom Element Classes

### Adding Custom Spillwater Detection

In your IFC export, add class attributes:

```python
# During SVG generation
if element.is_spillwater():
    element_class = "IfcPipeSegment spillwater"
else:
    element_class = "IfcPipeSegment"
```

**Result in SVG:**
```xml
<path class="IfcPipeSegment spillwater" d="..."/>  <!-- Gets 2× thickness -->
<path class="IfcPipeSegment" d="..."/>             <!-- Standard thickness -->
```

---

## 10. Interactive Elements (Future Enhancement)

```css
/* Hover effects for web viewers */
.clickable {
  cursor: pointer;
  transition: opacity 0.2s;
}

.clickable:hover {
  opacity: 0.8;
}

/* Selection highlight */
.is-selected {
  filter: drop-shadow(0 0 3px rgba(255, 255, 0, 1));
  stroke-width: 150% !important;
}
```

**Usage:**
```xml
<g class="plumbing-layer clickable" data-system-id="P-101" onclick="showSystemInfo('P-101')">
  <!-- Pipe system P-101 -->
</g>
```

---

## Quick Test Checklist

Use this to verify your CSS is working:

- [ ] Floor MEP shows solid lines
- [ ] Ceiling MEP shows dashed lines (6-4 pattern)
- [ ] Spillwater pipes are 2× thicker than regular pipes
- [ ] Architecture underlay is very faint (opacity 0.4)
- [ ] Text has white fill with black outline
- [ ] Ceiling elements are ~20% thinner than floor elements
- [ ] All colors match the system palette
- [ ] Existing elements are gray and thin
- [ ] Layer order: Architecture → Structure → Floor MEP → Ceiling MEP → Text

---

## Testing Commands

Generate a test floor plan to verify styles:

```bash
# Generate coordinated view with all layers
./generate-all-coordinated.sh

# Check a specific storey
./generate-coordinated-floorplan.sh "020 Mezzanine +5.40m" 5.40

# View the output
ls -lh /output/converted/floorplans/coord_all_*.svg
```

Then open in a browser or vector editor to verify:
1. Line weights are correct
2. Ceiling elements are dashed
3. Colors match specification
4. Text is readable

---

**Next Steps:**
- See [FLOORPLAN_CSS_GUIDE.md](./FLOORPLAN_CSS_GUIDE.md) for detailed documentation
- Edit [floorplan-styles.css](./floorplan-styles.css) to customize
- Update [floorplan-config.yaml](./floorplan-config.yaml) for model settings

