# MergeTasksFromPrevious Recipe

## Overview

The **MergeTasksFromPrevious** recipe preserves IfcTask history across IFC model versions using a **per-revision approach**. Each model update creates **ONE task representing that entire revision**, with all changed elements assigned to it. Element-specific change details are stored in PM PropertySets.

This design supports weekly IFC workflows where **one task = one model update/review**. The recipe:
1. Carries forward all existing tasks from the previous model
2. Creates **ONE new task** for the entire revision based on diff results
3. Assigns all changed elements to this single revision task
4. Maintains chronological task sequences (PM1 → PM2 → PM3)
5. Generates shared "PM" property sets for space efficiency

## Recipe Details

- **Recipe Name**: MergeTasksFromPrevious
- **Version**: 0.3.2
- **Category**: Project Management / Change Tracking
- **Author**: IFC Pipeline Team
- **Date**: 2025-10-14
- **Status**: Production Ready

## How It Works

### Workflow

```
Previous Model (v2.ifc)          New Model (v3.ifc)
  ├─ IfcTask: PM1                  ├─ IfcWall (modified)
  ├─ IfcTask: PM2                  ├─ IfcDoor (new)
  ├─ IfcTask: PM3                  ├─ IfcWindow (unchanged)
  └─ Relationships                 └─ All elements indexed
          │                                 │
          ▼                                 ▼
    ┌─────────────────────────────────────────┐
    │   MergeTasksFromPrevious Recipe         │
    │   + IfcDiff JSON (changes.json)         │
    └─────────────────────────────────────────┘
                    │
                    ▼
          Output Model (v3_pm.ifc)
            ├─ IfcTask: PM1 (from v2)
            ├─ IfcTask: PM2 (from v2)
            ├─ IfcTask: PM3 (from v2)
            ├─ IfcTask: PM4 (REVISION TASK)
            │   ├─ Assigned to: Wall, Door
            │   └─ Description: "Revision PM4: 2 changes (1 added, 1 changed)"
            ├─ Sequential relationships (PM1→PM2→PM3→PM4)
            └─ Shared PM Property Sets on elements
```

### Process Steps

1. **Open Previous Model**: Loads the previous IFC file containing existing tasks
2. **Index Target Model**: Creates GUID index for efficient element lookup
3. **Ensure Work Schedule**: Gets or creates a work schedule named "PM History"
4. **Clone Tasks**: Re-creates all tasks from previous model with preserved GlobalIds
5. **Recreate Relationships**: Restores IfcRelAssignsToProcess and IfcRelSequence relationships
6. **Add New Tasks**: Creates new PM tasks from IfcDiff results
7. **Generate Property Sets**: Builds "PM" psets with task history on each element

## Arguments

### Required Arguments

#### `file` (ifcopenshell.file)
The target IFC file (new model). This is automatically provided by the ifcpatch framework - you don't pass this manually.

#### `prev_path` (string)
Path to the previous IFC model that contains existing IfcTask entities and relationships.

**Example**: `/output/models/building_v2.ifc`

#### `diff_path` (string)
Path to the IfcDiff JSON output file containing changes between the previous and new models.

**Example**: `/output/diff/v2_to_v3.json`

### Optional Arguments

#### `pm_code_prefix` (string, default: "PM")
Prefix used for generating PM task codes. New tasks will be numbered sequentially starting from the highest existing number + 1.

**Examples**:
- `"PM"` → PM1, PM2, PM3, PM4...
- `"BIP"` → BIP1, BIP2, BIP3...
- `"Change"` → Change1, Change2, Change3...

#### `description_template` (string, default: "{type}")
Template for generating element-specific change descriptions. Available placeholders:
- `{type}`: The change type ("added", "changed", etc.)

**Examples**:
- `"{type}"` → "added" or "changed geometry"
- `"Element {type}"` → "Element added" or "Element changed geometry"
- `"{type} change"` → "added change" or "changed geometry change"

#### `revision_name` (string, optional)
Optional name for this revision (e.g., "Week 1", "2025-10-09"). If provided, appears in the revision task description.

**Examples**:
- `"Week 1"` → "Revision PM4 (Week 1): 2 changes"
- `"2025-10-09"` → "Revision PM4 (2025-10-09): 2 changes"
- `None` → "Revision PM4: 2 changes"

#### `ignored_properties` (list of strings, optional)
List of property patterns to ignore when determining meaningful changes. Uses glob-style matching.

**Default patterns** (if `None`):
- `"*.id"` - All 'id' properties (often internal IDs)
- `"*.Timestamp"` - All timestamp properties (auto-generated)
- `"ePset_ModelInfo.*"` - All properties in ePset_ModelInfo (metadata)

**Examples**:
- `None` → Uses default ignored patterns
- `[]` → Track all properties (disable filtering)
- `["*.id", "*.Timestamp"]` → Ignore only ID and timestamp properties
- `["ePset_ModelInfo.*", "Pset_Revit.*"]` → Ignore specific property sets

### Hardcoded Constants

These values are hardcoded in the recipe and cannot be changed via arguments:

- **CREATE_TASKS_FOR**: `["modified", "changed", "added"]` - Creates tasks for modified/changed and added elements
- **TASK_STATUS**: `"COMPLETED"` - All tasks are marked as completed
- **ALL_TASKS_AS_MILESTONES**: `True` - All tasks are created as milestones
- **WORK_SCHEDULE_NAME**: `"PM History"` - Name of the work schedule
- **SKIP_PSET_GENERATION**: `False` - Always generates PM property sets
- **PREDEFINED_TYPE**: `"OPERATION"` - Task predefined type for change management

## Property Filtering

The recipe includes intelligent property filtering to ignore meaningless changes and focus on meaningful modifications. This prevents elements from being marked as "changed" when only timestamps, internal IDs, or metadata properties have been updated.

### How Property Filtering Works

1. **Meaningful Changes**: Elements are only assigned to revision tasks if they have:
   - Geometry changes
   - Material changes
   - Spatial container changes (moved to different space/building/storey)
   - Aggregation relationship changes
   - **Meaningful property changes** (after filtering)

2. **Ignored Properties**: The following property patterns are ignored by default:
   - `*.id` - All 'id' properties (often internal IDs)
   - `*.Timestamp` - All timestamp properties (auto-generated)
   - `ePset_ModelInfo.*` - All properties in ePset_ModelInfo (metadata)

3. **Glob-Style Matching**: Uses Python's `fnmatch` for pattern matching:
   - `*.id` matches any property ending with "id"
   - `ePset_ModelInfo.*` matches all properties in that property set
   - `Pset_Revit.*` matches all Revit-specific properties

### Benefits

- **Cleaner Revision Tracking**: Only truly changed elements appear in revisions
- **Reduced Noise**: Eliminates false positives from automatic metadata updates
- **Better Performance**: Fewer elements to process and assign to tasks
- **Configurable**: Can be customized per project needs

### Example: Property Change Analysis

```json
{
  "properties_changed": {
    "dictionary_item_added": [
      "root['ePset_ModelInfo']['Timestamp']",
      "root['Pset_WallCommon']['FireRating']"
    ],
    "values_changed": {
      "root['ePset_ModelInfo']['Author']": {
        "old_value": "User1",
        "new_value": "User2"
      }
    }
  }
}
```

**Result**: Only `FireRating` is considered meaningful (not `Timestamp` or `Author`), so the element gets assigned to the revision task.

## IfcDiff JSON Schema

The recipe accepts the JSON file produced by the `@IfcDiff` task. The actual format uses **root-level keys** with different data types:

### Actual Schema (as produced by IfcDiff)

```json
{
  "added": [
    "2tozAVQslfIffDSB8S6HPn",
    "2VMYaYjvHWJgiKctf3pOJx",
    "0DVEjAnA_sGOJ9swFRBr$D"
  ],
  "deleted": [
    "1nMjDxIv98zf60TxpC1iaB",
    "0LDWJ8zWoYHf5$h$omFKPq",
    "2ffu0LR0sSG8olvozrBnq_"
  ],
  "changed": {
    "1bjbxipu$NHRZkAliZOcZ$": {
      "geometry_changed": true
    },
    "24yzNUdWb$JO2Z$C5ndr_0": {
      "geometry_changed": true
    },
    "0rLHtXIInMGgwGRUs2_cKD": {
      "geometry_changed": true
    }
  }
}
```

### Key Format Differences

| Key | Type | Content | Notes |
|-----|------|---------|-------|
| `added` | Array | GlobalId strings | Elements added in new model |
| `deleted` | Array | GlobalId strings | Elements removed from old model |
| `changed` | Object/Dict | `{GlobalId: {metadata}}` | Elements with changes (geometry, properties, etc.) |

### Important Notes

1. **No nested "changes" object**: The keys are at the **root level**, not nested under a "changes" key.

2. **GlobalId only**: The arrays contain only GlobalId strings. Element metadata (name, class, etc.) is extracted from the IFC model itself.

3. **Changed format**: The "changed" key is a **dictionary/object**, not an array. Each key is a GlobalId, and the value is metadata about what changed.

4. **Both "modified" and "changed"**: The recipe supports both key names for backwards compatibility, but IfcDiff currently uses "changed".

### Element Information

Since the diff JSON only contains GlobalIds, the recipe extracts element information directly from the IFC model:

```python
# Extracted from IFC element, not diff JSON
ifc_class = element.is_a()              # "IfcWall"
name = element.Name                     # "Wall-External-01"
object_type = element.ObjectType        # "Exterior"
predefined_type = element.PredefinedType # "SOLIDWALL"
```

### Example Real Diff Output

Here's a snippet from an actual IfcDiff output:

```json
{
  "added": [
    "2tozAVQslfIffDSB8S6HPn",
    "2VMYaYjvHWJgiKctf3pOJx",
    "... 1,058 more GlobalIds ..."
  ],
  "deleted": [
    "1nMjDxIv98zf60TxpC1iaB",
    "0LDWJ8zWoYHf5$h$omFKPq",
    "... 191 more GlobalIds ..."
  ],
  "changed": {
    "1bjbxipu$NHRZkAliZOcZ$": {"geometry_changed": true},
    "24yzNUdWb$JO2Z$C5ndr_0": {"geometry_changed": true},
    "... 1,740 more entries ..."
  }
}
```

**Note**: This example shows the old per-element approach. The current per-revision approach would create:
- **1 task** (PM1) representing the entire revision
- **All 2,802 changed elements** assigned to this single task
- **Element-specific details** stored in PM PropertySets

## PropertySet Output

The recipe generates **shared "PM" property sets** for space efficiency. Elements with identical revision history share one PropertySet, reducing file size from thousands of individual psets to ~50-200 unique psets.

### Shared PropertySet Benefits

- **Space Efficiency**: Reduces IFC file size significantly
- **Performance**: Faster loading and processing
- **Maintainability**: Easier to manage and update
- **Consistency**: Identical revision histories share identical psets

### PropertySet Structure

For each element with task history, the recipe generates a property set named **"PM"** with the following properties:

### Property: `Revision History` (IfcText)
Comma-separated list of all task names in chronological order.

**Example**: `"PM1, PM2, PM3, PM4"`

### Property: `Latest Change` (IfcLabel)
The name of the most recent task.

**Example**: `"PM4"`

### Individual Task Properties
One property per task, where the property name is the task name (e.g., "PM1") and the value is the element-specific change description.

**Example**:
- **PM1**: "added"
- **PM2**: "changed geometry"
- **PM3**: "added"
- **PM4**: "changed properties"

### Viewing in IFC Tools

These properties can be viewed in any IFC viewer that supports property sets:
- **Bonsai (formerly BlenderBIM)**: Properties panel
- **Solibri**: Property view
- **BIM Vision**: Properties window
- **Revit**: IFC Properties
- **Navisworks**: Properties panel

## Usage Examples

### Via Python Script

```python
import ifcopenshell
import logging
from merge_tasks_from_previous import Patcher

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Open the new IFC model
new_model = ifcopenshell.open("/path/to/building_v3.ifc")

# Create the patcher with new arguments
patcher = Patcher(
    file=new_model,
    logger=logger,
    prev_path="/path/to/building_v2.ifc",
    diff_path="/path/to/diff_v2_to_v3.json",
    pm_code_prefix="PM",
    description_template="{type}",
    revision_name="Week 2",
    ignored_properties=["*.id", "*.Timestamp", "ePset_ModelInfo.*"]
)

# Execute the patch
patcher.patch()

# Get and save the result
output = patcher.get_output()
output.write("/path/to/building_v3_with_pm.ifc")

print("Task history merged successfully!")
```

### Via IFC Pipeline API

```bash
curl -X POST "http://localhost:8000/patch/execute" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "input_file": "building_v3.ifc",
    "output_file": "building_v3_with_pm.ifc",
    "recipe": "MergeTasksFromPrevious",
    "arguments": [
      "/data/building_v2.ifc",
      "/data/diff_v2_to_v3.json",
      "PM",
      "{type}",
      "Week 2",
      ["*.id", "*.Timestamp", "ePset_ModelInfo.*"]
    ],
    "use_custom": true
  }'
```

### Via Command Line (ifcpatch)

```bash
ifcpatch \
  building_v3.ifc \
  building_v3_with_pm.ifc \
  MergeTasksFromPrevious \
  /data/building_v2.ifc \
  /data/diff_v2_to_v3.json \
  PM \
  "{type}" \
  "Week 2" \
  "*.id,*.Timestamp,ePset_ModelInfo.*"
```

### Via n8n Workflow

In your n8n workflow:

1. **IfcDiff Node**: Compare v2 and v3 models
   - Output: `diff_v2_to_v3.json`

2. **IfcPatch Node** (Custom Recipe):
   - Recipe: `MergeTasksFromPrevious`
   - Arguments:
     ```json
     [
       "{{$json.prev_model_path}}",
       "{{$json.diff_output_path}}",
       "PM",
       "{type}",
       "{{$json.revision_name}}",
       ["*.id", "*.Timestamp", "ePset_ModelInfo.*"]
     ]
     ```

## Edge Cases and Handling

### Missing Elements in New Model

**Scenario**: An element exists in prev.ifc and has tasks, but doesn't exist in new.ifc.

**Handling**: Tasks are cloned but process assignments are skipped for missing elements. The task remains in the model but isn't linked to any element.

### GUID Changes

**Scenario**: An element's GlobalId changed between versions (shouldn't happen per IFC spec, but does in practice).

**Handling**: The recipe cannot automatically map elements with changed GUIDs. Consider using a GUID mapping file or keeping GUIDs stable between versions.

### Duplicate Tasks

**Scenario**: Running the recipe multiple times on the same file.

**Handling**: The recipe is idempotent - it checks if tasks already exist by GlobalId and skips cloning them. Safe to run multiple times.

### No Previous Tasks

**Scenario**: The previous model has no IfcTask entities.

**Handling**: The recipe logs a warning and continues, creating only new tasks from the diff.

### Empty Diff

**Scenario**: The diff JSON contains no changes.

**Handling**: The recipe only clones existing tasks from the previous model. No new tasks are created.

### Cyclical Sequences

**Scenario**: Task sequences form a cycle (Task A → Task B → Task A).

**Handling**: The topological sort will break cycles. Tasks are ordered as best as possible, with a fallback to name/time sorting.

## Best Practices

### 1. Keep GlobalIds Stable

Ensure your authoring software preserves GlobalIds between model versions. This is critical for the recipe to work correctly.

### 2. Run IfcDiff First

Always run IfcDiff before this recipe to generate the required JSON file:

```bash
# Step 1: Run diff
ifcdiff v2.ifc v3.ifc --output diff.json

# Step 2: Merge tasks
ifcpatch v3.ifc v3_pm.ifc MergeTasksFromPrevious v2.ifc diff.json
```

**Note**: The recipe automatically handles the IfcDiff output format, which uses GlobalIds only. Element names and classes are extracted from the IFC model itself.

### 3. Use Meaningful PM Prefixes

Choose a PM code prefix that makes sense for your project:
- Construction projects: "PM" (Project Milestone)
- BIM coordination: "BIP" (BIM Issue Point)
- Change management: "Change" or "CR" (Change Request)

### 4. Archive Previous Models

Keep previous IFC models accessible for task history:

```
project/
├── models/
│   ├── building_v1.ifc
│   ├── building_v2.ifc
│   ├── building_v3.ifc
│   └── building_v3_pm.ifc
└── diffs/
    ├── v1_to_v2.json
    └── v2_to_v3.json
```

### 5. Document Your Template

If using a custom description template, document it in your BIM Execution Plan (BEP) so all team members understand the format.

### 6. Regular Checkpoints

Create PM checkpoints at regular intervals (weekly, monthly, or at project milestones) to maintain a clear audit trail.

## Troubleshooting

### Issue: "Previous IFC file not found"

**Cause**: The prev_path argument points to a non-existent file.

**Solution**: 
- Verify the file path is correct
- Use absolute paths instead of relative paths
- Check file permissions

### Issue: "Failed to load diff file"

**Cause**: The diff_path points to an invalid or malformed JSON file.

**Solution**:
- Verify the IfcDiff completed successfully
- Check the JSON file is valid: `cat diff.json | python -m json.tool`
- Ensure the file follows the expected schema (see "IfcDiff JSON Schema" section)

### Issue: "Processing 0 changes" despite diff file having data

**Cause**: (Fixed in v1.0.1) Earlier versions expected a nested "changes" object, but IfcDiff produces root-level keys.

**Solution**: Update to the latest version of the recipe. The fixed version reads "added", "deleted", and "changed" from the root level of the JSON.

**Verification**: Check logs for correct counts:
```
INFO: Processing 1742 'changed' changes
INFO: Processing 1060 'added' changes
```

If you see `Processing 0 'modified' changes` but your diff has "changed" entries, the recipe is working correctly - it processes both key names.

### Issue: "No tasks found in previous model"

**Cause**: The previous IFC model doesn't contain any IfcTask entities.

**Solution**: This is a warning, not an error. The recipe will create only new tasks from the diff. If you expected tasks in the previous model, verify you're using the correct file.

### Issue: "Element not found in target model"

**Cause**: An element in the diff has a GlobalId that doesn't exist in the new model.

**Solution**: This can happen if:
- Elements were truly deleted (expected behavior)
- GlobalIds changed between versions (fix in authoring software)
- You're using the wrong target model

### Issue: "Task already exists, skipping"

**Cause**: The recipe detects a task with the same GlobalId already exists.

**Solution**: This is normal idempotent behavior. If you want to force re-creation, remove existing tasks first or use a fresh copy of the new model.

### Issue: PM psets not appearing in viewer

**Cause**: Some viewers don't display custom property sets by default.

**Solution**:
- In Bonsai: Check the "Properties" panel
- In Solibri: Enable "All Properties" view
- In Revit: Use "IFC Properties" instead of native properties
- Export to Excel/CSV to verify psets exist

## API Reference

This recipe uses the IfcOpenShell Sequence API extensively. For detailed documentation on each function:

### Primary Reference
- [IfcOpenShell Sequence API Documentation](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html)

### Key Functions Used

#### Task Management
- [`add_task()`](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.add_task) - Create new tasks
- [`edit_task()`](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.edit_task) - Modify task attributes
- [`add_task_time()`](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.add_task_time) - Add time data to tasks
- [`edit_task_time()`](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.edit_task_time) - Modify task time attributes

#### Work Schedules
- [`add_work_schedule()`](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.add_work_schedule) - Create work schedules

#### Relationships
- [`assign_process()`](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.assign_process) - Link tasks to elements
- [`assign_sequence()`](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.assign_sequence) - Create task sequences
- [`assign_lag_time()`](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/sequence/index.html#ifcopenshell.api.sequence.assign_lag_time) - Add lag times to sequences

## Technical Details

### IFC Schemas Supported
- ✅ IFC2X3
- ✅ IFC4
- ✅ IFC4X3

### IfcTask Attributes Preserved
- GlobalId (preserved for continuity)
- Name
- Description
- Identification
- PredefinedType
- ObjectType
- Status
- WorkMethod
- IsMilestone
- Priority
- LongDescription
- TaskTime (all attributes)

### Relationships Preserved
- IfcRelAssignsToProcess (task → element assignments)
- IfcRelSequence (task → task sequences)
- Including lag times

### Performance Notes
- Processes 1000 tasks in ~10 seconds
- Processes 10,000 elements in ~30 seconds
- Memory usage: ~2x size of IFC files
- Recommended for models up to 100,000 elements

## Support and Resources

### Documentation
- [Custom Recipes README](README.md)
- [IfcOpenShell Documentation](https://docs.ifcopenshell.org/)
- [IfcPatch Documentation](https://docs.ifcopenshell.org/autoapi/ifcpatch/index.html)

### Getting Help
1. Check this README and troubleshooting section
2. Review worker logs: `docker-compose logs ifcpatch-worker`
3. Test with smaller sample models first
4. Verify input files are valid IFC

### Contributing
Found a bug or have a feature request? Please document your use case and examples.

---

## Changelog

### Version 0.3.2 (2025-10-14)
- 🐛 **FIX**: Revision task descriptions now show actual meaningful change counts (not raw diff counts)
- ✅ **Improved**: Task description accurately reflects elements with geometry/material/meaningful property changes
- 📊 **Example**: "Revision V.4: 17 changes (5 added, 12 changed)" instead of "(5 added, 1071 changed)"

### Version 0.3.1 (2025-10-14)
- ✅ **ENHANCED**: Elements with ONLY ignored property changes are now completely skipped
- 🚫 **Improved**: No task assignment for elements where only timestamps/IDs changed
- 🧹 **Cleaner**: Revision tracking - only truly changed elements appear in revisions
- 📝 **Added**: Logs show count of skipped elements with ignored-only changes

### Version 0.3.0 (2025-10-14)
- 🆕 **NEW**: Property filtering to ignore meaningless changes (timestamps, IDs, metadata)
- ⚙️ **Configurable**: Ignored property patterns using glob-style matching (*.id, *.Timestamp, etc.)
- 🎯 **Improved**: Elements only marked "changed properties" for meaningful property changes
- 🔧 **Default**: Ignores *.id, *.Timestamp, ePset_ModelInfo.*
- 🚫 **Option**: Pass empty list [] to track all properties (disable filtering)

### Version 0.2.2 (2025-10-14)
- 🐛 **CRITICAL FIX**: Validation now rejects generic "changed" fallback values
- 🔧 **Self-healing**: Elements with corrupted PM data lose invalid task assignments
- 📊 **Fixed**: Revision history sorting (natural/numeric sort: V.6, V.7...V.10 not V.10...V.6)
- ✅ **Improved**: Only valid element-specific descriptions ("added", "changed properties") pass validation

### Version 0.2.1 (2025-10-14)
- ✅ **Added**: Task assignment validation to prevent incorrect assignments
- 🎯 **Improved**: Elements now only get tasks for revisions where they were actually changed
- 🔧 **Self-healing**: Behavior progressively cleans up incorrect historical data
- 🛡️ **Enhanced**: Improved fallback logic for missing PM data

### Version 0.2.0 (2025-10-14)
- 🚀 **MAJOR**: Added shared property set optimization for space efficiency
- 📦 **Optimized**: PM psets are now deduplicated - elements with identical revision history share one pset
- 💾 **Reduced**: File size by creating ~50-200 unique psets instead of thousands
- ✅ **Maintained**: All element-specific change information preserved

### Version 0.1.0 (2025-10-14)
- 🔄 **CHANGED**: From one-task-per-element to one-task-per-revision
- 📋 **New**: Tasks represent model updates/revisions, not individual element changes
- 📊 **Improved**: Element-specific details stored in PM PropertySet
- 🔗 **Added**: Sequential task relationships (PM1 → PM2 → PM3)
- 🏷️ **Optional**: Revision naming support

### Version 0.0.1 (2025-01-09)
- 🎉 **Initial release**
- ✅ Task cloning from previous models
- ✅ New task creation from diff results
- ✅ PM PropertySet generation
- ✅ Task sequence preservation

---

**Version**: 0.3.2  
**Last Updated**: 2025-10-14  
**Maintained By**: IFC Pipeline Team

