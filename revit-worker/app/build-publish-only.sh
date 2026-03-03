#!/bin/bash
# Publish only (no tests). Use when building on Windows or when tests are run separately.
set -e
cd "$(dirname "$0")"
echo "=== Publishing RevitWorkerApp (win-x64) ==="
dotnet publish RevitWorkerApp.csproj -r win-x64 -c Release --self-contained false -p:PublishSingleFile=true -o ../
echo "Done. Output: ../RevitWorkerApp.exe"
