#!/bin/bash
#
# List Available Building Storeys
# Shows storey names that can be used for filtering
#

set -e

IFC_FILE="${1:-/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc}"

echo "========================================="
echo "Available Building Storeys"
echo "========================================="
echo ""
echo "File: $IFC_FILE"
echo ""

# Use grep to find IfcBuildingStorey entries in the IFC file
docker exec ifcpipeline-ifcconvert-worker-1 grep -i "IFCBUILDINGSTOREY" "$IFC_FILE" | \
  grep -oP "(?<=')[^']*(?=')" | \
  sort -u | \
  nl -w2 -s'. '

echo ""
echo "Usage:"
echo "  ./svg-floorplan-complete.sh \"010 Quay Level +1.90m\""
echo "  ./svg-floorplan-complete.sh  (for all storeys)"
echo ""
echo "========================================="

