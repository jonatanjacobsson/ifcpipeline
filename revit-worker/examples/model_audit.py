# -*- coding: utf-8 -*-
"""Collect a compact model audit while a Revit document is open (RBP IronPython 2.7).

Used by detach_repath.py. Returns a dict merged into RW_RESULT.
"""

from __future__ import print_function

import os

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (  # noqa: E402
    BasicFileInfo,
    CategoryType,
    FilteredElementCollector,
    Level,
    RevitLinkType,
    View,
    ViewSheet,
)


def _safe_str(x):
    if x is None:
        return ""
    try:
        return str(x).encode("ascii", "replace").decode("ascii")
    except Exception:
        try:
            return unicode(x).encode("ascii", "replace").decode("ascii")  # noqa: F821
        except Exception:
            return ""


def collect_analytics(doc, model_path_abs):
    """Return a JSON-serializable audit dict for RW_RESULT."""
    out = {
        "warnings_total": 0,
        "warnings_by_description": {},
        "views_total": 0,
        "views_by_type": {},
        "sheets_total": 0,
        "linked_models": [],
        "levels": [],
        "file_size_mb": None,
        "revit_format": None,
        "workshared": None,
    }

    try:
        if os.path.isfile(model_path_abs):
            out["file_size_mb"] = round(
                float(os.path.getsize(model_path_abs)) / (1024.0 * 1024.0), 3
            )
            fi = BasicFileInfo.Extract(model_path_abs)
            out["revit_format"] = _safe_str(fi.Format)
            out["workshared"] = bool(fi.IsWorkshared)
    except Exception:
        pass

    try:
        warns = doc.GetWarnings()
        out["warnings_total"] = len(warns)
        by_desc = {}
        for w in warns:
            try:
                desc = _safe_str(w.GetDescriptionText())
            except Exception:
                desc = "unknown"
            by_desc[desc] = by_desc.get(desc, 0) + 1
        out["warnings_by_description"] = by_desc
    except Exception:
        pass

    try:
        all_views = list(
            FilteredElementCollector(doc)
            .OfClass(View)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        views_by_type = {}
        n = 0
        for v in all_views:
            if getattr(v, "IsTemplate", False):
                continue
            vt = _safe_str(v.ViewType)
            views_by_type[vt] = views_by_type.get(vt, 0) + 1
            n += 1
        out["views_total"] = n
        out["views_by_type"] = views_by_type
    except Exception:
        pass

    try:
        sheets = list(
            FilteredElementCollector(doc)
            .OfClass(ViewSheet)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        out["sheets_total"] = len(sheets)
    except Exception:
        pass

    try:
        links = []
        for lt in FilteredElementCollector(doc).OfClass(RevitLinkType).ToElements():
            links.append({"name": _safe_str(lt.Name), "loaded": bool(lt.IsLoaded)})
        out["linked_models"] = links
    except Exception:
        pass

    try:
        levels = []
        for lv in FilteredElementCollector(doc).OfClass(Level).ToElements():
            row = {"name": _safe_str(lv.Name)}
            try:
                row["elevation_ft"] = float(lv.Elevation)
            except Exception:
                pass
            levels.append(row)
        out["levels"] = levels
    except Exception:
        pass

    try:
        cats = 0
        for cat in doc.Settings.Categories:
            try:
                if cat.CategoryType == CategoryType.Model:
                    cats += 1
            except Exception:
                pass
        out["model_categories"] = cats
    except Exception:
        pass

    return out
