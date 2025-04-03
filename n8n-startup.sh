#!/bin/bash
set -e

# Create the n8n nodes directory if it doesn't exist
mkdir -p /home/node/.n8n/nodes

# Go to the nodes directory
cd /home/node/.n8n/nodes

# Install the community node package
echo "Installing n8n-nodes-ifcpipeline..."
npm install n8n-nodes-ifcpipeline

# Set correct ownership
chown -R node:node /home/node/.n8n

# Start n8n
echo "Starting n8n..."
exec n8n start 