# Duplicate `n8n-nodes-ifcpipeline` install

There are currently **two** installations of the `n8n-nodes-ifcpipeline`
package side by side in the n8n container:

| Source                                                       | Path inside container                                      | Version | n8n type prefix              |
| ------------------------------------------------------------ | ---------------------------------------------------------- | ------- | ---------------------------- |
| Community npm (installed via the n8n UI / community packages) | `/home/node/.n8n/nodes/node_modules/n8n-nodes-ifcpipeline` | `0.7.0` | `n8n-nodes-ifcpipeline.*`    |
| Workspace source (locally linked from `apps/n8n-nodes-ifcpipeline`) | `/home/node/.n8n/custom/n8n-nodes-ifcpipeline`             | `0.7.2` | `CUSTOM.*`                   |

n8n loads both at startup, so two distinct sets of node "types" appear in
every editor and in the workflow JSON. Both forward the same upstream
`ifcpipeline` API. The differences are cosmetic — for example the error
message wording in `pollForJobCompletion`:

* community 0.7.0:
  `Job failed: Work-horse terminated unexpectedly; waitpid returned 139 (signal 11);`
* workspace 0.7.2:
  `Job failed (retryable): Work-horse terminated unexpectedly; waitpid returned 139 (signal 11);`

That is why the Teams Error alerts collected on the night of
2026-05-13 → 2026-05-14 had two different message prefixes for the same
underlying ifcopenshell SIGSEGV.

## Current usage (`scripts/audit-ifcpipeline-node-usage.py`)

```
CUSTOM.*                nodes=92  types=9
n8n-nodes-ifcpipeline.* nodes=88  types=6
```

Per type (community npm side):

```
n8n-nodes-ifcpipeline.ifcPipeline    54 nodes  29 workflows
n8n-nodes-ifcpipeline.ifcPatch       22 nodes   9 workflows
n8n-nodes-ifcpipeline.ifcClash        5 nodes   3 workflows
n8n-nodes-ifcpipeline.ifcDiff         4 nodes   4 workflows
n8n-nodes-ifcpipeline.ifcTester       2 nodes   2 workflows
n8n-nodes-ifcpipeline.ifcCsv          1 nodes   1 workflows
```

Run the audit any time:

```bash
python3 scripts/audit-ifcpipeline-node-usage.py            # human-readable
python3 scripts/audit-ifcpipeline-node-usage.py --json     # machine-readable
```

## Why we did NOT auto-rename

Switching workflows from the npm prefix to the CUSTOM prefix means
rewriting the `type` field on **88 nodes across ~33 workflows**, including
production scheduled flows (Forsmark, LA Orexo *, Nobel *, Frankenstein,
Diff+Patch, …). n8n does not migrate runtime state across prefix renames,
so each rewritten node loses its execution history association and any
`webhookId`-keyed integration loses its endpoint.

That is a deliberate refactor, not something to do as a side effect of an
overnight incident.

## Recommended cleanup (when ready)

1. **Pick one source.** The workspace `CUSTOM.*` install is newer (0.7.2 vs
   0.7.0) and is the one the team actively edits, so it is the natural
   keeper. The community 0.7.0 install can be retired.
2. **Schedule a freeze window** — even a short one — because step 4 will
   restart n8n and any in-flight executions will end as `crashed`.
3. **Bulk-rewrite workflow JSON.** For every node in
   `scripts/audit-ifcpipeline-node-usage.py --json` whose type starts with
   `n8n-nodes-ifcpipeline.`, change it to the matching `CUSTOM.` form
   (`ifcPipeline → ifcPipeline`, `ifcPatch → ifcPatch`, etc.). Re-save via
   the n8n public API (`PUT /workflows/{id}` with the same shape used by
   `/tmp/patch-workflows.py`).
4. **Uninstall the community package** from the n8n UI (Settings →
   Community Nodes → Remove) **or** delete
   `/home/node/.n8n/nodes/node_modules/n8n-nodes-ifcpipeline` and restart
   the n8n container. After restart, only the `CUSTOM.*` types should be
   loaded.
5. **Bump the workspace package version** from 0.7.2 → 0.7.3 and re-link
   so that future Teams Error alerts have a single, consistent error
   message format.

Until then, the duplication is harmless to runtime behaviour (both
implementations correctly forward upstream errors); only the alert
wording is inconsistent.
