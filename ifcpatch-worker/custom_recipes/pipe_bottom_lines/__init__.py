"""Bottom-of-pipe lines (Swedish vattengång, VG) from the pipes and conduits
in an IFC, written as a 3D-polyline DWG (PipeBottomLinesDwg) or an IFC of
annotations with level markers (PipeBottomLinesIfc). labels.py holds every
name the output shows.

chain.py, naming.py and palette.py are copies of the modules with the same
names in the pyRevit VG-linjer tool (lib/vg_lines), which draws the same
lines from inside Revit. Keep them in step, so the DWG from the IFC and the
DWG from Revit agree.
"""
