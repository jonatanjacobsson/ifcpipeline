"""Idempotent runtime patches for provably-pure hot functions.

topologicpy's TGraph colors every vertex/edge during ``ByIFCFile``,
``BetweennessCentrality``, ``CommunityPartition``, etc. via
``Color.ByValueInRange``, which instantiates a fresh plotly
``ColorscaleValidator`` per call. Each validator recomputes its
``named_colorscales`` table with ``inspect.getmembers`` over three plotly
color modules (~0.35 ms/call, thousands of calls per graph build — the
dominant cost of small-file graph builds, see ingest_bench profiles).

Both patched functions are pure:
  * ``ColorscaleValidator.named_colorscales`` derives only from static
    plotly module data (name -> color list); callers never mutate the
    returned dict (``validate_coerce`` only rebinds/copies).
  * ``Color.ByValueInRange`` is a deterministic function of its arguments;
    the cache stores tuples and returns a fresh list per call so callers
    can never mutate shared state.

``apply()`` is idempotent and safe to call from multiple modules.
"""

from __future__ import annotations

import functools

_applied = False


def apply() -> None:
    global _applied
    if _applied:
        return
    _applied = True
    _patch_plotly_named_colorscales()
    _patch_color_by_value_in_range()


def _patch_plotly_named_colorscales() -> None:
    """Share one computed named-colorscales table across all validator instances."""
    try:
        from _plotly_utils import basevalidators as _bv
    except Exception:
        return
    cls = _bv.ColorscaleValidator
    if getattr(cls, "_ingest_named_colorscales_patched", False):
        return
    orig_fget = cls.named_colorscales.fget
    shared: dict = {}

    def named_colorscales(self):
        if not shared:
            shared.update(orig_fget(self))
        return shared

    cls.named_colorscales = property(named_colorscales)
    cls._ingest_named_colorscales_patched = True


def _patch_color_by_value_in_range() -> None:
    """Memoize Color.ByValueInRange; identical (value, range, scale) calls repeat
    heavily (community ids, normalized centralities rounded to 6 decimals)."""
    try:
        from topologicpy.Color import Color
    except Exception:
        return
    orig = Color.ByValueInRange
    if getattr(orig, "_ingest_lru_patched", False):
        return

    @functools.lru_cache(maxsize=65536)
    def _cached(value, minValue, maxValue, alpha, colorScale):
        result = orig(value=value, minValue=minValue, maxValue=maxValue,
                      alpha=alpha, colorScale=colorScale)
        return tuple(result) if isinstance(result, list) else result

    @functools.wraps(orig)
    def by_value_in_range(value=0.5, minValue=0.0, maxValue=1.0, alpha=None,
                          colorScale="viridis"):
        try:
            result = _cached(value, minValue, maxValue, alpha, colorScale)
        except TypeError:  # unhashable arg (e.g. explicit colorscale list)
            return orig(value=value, minValue=minValue, maxValue=maxValue,
                        alpha=alpha, colorScale=colorScale)
        return list(result) if isinstance(result, tuple) else result

    by_value_in_range._ingest_lru_patched = True
    Color.ByValueInRange = staticmethod(by_value_in_range)
