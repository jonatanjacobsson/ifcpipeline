FROM docker.n8n.io/n8nio/n8n

USER root

# Create the .n8n/nodes directory and install community nodes
RUN mkdir -p /home/node/.n8n/nodes \
    && cd /home/node/.n8n/nodes \
    && npm i n8n-nodes-ifcpipeline

# Set permissions
RUN chown -R node:node /home/node/.n8n

USER node 