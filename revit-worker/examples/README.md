# Revit worker example scripts

Generic **Revit Batch Processor (RBP)** jobs for `POST /revit/execute` with
`command_type: powershell`. Copy this `examples/` folder onto the Windows
machine next to `RevitWorkerApp.exe` (or keep the paths in `script_path`).

Requires [Revit Batch Processor](https://github.com/nmclean/RevitBatchProcessor)
(`%LOCALAPPDATA%\RevitBatchProcessor\BatchRvt.exe`) and a matching Revit year.

IronPython task scripts are 2.7-compatible (no f-strings). Results go to stdout
as `RW_RESULT:{json}` and to a `.rbp_rw_result.json` sidecar (used if the
worker is redeployed mid-job).

| Script | What it does |
|--------|----------------|
| `Run-RBPDetachRepath.ps1` | Detach the central, collect model audit, save `{stem}_detached.rvt` |
| `Run-RBPModelAudit.ps1` | Same open path, audit only (no SaveAs) |
| `Run-RBPIfcExport.ps1` | Find or create a 3D view, export IFC4 Reference View |

The worker forwards `-ModelPath`, `-RevitVersion`, and `-JobId`. Extra flags go
in `arguments`.

## Detach + audit + save

```json
{
  "command_type": "powershell",
  "script_path": "C:\\revit-worker\\examples\\Run-RBPDetachRepath.ps1",
  "model_path": "C:\\Models\\project.rvt",
  "revit_version": "2025",
  "timeout_seconds": 3600
}
```

## Model audit only

```json
{
  "command_type": "powershell",
  "script_path": "C:\\revit-worker\\examples\\Run-RBPModelAudit.ps1",
  "model_path": "C:\\Models\\project.rvt",
  "revit_version": "2025",
  "timeout_seconds": 1800
}
```

`Run-RBPModelAudit.ps1` is a thin wrapper that runs detach/repath with
`-AnalyticsOnly`.

## IFC export

```json
{
  "command_type": "powershell",
  "script_path": "C:\\revit-worker\\examples\\Run-RBPIfcExport.ps1",
  "model_path": "C:\\Models\\project.rvt",
  "revit_version": "2025",
  "timeout_seconds": 3600,
  "arguments": ["-ExportDir", "C:\\Output\\ifc"]
}
```

Optional arguments: `-Filename` (stem without `.ifc`), or set env
`RBP_PSET_FILE` to a user-defined property-set definition file.

## Files

| File | Role |
|------|------|
| `Run-RBPDetachRepath.ps1` | Launches BatchRvt `--detach` with `detach_repath_rbp.py` |
| `Run-RBPModelAudit.ps1` | Calls the detach launcher with `-AnalyticsOnly` |
| `Run-RBPIfcExport.ps1` | Launches BatchRvt `--detach` with `ifc_export_rbp.py` |
| `detach_repath_rbp.py` | RBP entry: active document → `process_open_document` |
| `detach_repath.py` | Audit + optional SaveAs as a new central |
| `model_audit.py` | Warning / view / sheet / link / level counts |
| `ifc_export_rbp.py` | 3D view + `doc.Export` IFC4 RV |
| `RevitBackupCleanup.ps1` | Deletes `*.0001.rvt` incrementals after BatchRvt |
