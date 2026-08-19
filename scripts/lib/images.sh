#!/usr/bin/env bash
# Canonical image names for ifcpipeline services.
#
# Compose tags built images by the `image:` key, so since prebuilt GHCR images
# were introduced the local tag is `ghcr.io/jonatanjacobsson/ifcpipeline-<svc>:latest`,
# NOT the old compose-generated `ifcpipeline-<svc>:latest`. Every script that
# inspects/saves/loads an image must go through image_ref() so the name stays in
# one place and follows IFCPIPELINE_REGISTRY / IFCPIPELINE_TAG.
#
# ifccoord-worker is the exception: it has no `image:` key (private ifc-coord
# submodule, never published), so Compose still names it ifcpipeline-ifccoord-worker.

IFCPIPELINE_REGISTRY="${IFCPIPELINE_REGISTRY:-ghcr.io/jonatanjacobsson}"
IFCPIPELINE_TAG="${IFCPIPELINE_TAG:-latest}"

# image_ref <service>  ->  fully qualified image reference
image_ref() {
  local svc="$1"
  if [[ "$svc" == "ifccoord-worker" ]]; then
    echo "ifcpipeline-ifccoord-worker:${IFCPIPELINE_TAG}"
  else
    echo "${IFCPIPELINE_REGISTRY}/ifcpipeline-${svc}:${IFCPIPELINE_TAG}"
  fi
}
