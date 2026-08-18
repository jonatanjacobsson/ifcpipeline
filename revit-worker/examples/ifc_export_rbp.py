# -*- coding: utf-8 -*-
"""RBP task script: IFC4 Reference View export.

BatchRvt opens the model (--detach). Creates a 3D view if none exists.
Launcher: Run-RBPIfcExport.ps1

Environment:
    RBP_EXPORT_DIR    output directory
    RBP_IFC_FILENAME  stem without .ifc
    RBP_JOB_ID        job id
    RBP_SIDECAR_PATH  optional sidecar JSON path
    RBP_PSET_FILE     optional user-defined property set file
"""

from __future__ import print_function

import json
import os
import time
import traceback

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (  # noqa: E402
    FilteredElementCollector,
    IFCExportOptions,
    Transaction,
    TransactionStatus,
    View3D,
    ViewFamily,
    ViewFamilyType,
)

import revit_script_util
from revit_script_util import Output

doc = revit_script_util.GetScriptDocument()
revitFilePath = revit_script_util.GetRevitFilePath()

export_dir = os.environ.get("RBP_EXPORT_DIR", "")
ifc_filename = os.environ.get("RBP_IFC_FILENAME", "")
job_id = os.environ.get("RBP_JOB_ID", "unknown")
pset_file = os.environ.get("RBP_PSET_FILE", "").strip() or None

LOG_TAG = "[RBP-IFC]"


def log(msg):
    Output("{} {}".format(LOG_TAG, msg))


def _safe_str(v):
    if isinstance(v, dict):
        return dict((_safe_str(k), _safe_str(val)) for k, val in v.items())
    if isinstance(v, (list, tuple)):
        return [_safe_str(i) for i in v]
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v
    try:
        return str(v).encode("ascii", "replace").decode("ascii")
    except Exception:
        return repr(v)


def write_rw_result(model_path, data):
    safe_data = _safe_str(data)
    sidecar_env = os.environ.get("RBP_SIDECAR_PATH", "")
    model_basename = os.path.splitext(os.path.basename(model_path))[0]
    model_dir = os.path.dirname(model_path)
    sidecar_name = model_basename + ".rbp_rw_result.json"
    candidates = [
        sidecar_env if sidecar_env else None,
        os.path.join(model_dir, sidecar_name),
        os.path.join(export_dir, sidecar_name) if export_dir else None,
        os.path.join(os.environ.get("TEMP", r"C:\Temp"), "rbp_ifc_export", sidecar_name),
    ]
    payload = json.dumps(safe_data, ensure_ascii=True)
    print("RW_RESULT:" + payload)
    for rw_file in candidates:
        if not rw_file:
            continue
        try:
            parent = os.path.dirname(rw_file)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            with open(rw_file, "w") as fh:
                fh.write(payload)
            log("RW_RESULT written to {}".format(rw_file))
            return
        except Exception as e:
            log("WARNING: Could not write sidecar {}: {}".format(rw_file, e))


def find_or_create_3d_view(document):
    collector = FilteredElementCollector(document).OfClass(View3D)
    all_views = [v for v in collector if not v.IsTemplate]
    log("Found {} non-template 3D views".format(len(all_views)))
    if not all_views:
        return create_3d_view(document), True
    for v in all_views:
        if v.Name == "{3D}":
            log("Using default '{3D}' view")
            return v, False
    log("Using first 3D view: '{}'".format(all_views[0].Name))
    return all_views[0], False


def create_3d_view(document):
    vft_3d = None
    for vft in FilteredElementCollector(document).OfClass(ViewFamilyType).ToElements():
        if vft.ViewFamily == ViewFamily.ThreeDimensional:
            vft_3d = vft
            break
    if not vft_3d:
        raise RuntimeError("No 3D ViewFamilyType found")
    t = Transaction(document, "Create IFC export 3D view")
    t.Start()
    try:
        new_view = View3D.CreateIsometric(document, vft_3d.Id)
        new_view.Name = "IFC_Export_3D"
        t.Commit()
        log("Created 3D view IFC_Export_3D")
        return new_view
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


def export_ifc(document, view, out_dir, filename):
    options = IFCExportOptions()
    options.FilterViewId = view.Id
    try:
        from Autodesk.Revit.DB import IFCVersion
        options.FileVersion = IFCVersion.IFC4RV
        log("IFC version: IFC4RV")
    except Exception:
        try:
            from Autodesk.Revit.DB import IFCVersion
            options.FileVersion = IFCVersion.IFC4
            log("IFC version: IFC4")
        except Exception as e:
            log("WARNING: Could not set IFC version: {}".format(e))

    options.SpaceBoundaryLevel = 0
    options.ExportBaseQuantities = True
    options.WallAndColumnSplitting = True
    options.AddOption("ExportIFCCommonPropertySets", "true")
    options.AddOption("StoreIFCGUID", "true")
    options.AddOption("ExportVisibleElementsInView", "true")
    options.AddOption("UseActiveViewGeometry", "true")
    options.AddOption("TessellationLevelOfDetail", "0.25")

    if pset_file:
        options.AddOption("ExportUserDefinedPsets", "true")
        options.AddOption("ExportUserDefinedPsetsFileName", pset_file)
        log("Custom PSETs: {}".format(pset_file))
    else:
        options.AddOption("ExportUserDefinedPsets", "false")

    if not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir)
        except Exception as e:
            log("WARNING: makedirs: {}".format(e))

    log("Exporting: {}\\{}.ifc".format(out_dir, filename))
    t0 = time.time()
    try:
        result = document.Export(out_dir, filename, options)
    except Exception as e1:
        if "transaction" not in str(e1).lower():
            raise
        log("Retrying export inside a Transaction")
        txn = Transaction(document, "IFC Export")
        txn.Start()
        try:
            result = document.Export(out_dir, filename, options)
            if txn.GetStatus() == TransactionStatus.Started:
                txn.Commit()
        except Exception:
            if txn.GetStatus() == TransactionStatus.Started:
                txn.RollBack()
            raise
    log("doc.Export() returned {} in {:.1f}s".format(result, time.time() - t0))
    return result


rw = {
    "ifc_exported": False,
    "view_created": False,
    "view_name": "",
    "export_path": "",
    "model_path": revitFilePath,
    "job_id": job_id,
    "pset_file": pset_file or "",
}

try:
    if not export_dir:
        raise RuntimeError("RBP_EXPORT_DIR not set")
    raw_fname = ifc_filename if ifc_filename else os.path.splitext(
        os.path.basename(revitFilePath or "model")
    )[0]
    fname = os.path.basename(raw_fname).replace("_detached", "")
    view_3d, view_created = find_or_create_3d_view(doc)
    rw["view_created"] = view_created
    rw["view_name"] = view_3d.Name
    rw["export_path"] = "{}\\{}.ifc".format(export_dir, fname)
    export_result = export_ifc(doc, view_3d, export_dir, fname)
    rw["ifc_exported"] = bool(export_result)
    if export_result:
        ifc_path = os.path.join(export_dir, fname + ".ifc")
        try:
            if os.path.isfile(ifc_path):
                rw["ifc_size_mb"] = round(os.path.getsize(ifc_path) / 1048576.0, 1)
        except Exception:
            pass
    else:
        rw["error"] = "doc.Export() returned False"
except Exception as exc:
    log(traceback.format_exc())
    rw["error"] = str(exc)
finally:
    write_rw_result(revitFilePath, rw)
    log("=== RBP IFC export complete ===")
