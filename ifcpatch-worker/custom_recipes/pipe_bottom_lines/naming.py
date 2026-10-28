# -*- coding: utf-8 -*-
"""Line style and DWG layer names. Pure Python, no Revit.

A layer is "VG - <part> - <part> - <size>", for example
"VG - Spillvatten - 110mm" for a pipe and "VG - 611 - Markrör KRAFT - 110mm"
for a conduit coloured by a view filter. The same text is the Revit line
style and the DWG layer, so both exports agree.
"""

try:
    from pipe_bottom_lines.labels import LAYER_PREFIX as PREFIX
except ImportError:
    # The Revit tool's copy of this module has no labels module.
    PREFIX = u"VG"
SEPARATOR = u" - "
MAX_PART = 80

# Revit refuses these in a subcategory name.
STYLE_BAD_CHARS = u'\\/:*?"<>|{};[]`~'
# AutoCAD refuses these in a layer name.
LAYER_BAD_CHARS = u'\\<>/":;?*|,=`'

try:
    text_type = unicode  # noqa: F821 - IronPython 2.7
except NameError:
    text_type = str


def py_text(value):
    if value is None:
        return u""
    if isinstance(value, text_type):
        return value
    try:
        return text_type(value)
    except Exception:
        try:
            return text_type(str(value), "utf-8", "replace")
        except Exception:
            return u""


def _replace_chars(text, bad):
    cleaned = []
    for char in text:
        if char in bad or ord(char) < 32:
            cleaned.append(u" ")
        else:
            cleaned.append(char)
    return u" ".join(u"".join(cleaned).split())


def clean_part(value):
    """One name part, safe for a Revit line style. Empty when nothing is left."""
    collapsed = _replace_chars(py_text(value), STYLE_BAD_CHARS)
    if len(collapsed) > MAX_PART:
        collapsed = collapsed[:MAX_PART].rstrip()
    return collapsed


def size_label(diameter_mm):
    """110.0 -> "110mm", 32.5 -> "32.5mm", None -> ""."""
    if diameter_mm is None:
        return u""
    try:
        value = float(diameter_mm)
    except Exception:
        return u""
    if value <= 0:
        return u""
    rounded = round(value, 1)
    if abs(rounded - round(rounded)) < 0.05:
        return u"%dmm" % int(round(rounded))
    return (u"%.1f" % rounded).rstrip(u"0").rstrip(u".") + u"mm"


def layer_name(parts, diameter_mm=None):
    """Join the non-empty, de-duplicated parts and the size under the VG prefix."""
    names = [PREFIX]
    seen = set()
    for part in parts or ():
        cleaned = clean_part(part)
        if not cleaned or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        names.append(cleaned)
    size = size_label(diameter_mm)
    if size:
        names.append(size)
    return SEPARATOR.join(names)


def dwg_layer_name(name):
    result = _replace_chars(py_text(name), LAYER_BAD_CHARS)
    if not result:
        result = PREFIX
    return result[:250]


def is_vg_style_name(name):
    """True for line styles this tool (or the Dynamo script before it) created."""
    text = py_text(name)
    if not text:
        return False
    if text in (u"Vattengang", u"Vatteng\u00e5ng", PREFIX):
        return True
    for prefix in (u"Vatteng\u00e5ng - ", u"Vattengang - ", PREFIX + SEPARATOR):
        if text.startswith(prefix):
            return True
    return False
