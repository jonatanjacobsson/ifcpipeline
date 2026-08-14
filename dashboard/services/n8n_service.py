import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if settings.N8N_API_KEY:
        h["X-N8N-API-KEY"] = settings.N8N_API_KEY
    return h


def check_health() -> dict:
    try:
        r = httpx.get(f"{settings.N8N_API_URL}/healthz", timeout=_TIMEOUT)
        return {"connected": True, "status_code": r.status_code}
    except Exception as e:
        logger.error(f"n8n health check failed: {e}")
        return {"connected": False, "error": str(e)}


def get_workflows() -> list:
    try:
        r = httpx.get(
            f"{settings.N8N_API_URL}/api/v1/workflows",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        workflows = data.get("data", data) if isinstance(data, dict) else data
        result = []
        for wf in workflows:
            result.append({
                "id": wf.get("id"),
                "name": wf.get("name"),
                "active": wf.get("active", False),
                "createdAt": wf.get("createdAt"),
                "updatedAt": wf.get("updatedAt"),
                "tags": [t.get("name", t) if isinstance(t, dict) else t for t in wf.get("tags", [])],
            })
        return result
    except Exception as e:
        logger.error(f"Failed to fetch n8n workflows: {e}")
        return []


def get_executions_with_data(
    limit: int = 50,
    workflow_id: str | None = None,
    status: str | None = None,
) -> list:
    """Fetch recent executions with full data (for extracting webhook input)."""
    try:
        params = {"limit": limit, "includeData": "true"}
        if workflow_id:
            params["workflowId"] = workflow_id
        if status:
            params["status"] = status
        r = httpx.get(
            f"{settings.N8N_API_URL}/api/v1/executions",
            headers=_headers(),
            params=params,
            timeout=httpx.Timeout(20.0),
        )
        r.raise_for_status()
        data = r.json()
        return data.get("data", data) if isinstance(data, dict) else data
    except Exception as e:
        logger.error(f"Failed to fetch n8n executions with data: {e}")
        return []


def get_executions(limit: int = 20, status: str | None = None) -> list:
    try:
        params = {"limit": limit, "includeData": "false"}
        if status:
            params["status"] = status
        r = httpx.get(
            f"{settings.N8N_API_URL}/api/v1/executions",
            headers=_headers(),
            params=params,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        executions = data.get("data", data) if isinstance(data, dict) else data
        result = []
        for ex in executions:
            result.append({
                "id": ex.get("id"),
                "workflowId": ex.get("workflowId"),
                "workflowName": ex.get("workflowData", {}).get("name") if isinstance(ex.get("workflowData"), dict) else None,
                "status": ex.get("status", ex.get("finished") and "success" or "running"),
                "startedAt": ex.get("startedAt"),
                "stoppedAt": ex.get("stoppedAt"),
                "mode": ex.get("mode"),
            })
        return result
    except Exception as e:
        logger.error(f"Failed to fetch n8n executions: {e}")
        return []


def get_execution(execution_id: str) -> dict | None:
    """Fetch a single execution by ID to retrieve workflowId and status."""
    try:
        r = httpx.get(
            f"{settings.N8N_API_URL}/api/v1/executions/{execution_id}",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return {
            "id": data.get("id"),
            "workflowId": data.get("workflowId"),
            "status": data.get("status"),
            "startedAt": data.get("startedAt"),
        }
    except Exception as e:
        logger.error(f"Failed to fetch execution {execution_id}: {e}")
        return None


def get_execution_with_data(execution_id: str) -> dict | None:
    """Fetch a single execution with runData (timings, node outputs)."""
    try:
        r = httpx.get(
            f"{settings.N8N_API_URL}/api/v1/executions/{execution_id}",
            headers=_headers(),
            params={"includeData": "true"},
            timeout=httpx.Timeout(30.0),
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Failed to fetch execution with data {execution_id}: {e}")
        return None


_EXECUTE_TIMEOUT = httpx.Timeout(30.0)


def trigger_webhook(path: str, data: dict) -> dict:
    """Trigger an n8n workflow via its webhook endpoint."""
    r = httpx.post(
        f"{settings.N8N_API_URL}/webhook/{path}",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        json=data,
        timeout=_EXECUTE_TIMEOUT,
    )
    r.raise_for_status()
    result = r.json() if "json" in r.headers.get("content-type", "") else {}
    execution_id = r.headers.get("x-n8n-execution-id")
    if execution_id and "executionId" not in result:
        result["executionId"] = execution_id
    return result


def build_execution_url(workflow_id: str, execution_id: str) -> str:
    """Build a browser-accessible URL for an n8n execution."""
    base = settings.N8N_EXTERNAL_URL.rstrip("/")
    if not base:
        return ""
    return f"{base}/workflow/{workflow_id}/executions/{execution_id}"
