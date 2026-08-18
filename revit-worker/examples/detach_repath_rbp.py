# -*- coding: utf-8 -*-
"""RBP task script: detach_repath pipeline.

BatchRvt opens the model (--detach). Environment: ANALYTICS_ONLY, RBP_SIDECAR_PATH.
Launcher: Run-RBPDetachRepath.ps1 / Run-RBPModelAudit.ps1.
"""

from __future__ import print_function

import os
import sys

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import BasicFileInfo, ModelPathUtils  # noqa: E402

import revit_script_util
from revit_script_util import Output

try:
    _script_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _script_dir = os.getcwd()
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from detach_repath import (  # noqa: E402
    Logger,
    process_open_document,
    _normalize_model_path_for_revit,
)

log = Logger()
log.info("detach_repath_rbp (module load)")

doc = revit_script_util.GetScriptDocument()
raw_path = revit_script_util.GetRevitFilePath() or ""
model_path_abs = _normalize_model_path_for_revit(raw_path)
log.info("Model path: {}".format(model_path_abs))

analytics_only = os.environ.get("ANALYTICS_ONLY", "").lower() in ("1", "true", "yes")

try:
    fi = BasicFileInfo.Extract(model_path_abs)
    is_workshared_file = fi.IsWorkshared
except Exception as ex:
    log.warning("BasicFileInfo.Extract failed: {}".format(ex))
    is_workshared_file = False

try:
    doc_is_workshared = doc.IsWorkshared
except Exception:
    doc_is_workshared = is_workshared_file

model_path_obj = ModelPathUtils.ConvertUserVisiblePathToModelPath(model_path_abs)

model_name = os.path.splitext(os.path.basename(model_path_abs))[0]
extra_paths = []
_sc = os.environ.get("RBP_SIDECAR_PATH", "").strip()
if _sc:
    extra_paths.append(_sc)
extra_paths.append(
    os.path.join(os.path.dirname(model_path_abs), model_name + ".rbp_rw_result.json")
)

process_open_document(
    doc,
    model_path_abs,
    analytics_only,
    log,
    is_workshared_file,
    doc_is_workshared,
    "rbp",
    model_path_obj,
    extra_rw_result_paths=extra_paths,
)

log.debug("detach_repath_rbp complete")
Output('Log File: "{}"'.format(log.log_file))
