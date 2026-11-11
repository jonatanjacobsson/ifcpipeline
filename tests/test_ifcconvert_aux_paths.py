"""IfcConvert auxiliary file paths must stay inside the shared volumes."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("ifcconvert_tasks", ROOT / "ifcconvert-worker" / "tasks.py")
tasks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tasks)


@pytest.mark.parametrize("path", ["/uploads/filter.txt", "/output/converted/cache.h5", "/uploads/../uploads/m.mtl"])
def test_paths_inside_shared_volumes_are_accepted(path):
    assert tasks._confine_aux_path(path, "filter_file") == str(Path(path).resolve())


@pytest.mark.parametrize("path", ["/etc/passwd", "/uploads/../etc/passwd", "/uploads-other/x", "/app/tasks.py"])
def test_paths_outside_shared_volumes_are_rejected(path):
    with pytest.raises(ValueError, match="filter_file"):
        tasks._confine_aux_path(path, "filter_file")


def test_s3_tmpdir_is_allowed_when_passed(tmp_path):
    target = tmp_path / "cache.h5"
    assert tasks._confine_aux_path(str(target), "cache_file", (str(tmp_path),)) == str(target.resolve())
    with pytest.raises(ValueError):
        tasks._confine_aux_path(str(target), "cache_file")


def test_cache_file_may_not_overwrite_uploads():
    with pytest.raises(ValueError, match="cache_file"):
        tasks._confine_aux_path("/uploads/model.ifc", "cache_file", (), tasks.AUX_WRITE_ROOTS)
    assert tasks._confine_aux_path("/output/c.h5", "cache_file", (), tasks.AUX_WRITE_ROOTS) == "/output/c.h5"
