from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from services import network_share_service, cache

router = APIRouter(prefix="/api/network-share", tags=["Network Share"])


class FileWriteRequest(BaseModel):
    content: str


@router.get("/status")
def status():
    return network_share_service.check_mount()


@router.get("/files")
def browse(path: str = Query("")):
    return cache.get_or_set(f"ns_browse_{path}", 30, network_share_service.browse_files, path)


@router.get("/file")
def read_file(path: str = Query(...)):
    result = network_share_service.read_file(path)
    if "error" in result:
        from fastapi import HTTPException
        codes = {"invalid_path": 400, "not_found": 404, "not_editable": 415, "too_large": 413, "read_failed": 500}
        raise HTTPException(status_code=codes.get(result["error"], 500), detail=result.get("detail", result["error"]))
    return result


@router.get("/download")
def download_file(path: str = Query(...), inline: bool = Query(False)):
    result = network_share_service.get_download_path(path)
    if "error" in result:
        from fastapi import HTTPException
        codes = {"invalid_path": 400, "not_found": 404, "not_ifc": 415, "not_downloadable": 415, "too_large": 413}
        raise HTTPException(status_code=codes.get(result["error"], 500), detail=result.get("detail", result["error"]))
    disposition = "inline" if inline else "attachment"
    return FileResponse(result["path"], filename=result["name"], media_type=result["media_type"],
                        content_disposition_type=disposition)


@router.put("/file")
def write_file(path: str = Query(...), body: FileWriteRequest = ...):
    result = network_share_service.write_file(path, body.content)
    if "error" in result:
        from fastapi import HTTPException
        codes = {"invalid_path": 400, "not_found": 404, "not_editable": 415, "permission_denied": 403, "write_failed": 500}
        raise HTTPException(status_code=codes.get(result["error"], 500), detail=result.get("detail", result["error"]))
    return result
