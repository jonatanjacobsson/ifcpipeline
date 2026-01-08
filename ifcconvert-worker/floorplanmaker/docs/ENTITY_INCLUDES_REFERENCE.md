# IFC Entity Includes Reference

This document shows the IFC entity types included in each layer for the coordinated floor plan scripts.

## Comparison: Basic vs Enhanced

### 📐 Architecture Layer

**Basic Version:**
```
IfcWall
IfcDoor
IfcWindow
IfcStair
IfcRailing
```

**Enhanced Version (ADDED):**
```
IfcWall
IfcWallStandardCase          ← NEW
IfcCurtainWall               ← NEW
IfcDoor
IfcWindow
IfcStair
IfcStairFlight               ← NEW
IfcRailing
IfcRamp                      ← NEW
IfcRampFlight                ← NEW
IfcRoof                      ← NEW
IfcSlab                      ← NEW
IfcCovering                  ← NEW (flooring, cladding, etc.)
IfcPlate                     ← NEW
IfcMember                    ← NEW
IfcBuildingElementProxy      ← NEW (generic elements)
IfcColumn                    ← NEW
IfcBeam                      ← NEW
IfcChimney                   ← NEW
IfcShadingDevice             ← NEW
IfcCivilElement              ← NEW
```

### 🏗️ Structural Layer

**Basic Version:**
```
IfcColumn
IfcBeam
IfcSlab
IfcFooting
IfcPile
IfcWall
```

**Enhanced Version (ADDED):**
```
IfcColumn
IfcBeam
IfcSlab
IfcFooting
IfcPile
IfcWall
IfcWallStandardCase          ← NEW
IfcMember                    ← NEW
IfcPlate                     ← NEW
IfcReinforcingBar            ← NEW (rebar)
IfcReinforcingMesh           ← NEW (mesh)
IfcTendon                    ← NEW (post-tensioning)
IfcTendonAnchor              ← NEW
IfcBearing                   ← NEW (structural bearings)
IfcDeepFoundation            ← NEW
IfcCaissonFoundation         ← NEW
IfcPileFoundation            ← NEW
```

### 🚰 Plumbing Layer

**Basic Version:**
```
IfcPipeSegment
IfcPipeFitting
IfcFlowSegment
IfcFlowFitting
IfcFlowTerminal
IfcFlowController
IfcFlowTreatmentDevice
IfcEnergyConversionDevice
IfcFlowStorageDevice
IfcTank
IfcPump
IfcFlowMeter
```

**Enhanced Version (ADDED):**
```
IfcPipeSegment
IfcPipeFitting
IfcFlowSegment
IfcFlowFitting
IfcFlowTerminal
IfcFlowController
IfcFlowTreatmentDevice
IfcEnergyConversionDevice
IfcFlowStorageDevice
IfcTank
IfcPump
IfcFlowMeter
IfcValve                     ← NEW (valves)
IfcSanitaryTerminal          ← NEW (sinks, toilets, etc.)
IfcWasteTerminal             ← NEW
IfcStackTerminal             ← NEW (drainage stacks)
IfcDrainageSegment           ← NEW
IfcDrainageFitting           ← NEW
IfcFireSuppressionTerminal   ← NEW (sprinkler heads)
IfcSprinkler                 ← NEW
IfcBoiler                    ← NEW
IfcChiller                   ← NEW
IfcCooledBeam                ← NEW
IfcCoolingTower              ← NEW
IfcHeatExchanger             ← NEW
IfcHumidifier                ← NEW
IfcTubeBundle                ← NEW
IfcWaterHeater               ← NEW (hot water heaters)
```

### 🌬️ Mechanical (HVAC) Layer

**Basic Version:**
```
IfcDuctSegment
IfcDuctFitting
IfcFlowSegment
IfcFlowFitting
IfcFlowTerminal
IfcAirTerminal
IfcFlowController
IfcDamper
IfcFlowTreatmentDevice
IfcEnergyConversionDevice
IfcFlowStorageDevice
IfcFan
IfcCoil
IfcFilter
```

**Enhanced Version (ADDED):**
```
IfcDuctSegment
IfcDuctFitting
IfcFlowSegment
IfcFlowFitting
IfcFlowTerminal
IfcAirTerminal
IfcAirTerminalBox            ← NEW (VAV boxes)
IfcFlowController
IfcDamper
IfcFlowTreatmentDevice
IfcEnergyConversionDevice
IfcFlowStorageDevice
IfcFan
IfcCoil
IfcFilter
IfcAirToAirHeatRecovery      ← NEW (heat recovery units)
IfcCompressor                ← NEW
IfcCondenser                 ← NEW
IfcEvaporativeCooler         ← NEW
IfcEvaporator                ← NEW
IfcUnitaryEquipment          ← NEW (packaged units)
IfcAirHandler                ← NEW (AHU)
IfcVibrationIsolator         ← NEW
IfcDuctSilencer              ← NEW (sound attenuators)
```

### ⚡ Electrical Layer

**Basic Version:**
```
IfcCableCarrierSegment
IfcCableCarrierFitting
IfcFlowSegment
IfcFlowFitting
IfcFlowTerminal
IfcElectricDistributionPoint
IfcElectricAppliance
IfcLightFixture
IfcFlowController
IfcFlowTreatmentDevice
IfcEnergyConversionDevice
IfcFlowStorageDevice
```

**Enhanced Version (ADDED):**
```
IfcCableCarrierSegment       (cable trays, conduits)
IfcCableCarrierFitting
IfcCableSegment              ← NEW (actual cables)
IfcCableFitting              ← NEW (cable connections)
IfcFlowSegment
IfcFlowFitting
IfcFlowTerminal
IfcElectricDistributionBoard ← NEW (panels, switchboards)
IfcElectricDistributionPoint
IfcElectricAppliance
IfcLightFixture
IfcLamp                      ← NEW (individual lamps)
IfcFlowController
IfcFlowTreatmentDevice
IfcEnergyConversionDevice
IfcFlowStorageDevice
IfcMotorConnection           ← NEW
IfcOutlet                    ← NEW (power outlets)
IfcSwitchingDevice           ← NEW (switches, breakers)
IfcTransformer               ← NEW
IfcElectricFlowStorageDevice ← NEW (batteries, UPS)
IfcElectricGenerator         ← NEW
IfcElectricMotor             ← NEW
IfcUnitaryControlElement     ← NEW (control devices)
IfcSensor                    ← NEW (sensors)
IfcActuator                  ← NEW
IfcAlarm                     ← NEW (fire alarms, etc.)
IfcController                ← NEW (BMS controllers)
```

### 🪑 Furniture & Equipment Layer

**NEW LAYER (not in basic version):**
```
IfcFurniture                 (desks, chairs, tables)
IfcSystemFurnitureElement    (modular furniture systems)
IfcFurnishingElement         (curtains, artwork, etc.)
IfcDistributionElement       (generic distribution elements)
IfcTransportElement          (elevators, escalators)
IfcVirtualElement            (virtual/placeholder elements)
IfcEquipmentElement          (fixed equipment)
IfcBuildingElementPart       (component parts)
```

### 📦 Spaces Layer

**No changes** (same in both versions):
```
IfcSpace                     (room boundaries and labels)
```

---

## How to Customize Further

You can easily add or remove entity types by editing the `--include entities` line in the script:

### Example: Add More Architecture Elements

```bash
--include entities IfcWall IfcDoor IfcWindow IfcOpeningElement IfcCovering
```

### Example: Focus Only on Specific MEP Equipment

```bash
# Only pumps and valves:
--include entities IfcPump IfcValve IfcFlowMeter
```

### Common IFC Entity Patterns

- `IfcWall*` - Wall variants (WallStandardCase, CurtainWall, etc.)
- `IfcFlow*` - MEP flow elements (generic)
- `IfcPipe*` - Piping specific
- `IfcDuct*` - Ductwork specific
- `IfcCable*` - Cable/conduit specific
- `IfcElectric*` - Electrical specific
- `IfcSanitary*` - Plumbing fixtures
- `IfcFire*` - Fire protection

---

## Performance Note

The enhanced version includes **many more entity types** but runs at approximately the **same speed** as the basic version because:

1. IfcConvert filters efficiently
2. Only elements assigned to the specified storey are processed
3. Empty entity types add minimal overhead

**Recommendation:** Use the enhanced version by default for completeness, or create a custom version with only the entities you need for your specific project.

---

## Script Files

- **Basic**: `generate-coordinated-floorplan-by-storey.sh` (6 layers)
- **Enhanced**: `generate-coordinated-floorplan-by-storey-enhanced.sh` (7 layers)
- **Original**: `generate-coordinated-floorplan.sh` (9 layers, section-height based)

