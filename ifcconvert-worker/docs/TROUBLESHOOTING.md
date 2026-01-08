# IfcConvert Worker - Troubleshooting Guide

## Overview

The IfcConvert worker has been enhanced with comprehensive logging and error handling to make troubleshooting easier.

## Logging Enhancements

### Job Start Logging
```
================================================================================
Starting IFC conversion job
Job data keys: ['input_filename', 'output_filename', 'threads', ...]
Parsing request from job data...
Request parsed successfully. Input: /uploads/model.ifc, Output: /output/model.glb
```

### Path Validation Logging
```
Processing paths:
  Input path:  /uploads/model.ifc
  Output path: /output/model.glb
  Log path:    (auto-generate)
Created/verified default output directory: /output/converted
Generated log file path: /output/converted/model_convert.txt
```

### File Validation Logging
```
Validating input file exists: /uploads/model.ifc
Input file size: 1,234,567 bytes (1.18 MB)
```

### Command Construction Logging
```
Constructing IfcConvert command...
Enabled options: threads=8, include=entities(2 items), center_model
Full command (15 arguments):
  /usr/local/bin/IfcConvert -y --log-format plain --log-file /output/converted/model_convert.txt -j 8 --include entities IfcWall IfcSlab /uploads/model.ifc /output/model.glb
```

### Execution Logging
```
Executing IfcConvert...
IfcConvert execution completed in 12.45 seconds
Return code: 0
IfcConvert stdout (150 chars):
  STDOUT: Processing file: /uploads/model.ifc
  STDOUT: Found 245 elements
  ... (truncated, see log file for full output)
```

### Success Logging
```
Verifying output file creation: /output/model.glb
Output file created successfully: 3,456,789 bytes (3.30 MB)
Output/Input size ratio: 280.1%
Saved to database with ID: 550e8400-e29b-41d4-a716-446655440000
================================================================================
✓ IFC conversion completed successfully!
  Input:  /uploads/model.ifc (1,234,567 bytes)
  Output: /output/model.glb (3,456,789 bytes)
  Time:   12.45 seconds
  Log:    /output/converted/model_convert.txt
  DB ID:  550e8400-e29b-41d4-a716-446655440000
================================================================================
```

## Error Handling

### File Not Found Errors
```
================================================================================
✗ FILE NOT FOUND ERROR during IFC conversion
  Error: Input file /uploads/model.ifc not found
================================================================================
Current working directory: /app
Directory contents: ['file1.ifc', 'file2.ifc']
Stack trace:
  [Full Python traceback]
```

### Timeout Errors
```
================================================================================
✗ TIMEOUT ERROR during IFC conversion
  Command timed out after 3600 seconds
================================================================================
```

### Runtime Errors (e.g., IfcConvert failures)
```
================================================================================
✗ RUNTIME ERROR during IFC conversion
  Error: IfcConvert failed with return code 1. Stderr: Error parsing IFC file
================================================================================
IfcConvert log (/output/converted/model_convert.txt):
  [Log file contents]
Stack trace:
  [Full Python traceback]
```

### Validation Errors
```
================================================================================
✗ VALIDATION ERROR during IFC conversion
  Error: Invalid value for parameter 'threads': must be positive integer
  Job data: {'input_filename': '/uploads/model.ifc', 'threads': -1, ...}
================================================================================
```

## Common Issues and Solutions

### Issue 1: Input File Not Found

**Symptoms:**
```
✗ FILE NOT FOUND ERROR during IFC conversion
  Error: Input file /uploads/model.ifc not found
```

**Possible Causes:**
1. File was not uploaded successfully
2. Incorrect file path specified
3. File was deleted before processing
4. Volume mount issue in Docker

**Solutions:**
1. Verify file exists in the uploads directory
2. Check file path is absolute and correct
3. Check Docker volume mounts in docker-compose.yml
4. Review upload logs for errors

### Issue 2: Output File Not Created

**Symptoms:**
```
✗ RUNTIME ERROR during IFC conversion
  Error: IfcConvert completed but output file was not created
Output directory contents: ['other_file.obj']
```

**Possible Causes:**
1. IfcConvert failed silently
2. Insufficient disk space
3. Permission issues
4. Output directory doesn't exist

**Solutions:**
1. Check IfcConvert log file for details
2. Verify disk space: `df -h`
3. Check directory permissions
4. Review IfcConvert stderr output

### Issue 3: IfcConvert Execution Failure

**Symptoms:**
```
IfcConvert failed with return code 1
Stderr: Error: Unable to parse IFC file
```

**Possible Causes:**
1. Corrupted IFC file
2. Unsupported IFC schema version
3. Invalid geometry in IFC file
4. Insufficient memory

**Solutions:**
1. Validate IFC file with IFC validator
2. Check IFC schema version compatibility
3. Try with `--disable-boolean-result` option
4. Increase container memory limit

### Issue 4: Timeout

**Symptoms:**
```
✗ TIMEOUT ERROR during IFC conversion
  Command timed out after 3600 seconds
```

**Possible Causes:**
1. Very large or complex IFC file
2. Too many threads causing resource contention
3. Complex boolean operations
4. Memory swapping

**Solutions:**
1. Increase timeout in tasks.py
2. Reduce thread count
3. Use `--disable-opening-subtractions` option
4. Increase container memory
5. Filter to process only needed elements

### Issue 5: Validation Error

**Symptoms:**
```
✗ VALIDATION ERROR during IFC conversion
  Error: field required
```

**Possible Causes:**
1. Missing required parameters
2. Invalid parameter types
3. Malformed request data

**Solutions:**
1. Check API request includes all required fields
2. Verify parameter types match schema
3. Review IfcConvertRequest model in classes.py

## Debugging Steps

### 1. Check Worker Logs

```bash
docker-compose logs -f ifcconvert-worker
```

Look for the job execution section between the separator lines (====).

### 2. Check RQ Job Status

Via API:
```bash
curl http://localhost:8000/jobs/{job_id}/status \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 3. Check IfcConvert Log File

The log file path is shown in the job output. Access it through:
```bash
docker exec ifcconvert-worker cat /output/converted/model_convert.txt
```

### 4. Check Docker Container Health

```bash
docker ps
docker stats ifcconvert-worker
docker exec ifcconvert-worker df -h
```

### 5. Verify File Paths

```bash
docker exec ifcconvert-worker ls -lh /uploads/
docker exec ifcconvert-worker ls -lh /output/
```

### 6. Test IfcConvert Directly

```bash
docker exec ifcconvert-worker /usr/local/bin/IfcConvert --help
docker exec ifcconvert-worker /usr/local/bin/IfcConvert /uploads/test.ifc /output/test.obj
```

## Performance Tuning

### Optimal Thread Count

- CPU cores + 1 for I/O bound operations
- CPU cores for CPU bound operations
- Start with 4-8 threads and adjust based on results

### Memory Optimization

```python
{
  "threads": 4,  # Reduce for large files
  "mesher_linear_deflection": 0.01,  # Increase for lower detail
  "disable_opening_subtractions": True,  # Skip expensive operations
  "cache": True,  # Enable caching
  "no_normals": True  # Skip if not needed
}
```

### Large File Strategy

```python
{
  "threads": 8,
  "include": ["IfcWall", "IfcSlab"],  # Process only needed elements
  "include_type": "entities",
  "mesher_linear_deflection": 0.05,  # Reduce detail
  "disable_opening_subtractions": True,
  "no_parallel_mapping": False  # Use parallel processing
}
```

## Monitoring

### Key Metrics to Monitor

1. **Execution Time** - Logged in result
2. **File Sizes** - Input/output size ratio
3. **Return Code** - 0 = success, non-zero = error
4. **Memory Usage** - Check Docker stats
5. **Error Rate** - Track failed jobs

### Setting Up Alerts

Monitor for:
- Jobs exceeding 30 minutes
- Multiple consecutive failures
- Output files significantly larger than expected
- Memory usage above 80%

## Additional Resources

- [IfcConvert Documentation](https://docs.ifcopenshell.org/ifcconvert/usage.html)
- [ARGUMENTS.md](./ARGUMENTS.md) - Parameter reference
- [EXAMPLES.md](./EXAMPLES.md) - Usage examples
- [IfcOpenShell Forum](https://forums.buildingsmart.org/)

## Getting Help

When reporting issues, include:

1. Full error message from logs
2. Job data (without sensitive info)
3. IFC file size and schema version
4. IfcConvert log file contents
5. Docker container logs
6. System resources (CPU, RAM, disk)

Example issue report:
```markdown
### Issue Description
Conversion fails with return code 1

### Error Message
[Paste full error from logs]

### Job Data
{
  "input_filename": "/uploads/model.ifc",
  "output_filename": "/output/model.obj",
  "threads": 8
}

### IFC File Info
- Size: 5.2 MB
- Schema: IFC4
- Tool: Revit 2024

### IfcConvert Log
[Paste relevant log lines]

### Environment
- Docker version: 24.0.5
- Container memory: 4GB
- CPU cores: 4
```


