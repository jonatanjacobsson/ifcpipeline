# 📚 CSS Styling System - Documentation Index

**Quick navigation to all CSS documentation files**

---

## 🚀 Start Here

### **[CSS_UPGRADE_README.md](./CSS_UPGRADE_README.md)**
**Your starting point** - Overview, quick start, and key benefits

- What's new
- Quick start guide
- Visual summary
- Testing checklist
- 5-minute overview

**Read this first!**

---

## 📖 Main Documentation

### 1. **[FLOORPLAN_CSS_GUIDE.md](./FLOORPLAN_CSS_GUIDE.md)**
**Complete technical reference**

What's inside:
- ✅ CSS variables and their purpose
- ✅ Layer classes (architecture, MEP, spaces)
- ✅ Floor vs ceiling styling differences
- ✅ Element-specific styling (pipes, ducts, cables)
- ✅ BH90 line weight standards
- ✅ Configuration integration
- ✅ Troubleshooting guide

**Use when:** You need detailed documentation or technical reference

---

### 2. **[CSS_STYLING_EXAMPLES.md](./CSS_STYLING_EXAMPLES.md)**
**Visual examples and comparisons**

What's inside:
- 🎨 Before/after visual comparisons
- 🎨 ASCII art representations
- 🎨 Real SVG code examples
- 🎨 Color palette reference
- 🎨 Line weight scaling examples
- 🎨 Text hierarchy examples
- 🎨 Testing checklist

**Use when:** You want to see visual examples or understand how styling looks

---

### 3. **[CSS_MIGRATION_GUIDE.md](./CSS_MIGRATION_GUIDE.md)**
**Migration steps and troubleshooting**

What's inside:
- 🔄 What changed (old vs new)
- 🔄 Step-by-step migration checklist
- 🔄 Old CSS → New CSS mapping
- 🔄 Common issues & solutions
- 🔄 Rollback plan
- 🔄 Testing procedures

**Use when:** You're migrating from old system or troubleshooting issues

---

## 📄 Core Files

### **[floorplan-styles.css](./floorplan-styles.css)**
**The actual stylesheet** - Edit this to customize styling

Key sections:
1. CSS Variables (`:root`) - Lines 1-40
2. Layer Base Styles - Lines 50+
3. Floor MEP Styles - Lines 100+
4. Ceiling MEP Styles - Lines 250+
5. Status Modifiers - Lines 400+
6. Text & Spaces - Lines 450+

---

## 📋 Related Configuration

### **[floorplan-config.yaml](./floorplan-config.yaml)**
Model and view template definitions

Relevant sections:
- `models:` (Line 20) - Model definitions including ceiling models
- `models.electrical_ceiling:` (Line 212) - Ceiling model example
- `view_templates:` (Line 333) - View configurations
- `view_templates.coordinated_all:` (Line 551) - 9-layer coordinated view

---

## 🛠️ Generation Scripts

### **[generate-coordinated-floorplan.sh](./generate-coordinated-floorplan.sh)**
Main generation script (now loads external CSS)

Key changes:
- Line 310-316: Loads `floorplan-styles.css`
- Line 78-159: Layer export (supports 9 layers)
- Line 233-246: Layer styling application

### **[generate-all-coordinated.sh](./generate-all-coordinated.sh)**
Batch generation for all storeys

---

## 🎯 Quick Reference

### By Task

| Task | Document |
|------|----------|
| **Getting started** | `CSS_UPGRADE_README.md` |
| **Understanding concepts** | `FLOORPLAN_CSS_GUIDE.md` |
| **See examples** | `CSS_STYLING_EXAMPLES.md` |
| **Troubleshooting** | `CSS_MIGRATION_GUIDE.md` |
| **Customize styling** | `floorplan-styles.css` |
| **Configure models** | `floorplan-config.yaml` |

### By Question

| Question | Answer In |
|----------|-----------|
| How do I change line weights? | `FLOORPLAN_CSS_GUIDE.md` § Variables |
| What's the difference between floor and ceiling MEP? | `CSS_STYLING_EXAMPLES.md` § Example 1 |
| How do I customize colors? | `FLOORPLAN_CSS_GUIDE.md` § Variables |
| Why aren't my ceiling elements dashed? | `CSS_MIGRATION_GUIDE.md` § Issue 2 |
| What are BH90 standards? | `FLOORPLAN_CSS_GUIDE.md` § BH90 Standards |
| How do I test the new system? | `CSS_UPGRADE_README.md` § Quick Start |
| What CSS classes are available? | `FLOORPLAN_CSS_GUIDE.md` § Layer Classes |
| How do I show existing vs new elements? | `FLOORPLAN_CSS_GUIDE.md` § Status Modifiers |

---

## 📊 Documentation Tree

```
CSS Documentation
│
├── 🚀 CSS_UPGRADE_README.md
│   └── Start here! Overview & quick start
│
├── 📖 FLOORPLAN_CSS_GUIDE.md
│   ├── CSS Variables Reference
│   ├── Layer Classes
│   ├── Floor vs Ceiling Styling
│   ├── Element-Specific Rules
│   ├── BH90 Standards
│   └── Troubleshooting
│
├── 🎨 CSS_STYLING_EXAMPLES.md
│   ├── Visual Comparisons (Before/After)
│   ├── Real SVG Examples
│   ├── Color Palettes
│   ├── Line Weight Demonstrations
│   └── Testing Checklist
│
├── 🔄 CSS_MIGRATION_GUIDE.md
│   ├── What Changed
│   ├── Migration Steps
│   ├── Old → New Mapping
│   ├── Common Issues
│   └── Rollback Plan
│
└── 📄 Core Files
    ├── floorplan-styles.css (stylesheet)
    ├── floorplan-config.yaml (configuration)
    ├── generate-coordinated-floorplan.sh (generator)
    └── generate-all-coordinated.sh (batch generator)
```

---

## 🎓 Learning Path

### Beginner (0-15 minutes)
1. Read `CSS_UPGRADE_README.md` (5 min)
2. Run test generation command (2 min)
3. View generated SVG (3 min)
4. Browse `CSS_STYLING_EXAMPLES.md` visuals (5 min)

### Intermediate (15-45 minutes)
1. Read `FLOORPLAN_CSS_GUIDE.md` (20 min)
2. Edit `floorplan-styles.css` variables (5 min)
3. Regenerate and compare (5 min)
4. Explore layer classes (15 min)

### Advanced (45+ minutes)
1. Deep dive into `CSS_MIGRATION_GUIDE.md` (20 min)
2. Customize element-specific styling (20 min)
3. Add custom status classes (20 min)
4. Integrate with project workflow (time varies)

---

## 📞 Need Help?

### Quick Fixes
→ See `CSS_MIGRATION_GUIDE.md` § Common Issues

### Understanding Concepts
→ See `FLOORPLAN_CSS_GUIDE.md` § Specific section

### Visual Examples
→ See `CSS_STYLING_EXAMPLES.md` § Relevant example

### Configuration Issues
→ Check `floorplan-config.yaml` model definitions

---

## 🔗 External Resources

- **BH90 Standards** - Swedish construction drawing standards
- **CSS Variables** - [MDN Web Docs](https://developer.mozilla.org/en-US/docs/Web/CSS/Using_CSS_custom_properties)
- **SVG Styling** - [W3C SVG Specification](https://www.w3.org/TR/SVG2/)
- **IFC Schema** - [buildingSMART Documentation](https://technical.buildingsmart.org/)

---

## ✅ Quick Test

After reading documentation, test your understanding:

```bash
# 1. Can you generate a floor plan?
./generate-coordinated-floorplan.sh "020 Mezzanine +5.40m" 5.40

# 2. Can you adjust line weights?
# Edit floorplan-styles.css, change --stroke-base

# 3. Can you identify layer classes in SVG?
grep 'class=' /output/converted/floorplans/coord_all_*.svg | head -10

# 4. Can you explain the difference between floor and ceiling MEP?
# Answer: Ceiling MEP uses dashed lines (stroke-dasharray: 6 4) and is 20% thinner
```

If you can do all 4, you're ready to use the system! 🎉

---

## 📅 Version History

- **v1.0** (Oct 16, 2025) - Initial BH90-inspired CSS system
  - Variable-based styling
  - Floor/ceiling distinction
  - Comprehensive documentation
  - Migration from inline CSS

---

## 🎯 Summary

**5 Documentation Files:**
1. `CSS_UPGRADE_README.md` - Overview & quick start
2. `FLOORPLAN_CSS_GUIDE.md` - Complete reference
3. `CSS_STYLING_EXAMPLES.md` - Visual examples
4. `CSS_MIGRATION_GUIDE.md` - Migration help
5. `CSS_DOCUMENTATION_INDEX.md` - This index

**1 Stylesheet:**
- `floorplan-styles.css` - BH90-inspired CSS

**2 Scripts (Updated):**
- `generate-coordinated-floorplan.sh` - Single floor plan
- `generate-all-coordinated.sh` - Batch generation

**All integrated with:**
- `floorplan-config.yaml` - Your existing configuration

---

**Happy reading! 📚** Choose your starting document above. 👆

