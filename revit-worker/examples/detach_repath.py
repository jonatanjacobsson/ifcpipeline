# -*- coding: utf-8 -*-
"""Detach / audit / SaveAs pipeline used by detach_repath_rbp.py (IronPython 2.7).

RBP opens the document with --detach. This module collects model_audit stats and,
unless ANALYTICS_ONLY is set, saves a new central as {stem}_detached.rvt.
"""

from __future__ import print_function

import json
import os
import traceback

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (  # noqa: E402
    ModelPathUtils,
    SaveAsOptions,
    WorksharingSaveAsOptions,
)

from model_audit import collect_analytics  # noqa: E402


class Logger(object):
    def __init__(self):
        self.lines = []
        self.log_file = ""

    def _emit(self, level, msg):
        line = "[{}] {}".format(level, msg)
        self.lines.append(line)
        print(line)

    def info(self, msg):
        self._emit("INFO", msg)

    def warning(self, msg):
        self._emit("WARN", msg)

    def error(self, msg):
        self._emit("ERROR", msg)

    def debug(self, msg):
        self._emit("DEBUG", msg)


def _normalize_model_path_for_revit(path):
    if not path:
        return ""
    return os.path.abspath(path.replace("/", "\\"))


def _write_rw_result(data, extra_paths):
    payload = json.dumps(data, ensure_ascii=True)
    print("RW_RESULT:" + payload)
    for path in extra_paths or []:
        if not path:
            continue
        try:
            parent = os.path.dirname(path)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            with open(path, "w") as fh:
                fh.write(payload)
        except Exception:
            pass


def process_open_document(
    doc,
    model_path_abs,
    analytics_only,
    log,
    is_workshared_file,
    doc_is_workshared,
    open_mode,
    model_path_obj,
    extra_rw_result_paths=None,
    audit_on_open=False,
):
    """Run audit, optionally SaveAs a detached central, write sidecar(s)."""
    rw = {
        "success": True,
        "host": "rbp",
        "open_mode": open_mode,
        "analytics_only": bool(analytics_only),
        "audit_on_open": bool(audit_on_open),
        "model_path": model_path_abs,
        "workshared_file": bool(is_workshared_file),
        "doc_workshared": bool(doc_is_workshared),
        "saved_path": None,
    }

    extra_paths = list(extra_rw_result_paths or [])
    try:
        log.info("Collecting model audit")
        rw.update(collect_analytics(doc, model_path_abs) or {})

        if analytics_only:
            log.info("Analytics-only: skipping SaveAs")
            _write_rw_result(rw, extra_paths)
            return True

        stem = os.path.splitext(os.path.basename(model_path_abs))[0]
        if stem.endswith("_detached"):
            out_name = stem + ".rvt"
        else:
            out_name = stem + "_detached.rvt"
        out_path = os.path.join(os.path.dirname(model_path_abs), out_name)
        rw["saved_path"] = out_path

        save_options = SaveAsOptions()
        save_options.OverwriteExistingFile = True
        if doc_is_workshared:
            ws = WorksharingSaveAsOptions()
            ws.SaveAsCentral = True
            save_options.SetWorksharingOptions(ws)

        dest = ModelPathUtils.ConvertUserVisiblePathToModelPath(out_path)
        log.info("SaveAs {}".format(out_path))
        doc.SaveAs(dest, save_options)
        rw["saved"] = True
        _write_rw_result(rw, extra_paths)
        return True
    except Exception as ex:
        log.error(traceback.format_exc())
        rw["success"] = False
        rw["error"] = str(ex)
        _write_rw_result(rw, extra_paths)
        return False
