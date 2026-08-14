import os


class Settings:
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "postgres")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "ifcpipeline")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "ifcpipeline")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")

    N8N_API_URL: str = os.getenv("N8N_API_URL", "http://n8n:5678")
    N8N_API_KEY: str = os.getenv("N8N_API_KEY", "")
    N8N_EXTERNAL_URL: str = os.getenv("N8N_EXTERNAL_URL", os.getenv("N8N_WEBHOOK_URL", ""))

    # Root the Network Share browser is confined to. Compose mounts the pipeline's own
    # ./shared/uploads and ./shared/output underneath it; point it at a CIFS/NFS mount
    # instead to browse a real file share.
    NETWORK_SHARE_PATH: str = os.getenv("NETWORK_SHARE_PATH", "/share")

    # Where workers drop per-job log files named `<job_id>-*`. Surfaced in job detail.
    WORKER_LOGS_DIR: str = os.getenv("WORKER_LOGS_DIR", "/share/logs")


settings = Settings()
