"""Parse per-parameter descriptions from a docstring (Google "Args:" or ":param x:" style)."""
import re
from typing import Dict

_ARGS_HEADERS = ('Args:', 'Arguments:', 'Parameters:')
_PARAM_RE = re.compile(r'^:param\s+(\w+):\s*(.*)$')
_GOOGLE_RE = re.compile(r'^(\w+)(?:\s*\([^)]*\))?:\s*(.*)$')


def parse_docstring_params(docstring: str) -> Dict[str, str]:
    params: Dict[str, str] = {}
    current = None
    current_indent = 0
    in_args = False
    for raw in (docstring or '').split('\n'):
        line = raw.strip()
        if line in _ARGS_HEADERS:
            in_args = True
            current = None
            continue
        if not line:
            current = None
            continue
        m = _PARAM_RE.match(line)
        if m:
            current, params[m.group(1)] = m.group(1), m.group(2).strip()
            current_indent = -1
            continue
        if in_args:
            indent = len(raw) - len(raw.lstrip())
            m = _GOOGLE_RE.match(line)
            if m and (current is None or indent <= current_indent):
                current, current_indent = m.group(1), indent
                params[current] = m.group(2).strip()
                continue
        if current and not line.startswith(':'):
            params[current] += ' ' + line
    return params
