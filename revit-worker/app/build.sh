#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "=== Running unit tests ==="
cd ../tests
dotnet test RevitWorkerApp.Tests.csproj -c Release --logger "console;verbosity=normal"
cd ../app

echo "=== Publishing ==="
dotnet publish RevitWorkerApp.csproj -r win-x64 -c Release --self-contained false -p:PublishSingleFile=true -o ../
