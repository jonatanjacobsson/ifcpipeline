# -*- coding: utf-8 -*-
"""Write pipe-bottom polylines to a DWG as 3D polylines, with ACadSharp through pythonnet.

The pyRevit VG-linjer button writes its DWG with the same library from inside
Revit (dwg_writer.py there), so both files are built the same way.
ACadSharp (MIT, https://github.com/DomCR/ACadSharp) is a .NET 8 library.

Requirements (only PipeBottomLinesDwg needs them, PipeBottomLinesIfc never loads this
module's .NET side). Nothing installs them by itself, and the upstream
ifcpipeline worker image has none of them:

- the .NET 8 runtime (tested 8.0.21), with DOTNET_ROOT pointing at it;
- pythonnet 3.0.x (tested 3.0.5), with PYTHONNET_RUNTIME=coreclr;
- ACadSharp.dll 3.8.0 (lib/net8.0 from the NuGet package), at
  /opt/acadsharp/ACadSharp.dll or wherever ACADSHARP_DLL says.

The image layer that adds them on a Debian-based worker image::

    ARG DOTNET_RUNTIME_VERSION=8.0.21
    ARG ACADSHARP_VERSION=3.8.0
    ARG ACADSHARP_DLL_SHA256=13544512673b3124a8d6452ab02a3a8e11d40da05a1c6a8b586c14da3b322d22
    RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates unzip \\
        && curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \\
        && bash /tmp/dotnet-install.sh --runtime dotnet --version "${DOTNET_RUNTIME_VERSION}" --install-dir /opt/dotnet \\
        && curl -fsSL "https://www.nuget.org/api/v2/package/ACadSharp/${ACADSHARP_VERSION}" -o /tmp/acadsharp.nupkg \\
        && mkdir -p /opt/acadsharp \\
        && unzip -j /tmp/acadsharp.nupkg lib/net8.0/ACadSharp.dll -d /opt/acadsharp \\
        && echo "${ACADSHARP_DLL_SHA256}  /opt/acadsharp/ACadSharp.dll" | sha256sum -c - \\
        && rm -f /tmp/dotnet-install.sh /tmp/acadsharp.nupkg && rm -rf /var/lib/apt/lists/*
    ENV DOTNET_ROOT=/opt/dotnet DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \\
        PYTHONNET_RUNTIME=coreclr ACADSHARP_DLL=/opt/acadsharp/ACadSharp.dll
    RUN pip install --no-cache-dir pythonnet==3.0.5

Without them load() raises DwgUnavailable, naming the missing part.
PipeBottomLinesDwg calls it before it reads any geometry, so the job stops at once.

Two things AutoCAD's AUDIT flags in ACadSharp 3.8.0 output are avoided here:
a vertex whose colour differs from its polyline, and the default "Standard"
table style, which ACadSharp writes malformed. With both handled, AUDIT
reports 0 errors.

ACadSharp leaves the drawing extents at 0,0,0 and opens the drawing on a
10-unit view of the origin. Viewers that trust the header rather than
working the extents out themselves then open on an empty screen, far from
lines in SWEREF coordinates. So the extents, limits and the opening view
(the *Active viewport) are set from the lines.
"""

import os

DLL_ENV = "ACADSHARP_DLL"
DEFAULT_DLL = "/opt/acadsharp/ACadSharp.dll"

_LOADED = []
# The opening view shows the lines' extents with this much room around them.
VIEW_MARGIN = 1.05


class DwgUnavailable(Exception):
    pass


# Ends every DwgUnavailable message: what to install, and the way round it.
HINT = ("PipeBottomLinesDwg needs the .NET 8 runtime, pythonnet 3.0.x and ACadSharp.dll 3.8.0 in the worker "
        "image (see pipe_bottom_lines/dwg.py for the install steps); PipeBottomLinesIfc writes the same lines as IFC without them.")


def dll_path():
    return os.environ.get(DLL_ENV) or DEFAULT_DLL


def load():
    """Start .NET and load ACadSharp, or raise DwgUnavailable saying what is missing."""
    if _LOADED:
        return
    path = dll_path()
    if not os.path.isfile(path):
        raise DwgUnavailable("ACadSharp.dll is not at %s (set %s). %s" % (path, DLL_ENV, HINT))
    try:
        import pythonnet
    except ImportError:
        raise DwgUnavailable("pythonnet is not installed. %s" % HINT)
    try:
        if pythonnet.get_runtime_info() is None:
            pythonnet.load(os.environ.get("PYTHONNET_RUNTIME", "coreclr"))
        import clr
    except Exception as ex:
        raise DwgUnavailable("Could not start the .NET 8 runtime (DOTNET_ROOT=%s): %s. %s"
                             % (os.environ.get("DOTNET_ROOT", "unset"), ex, HINT))
    try:
        clr.AddReference(path)
    except Exception as ex:
        raise DwgUnavailable("Could not load %s: %s. %s" % (path, ex, HINT))
    _LOADED.append(True)


def _color(rgb):
    import System
    from ACadSharp import Color

    return Color(System.Byte(int(rgb[0])), System.Byte(int(rgb[1])), System.Byte(int(rgb[2])))


def write(path, polylines, layer_colors):
    """Write the DWG.

    polylines: [(points, layer name, rgb or None, closed)] with points as
    (x, y, z) in metres. rgb None means the polyline takes its layer colour.
    layer_colors: {layer name: rgb}.
    """
    load()
    import CSMath
    from ACadSharp import ACadVersion, CadDocument
    from ACadSharp.Entities import Polyline3D, Vertex3D
    from ACadSharp.IO import DwgWriter
    from ACadSharp.Tables import Layer
    from ACadSharp.Types.Units import UnitsType

    document = CadDocument(ACadVersion.AC1032)
    document.Header.InsUnits = UnitsType.Meters
    for name in [style.Name for style in document.TableStyles]:
        document.TableStyles.Remove(name)

    layers = {}
    for name in sorted(layer_colors):
        layer = Layer(name)
        layer.Color = _color(layer_colors[name])
        document.Layers.Add(layer)
        layers[name] = layer

    for points, layer_name, rgb, closed in polylines:
        polyline = Polyline3D()
        polyline.Layer = layers[layer_name]
        if rgb is not None:
            polyline.Color = _color(rgb)
        for x, y, z in points:
            vertex = Vertex3D(CSMath.XYZ(x, y, z))
            vertex.Layer = polyline.Layer
            vertex.Color = polyline.Color
            polyline.Vertices.Add(vertex)
        polyline.IsClosed = bool(closed)
        document.Entities.Add(polyline)

    _fit_view(document, [point for points, _layer, _rgb, _closed in polylines for point in points])

    # Write beside the target first, so a failed write never leaves a
    # half-written DWG behind.
    temp = path + ".tmp"
    if os.path.exists(temp):
        os.remove(temp)
    DwgWriter.Write(temp, document, None, None)
    os.replace(temp, path)
    return len(polylines)


def _fit_view(document, points):
    """Set the model space extents and limits, and the opening plan view, to the box around points."""
    if not points:
        return
    import CSMath

    low = [min(p[axis] for p in points) for axis in range(3)]
    high = [max(p[axis] for p in points) for axis in range(3)]
    header = document.Header
    header.ModelSpaceExtMin = CSMath.XYZ(*low)
    header.ModelSpaceExtMax = CSMath.XYZ(*high)
    header.ModelSpaceLimitsMin = CSMath.XY(low[0], low[1])
    header.ModelSpaceLimitsMax = CSMath.XY(high[0], high[1])
    for layout in document.Layouts:
        if layout.Name == "Model":
            layout.MinExtents, layout.MaxExtents = header.ModelSpaceExtMin, header.ModelSpaceExtMax
            layout.MinLimits, layout.MaxLimits = header.ModelSpaceLimitsMin, header.ModelSpaceLimitsMax
    for vport in document.VPorts:
        if vport.Name == "*Active":
            width, height = high[0] - low[0], high[1] - low[1]
            vport.Center = CSMath.XY((low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0)
            vport.ViewHeight = max(height, width / vport.AspectRatio, 1.0) * VIEW_MARGIN
