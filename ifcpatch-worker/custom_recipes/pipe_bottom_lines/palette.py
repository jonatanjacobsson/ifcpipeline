# -*- coding: utf-8 -*-
"""Fallback colours and the AutoCAD colour index. Pure Python, no Revit."""

# Used when Revit has no colour for a group (a conduit with no colouring
# filter, a pipe system type without a line colour). Same palette as the
# Dynamo script, so unchanged groups keep their old colour.
STYLE_COLORS = (
    (0, 114, 178),
    (213, 94, 0),
    (0, 158, 115),
    (230, 159, 0),
    (204, 121, 167),
    (86, 180, 233),
    (0, 0, 0),
    (140, 86, 75),
)


def color_for_name(name):
    total = 0
    for char in name or u"":
        total += ord(char)
    return STYLE_COLORS[total % len(STYLE_COLORS)]


_ACI = []


def aci_palette():
    """RGB for AutoCAD colour index 0..255 (index 0 is unused, None)."""
    if _ACI:
        return _ACI
    colors = [None] * 256
    colors[1] = (255, 0, 0)
    colors[2] = (255, 255, 0)
    colors[3] = (0, 255, 0)
    colors[4] = (0, 255, 255)
    colors[5] = (0, 0, 255)
    colors[6] = (255, 0, 255)
    colors[7] = (255, 255, 255)
    colors[8] = (128, 128, 128)
    colors[9] = (192, 192, 192)
    colors[250] = (51, 51, 51)
    colors[251] = (91, 91, 91)
    colors[252] = (132, 132, 132)
    colors[253] = (173, 173, 173)
    colors[254] = (214, 214, 214)
    colors[255] = (255, 255, 255)
    hues = (
        (255, 0, 0), (255, 63, 0), (255, 127, 0), (255, 191, 0),
        (255, 255, 0), (191, 255, 0), (127, 255, 0), (63, 255, 0),
        (0, 255, 0), (0, 255, 63), (0, 255, 127), (0, 255, 191),
        (0, 255, 255), (0, 191, 255), (0, 127, 255), (0, 63, 255),
        (0, 0, 255), (63, 0, 255), (127, 0, 255), (191, 0, 255),
        (255, 0, 255), (255, 0, 191), (255, 0, 127), (255, 0, 63),
    )
    shades = (
        (255, False), (255, True), (165, False), (165, True),
        (127, False), (127, True), (76, False), (76, True),
        (38, False), (38, True),
    )
    index = 10
    for hue in hues:
        for level, pastel in shades:
            channels = []
            half = level // 2
            for channel in hue:
                if channel == 0:
                    channels.append(half if pastel else 0)
                else:
                    scaled = (channel * level + 127) // 255
                    if pastel:
                        channels.append(scaled + (level - scaled) // 2)
                    else:
                        channels.append(scaled)
            colors[index] = (channels[0], channels[1], channels[2])
            index += 1
    _ACI.extend(colors)
    return _ACI


def nearest_aci(rgb):
    red, green, blue = rgb
    best = 7
    best_distance = None
    for index, color in enumerate(aci_palette()):
        if color is None:
            continue
        distance = (red - color[0]) ** 2 + (green - color[1]) ** 2 + (blue - color[2]) ** 2
        if best_distance is None or distance < best_distance:
            best = index
            best_distance = distance
    return best


def hex_color(rgb):
    return u"#%02X%02X%02X" % (rgb[0], rgb[1], rgb[2])
