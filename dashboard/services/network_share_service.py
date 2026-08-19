import logging
import os
import time

from config import settings

logger = logging.getLogger(__name__)

EDITABLE_EXTENSIONS = {".txt", ".xml", ".ids", ".cfg", ".ini", ".csv", ".json", ".md", ".rbxml"}
IFC_EXTENSIONS = {".ifc"}
PDF_EXTENSIONS = {".pdf"}
DOWNLOADABLE_EXTENSIONS = IFC_EXTENSIONS | PDF_EXTENSIONS
MAX_IFC_SIZE = 200 * 1024 * 1024
MAX_PDF_SIZE = 50 * 1024 * 1024


def _is_editable(ext: str) -> bool:
    return ext in EDITABLE_EXTENSIONS or ext.endswith("xml")


def _is_ifc(ext: str) -> bool:
    return ext in IFC_EXTENSIONS


def _is_pdf(ext: str) -> bool:
    return ext in PDF_EXTENSIONS


def check_mount() -> dict:
    mount_path = settings.NETWORK_SHARE_PATH
    try:
        exists = os.path.isdir(mount_path)
        if exists:
            entries = os.listdir(mount_path)
            return {"mounted": True, "path": mount_path, "entry_count": len(entries)}
        return {"mounted": False, "path": mount_path}
    except PermissionError:
        return {"mounted": True, "path": mount_path, "error": "permission_denied"}
    except Exception as e:
        return {"mounted": False, "path": mount_path, "error": str(e)}


def _safe_path(subpath: str) -> str | None:
    mount = settings.NETWORK_SHARE_PATH
    target = os.path.normpath(os.path.join(mount, subpath))
    if not target.startswith(mount):
        return None
    return target


def browse_files(subpath: str = "") -> list:
    target = _safe_path(subpath)
    if not target or not os.path.isdir(target):
        return []

    mount = settings.NETWORK_SHARE_PATH
    entries = []
    try:
        for name in sorted(os.listdir(target)):
            if name.startswith('.'):
                continue
            full = os.path.join(target, name)
            rel = os.path.relpath(full, mount)
            is_dir = os.path.isdir(full)
            _, ext = os.path.splitext(name.lower())
            entry = {
                "name": name,
                "path": rel,
                "is_dir": is_dir,
                "editable": not is_dir and _is_editable(ext),
                "ifc_viewable": not is_dir and _is_ifc(ext),
                "pdf_viewable": not is_dir and _is_pdf(ext),
            }
            if not is_dir:
                try:
                    stat = os.stat(full)
                    entry["size"] = stat.st_size
                    entry["modified"] = stat.st_mtime
                except OSError:
                    pass
            entries.append(entry)
    except PermissionError:
        pass
    return entries


def browse_and_open_from_rel(rel: str) -> tuple[str, str | None]:
    """Map a relative mount path to ``(browse_directory_rel, open_file_rel_or_none)``."""
    rel = (rel or "").strip().replace("\\", "/").strip("/")
    if not rel:
        return "", None
    target = _safe_path(rel)
    if not target:
        return "", None
    mount = settings.NETWORK_SHARE_PATH
    if os.path.isdir(target):
        return rel.rstrip("/"), None
    if os.path.isfile(target):
        parent_abs = os.path.dirname(target)
        browse = os.path.relpath(parent_abs, mount)
        if browse == ".":
            browse = ""
        browse = browse.replace("\\", "/")
        return browse, rel.replace("\\", "/")
    return "", None


def resolve_network_share_paths(path_q: str = "", open_q: str | None = None) -> tuple[str, str | None]:
    """
    URL query → ``(browse_path, optional file to open after listing)``.
    If ``open_q`` is set and points to a file under the mount, browse is its parent.
    Otherwise ``path_q`` is interpreted via :func:`browse_and_open_from_rel`.
    """
    path_q = (path_q or "").strip().replace("\\", "/")
    open_q = (open_q or "").strip().replace("\\", "/") if open_q else ""
    mount = settings.NETWORK_SHARE_PATH
    if open_q:
        target = _safe_path(open_q)
        if target and os.path.isfile(target):
            parent_abs = os.path.dirname(target)
            browse = os.path.relpath(parent_abs, mount).replace("\\", "/")
            if browse == ".":
                browse = ""
            return browse, open_q.replace("\\", "/")
    return browse_and_open_from_rel(path_q)


def read_file(subpath: str) -> dict:
    target = _safe_path(subpath)
    if not target:
        return {"error": "invalid_path"}
    if not os.path.isfile(target):
        return {"error": "not_found"}

    _, ext = os.path.splitext(target.lower())
    if not _is_editable(ext):
        return {"error": "not_editable", "detail": f"Extension {ext} is not viewable"}

    try:
        stat = os.stat(target)
        if stat.st_size > 5 * 1024 * 1024:
            return {"error": "too_large", "detail": "File exceeds 5 MB limit"}
    except OSError:
        pass

    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {
            "path": subpath,
            "name": os.path.basename(target),
            "content": content,
            "size": len(content),
            "editable": True,
        }
    except Exception as e:
        logger.error(f"Failed to read file {subpath}: {e}")
        return {"error": "read_failed", "detail": str(e)}


def get_ifc_path(subpath: str) -> dict:
    target = _safe_path(subpath)
    if not target:
        return {"error": "invalid_path"}
    if not os.path.isfile(target):
        return {"error": "not_found"}

    _, ext = os.path.splitext(target.lower())
    if not _is_ifc(ext):
        return {"error": "not_ifc", "detail": f"Extension {ext} is not an IFC file"}

    try:
        stat = os.stat(target)
        if stat.st_size > MAX_IFC_SIZE:
            return {"error": "too_large", "detail": "IFC file exceeds 200 MB limit"}
    except OSError:
        pass

    return {"path": target, "name": os.path.basename(target)}


def get_download_path(subpath: str) -> dict:
    target = _safe_path(subpath)
    if not target:
        return {"error": "invalid_path"}
    if not os.path.isfile(target):
        return {"error": "not_found"}

    _, ext = os.path.splitext(target.lower())
    if ext not in DOWNLOADABLE_EXTENSIONS:
        return {"error": "not_downloadable", "detail": f"Extension {ext} is not downloadable"}

    size_limit = MAX_PDF_SIZE if _is_pdf(ext) else MAX_IFC_SIZE
    label = "PDF" if _is_pdf(ext) else "IFC"
    try:
        stat = os.stat(target)
        if stat.st_size > size_limit:
            return {"error": "too_large", "detail": f"{label} file exceeds {size_limit // (1024*1024)} MB limit"}
    except OSError:
        pass

    media_type = "application/pdf" if _is_pdf(ext) else "application/octet-stream"
    return {"path": target, "name": os.path.basename(target), "media_type": media_type}


def write_file(subpath: str, content: str) -> dict:
    target = _safe_path(subpath)
    if not target:
        return {"error": "invalid_path"}
    if not os.path.isfile(target):
        return {"error": "not_found"}

    _, ext = os.path.splitext(target.lower())
    if not _is_editable(ext):
        return {"error": "not_editable", "detail": f"Extension {ext} is not writable"}

    last_err = None
    for attempt in range(3):
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return {"ok": True, "path": subpath, "size": len(content)}
        except PermissionError:
            return {"error": "permission_denied"}
        except OSError as e:
            last_err = e
            if e.errno == 16 and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            break

    logger.error(f"Failed to write file {subpath}: {last_err}")
    return {"error": "write_failed", "detail": str(last_err)}
