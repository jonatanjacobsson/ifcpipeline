# Remote workers

Remote worker deployment is documented in **[DEPLOYMENT.md](DEPLOYMENT.md)** (recipes, data preservation, LAN setup, troubleshooting).

Quick links:

- Worker host start: `./scripts/start-remote-workers.sh` (requires `.env.remote`)
- Primary deploy: `./scripts/deploy-remote-workers-from-primary.sh` (requires `.env`)
- Full setup: `./scripts/setup-remote-workers.sh`

Set `REMOTE_SSH`, `PIPELINE_HOST`, and `PIPELINE_LAN_IP` in your environment or `.env` — do not rely on committed hostnames.
