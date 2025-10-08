# Fix: Use Actual Input Filename Instead of IFC Header Filename

## Problem

When uploading IFC files to Dalux, the recipe was using the **wrong filename**:

- **Input file:** `/uploads/A-40-V-201-00-00_ceilinggrid.ifc` (what you specify in n8n)
- **IFC header:** `FILE_NAME = "Building-Architecture.ifc"` (what the file was originally called when created)
- **Uploaded as:** `"Building-Architecture.ifc"` ❌ (WRONG!)

### Why This Happened

IFC files store metadata in their header, including the original filename from when the file was created. When you rename or copy an IFC file, the internal header doesn't update - it still has the old name.

The DaluxUpload recipe was reading from this header:
```python
header_path = self.file.wrapped_data.header.file_name.name
# Returns: "Building-Architecture.ifc" (from when file was created)
```

But it should use the **actual filename** you're trying to upload!

## Solution

### Changes Made

#### 1. tasks.py (Line 291-293)
Added code to pass the actual input filename to custom recipes:

```python
# Set the actual input path as an attribute on the IFC file object
# This allows custom recipes to know the real filename
ifc_file._input_file_path = input_path
```

Now the IFC file object carries information about the actual file path it was loaded from.

#### 2. DaluxUpload.py `_get_file_info()` method
Updated to **prioritize the actual input filename** over the IFC header:

```python
# Priority 1: Use the actual input file path if available
if hasattr(self.file, '_input_file_path') and self.file._input_file_path:
    self.file_path = self.file._input_file_path
    self.file_name = os.path.basename(self.file_path)
    self.logger.info(f"Using actual input filename: {self.file_name}")
else:
    # Fallback: Get filename from IFC header
    header_path = self.file.wrapped_data.header.file_name.name
    ...
```

## Result

**Now:**
- **Input file:** `/uploads/A-40-V-201-00-00_ceilinggrid.ifc`
- **Uploaded as:** `A-40-V-201-00-00_ceilinggrid.ifc` ✅ (CORRECT!)

The filename in Dalux will match the actual file you're uploading, not some old name from the IFC header.

## Logs

After the fix, you'll see:
```
INFO: Using actual input filename: A-40-V-201-00-00_ceilinggrid.ifc
INFO: Source path: /uploads/A-40-V-201-00-00_ceilinggrid.ifc
INFO: Filename to upload: A-40-V-201-00-00_ceilinggrid.ifc
```

Instead of:
```
INFO: IFC header filename: Building-Architecture.ifc  ❌
```

## Why This Matters

### File Management
- Files in Dalux have the correct, expected names
- Version numbers in filenames are preserved (e.g., `v2.3`)
- Dates and revisions in filenames are maintained

### Traceability
- Easy to match files in Dalux with source files
- No confusion about which file is which
- Clear audit trail

### Workflow
- Automated workflows can rely on predictable filenames
- No manual renaming needed in Dalux
- Consistent naming across systems

## Example Scenarios

### Scenario 1: Versioned Files
```
Input: Building-Architecture_v2.3_2025-10-07.ifc
IFC Header: Building-Architecture.ifc (old name)
Uploaded as: Building-Architecture_v2.3_2025-10-07.ifc ✅
```

### Scenario 2: Multiple Disciplines
```
Input: A-40-V-201-00-00_architecture.ifc
IFC Header: Project123.ifc (generic name)
Uploaded as: A-40-V-201-00-00_architecture.ifc ✅
```

### Scenario 3: Copied Files
```
Input: backup_copy_model.ifc
IFC Header: original_model.ifc (from copy source)
Uploaded as: backup_copy_model.ifc ✅
```

## Technical Details

### The `_input_file_path` Attribute

The `tasks.py` file sets this attribute before running custom recipes:
```python
ifc_file._input_file_path = input_path
```

This is a simple Python attribute assignment. The underscore prefix indicates it's an internal/private attribute used for coordination between the task runner and custom recipes.

### Fallback Behavior

If `_input_file_path` is not available (e.g., when used outside the task runner), the recipe falls back to the IFC header method. This ensures backward compatibility.

### Why Not Always Use Header?

The IFC header filename is often:
- Outdated (file was renamed after creation)
- Generic (e.g., "Untitled.ifc", "Model.ifc")
- From a different context (copied from another project)
- Not matching your naming convention

The actual input filename is:
- Current and accurate
- What you explicitly specified
- Matches your file management system
- Includes versions, dates, and metadata you added

## Testing

Try uploading a file with a descriptive name:
```
Input file in n8n: /uploads/Project-A_Architecture_v3.2_2025-10-07.ifc
```

Check the Dalux API log to verify it uploads as:
```json
{
  "fileName": "Project-A_Architecture_v3.2_2025-10-07.ifc"
}
```

Not as some old name from the IFC header!

## Date
2025-10-07

