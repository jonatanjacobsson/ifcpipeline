# -*- coding: utf-8 -*-
"""Every name the output shows a person, in one place.

Swedish by default: "VG" is vattengång, the Swedish drawing term for a
pipe's bottom level, and these are the names Swedish drawings and model
viewers use for it. Change them here to put the layers, annotation types,
property sets and marker names in another language or house style. They
are only names: geometry and GlobalIds do not depend on them, except that a
renamed run property set (RUN_PSET) gets GlobalIds of its own.
"""

# Layers (DWG layers and IFC presentation layers, and each run's Name):
# "<LAYER_PREFIX> - <group> - <colour rule> - <size>mm", "VG - Spillvatten - 110mm".
LAYER_PREFIX = u"VG"
# A connected line nothing names gets a group of its own: "Ledning 1", "Ledning 2", ...
LINE_LABEL = u"Ledning %d"

# The two kinds of annotation: their ObjectType and the IfcTypeProduct of that name.
LINE_TYPE = u"VG Linjer"
POINT_TYPE = u"VG Markör"
# A marker's Name: its level after this, "VG +12.425".
MARKER_NAME = u"VG %s"

# The runs' property set when the recipe names none, and its labels.
RUN_PSET = u"VG"
RUN_LEVEL_START = u"VG level start"
RUN_LEVEL_END = u"VG level end"
RUN_DIAMETER = u"Diameter"
# The diameter's label in a prefixed convention ("Name:Prefix"), after the prefix: "CC_diameter".
RUN_DIAMETER_PREFIXED = u"diameter"

# The markers' level, the way the pipes carry theirs: the property set, the
# level as a label ("+12.75") and as a number in the file's unit (what
# StreamBIM object tags read).
LEVEL_PSET = u"MÅTT"
LEVEL_LABEL = u"+VG"
LEVEL_NUMBER = u"Höjd UK Plushöjd"

# Style names (surface styles: "<name> RRGGBB"), and the site and project.
LINE_STYLE = u"VG-linje"
MARKER_STYLE = u"VG-punkt"
TEXT_STYLE = u"VG-text"
SITE_NAME = u"VG-linjer"
PROJECT_DESCRIPTION = u"VG-linjer"
# The project's Name when the source model has none.
PROJECT_NAME = u"VG"
