FROM node:20-alpine

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Accept build arguments for Vite environment variables
ARG VITE_API_BASE
ENV VITE_API_BASE=$VITE_API_BASE

# Build the application
RUN npm run build

# Copy the WebWorker file to the dist directory so it can be served
RUN mkdir -p dist/node_modules/@thatopen/fragments/dist/Worker && \
    cp node_modules/@thatopen/fragments/dist/Worker/worker.mjs dist/node_modules/@thatopen/fragments/dist/Worker/

# Install a simple HTTP server
RUN npm install -g serve

# Expose port
EXPOSE 3000

# Start the server serving the dist directory (with SPA mode for routing)
CMD ["serve", "-s", "dist", "-l", "3000"]
