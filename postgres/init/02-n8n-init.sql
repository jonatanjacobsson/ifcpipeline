-- n8n Database Initialization
-- This script creates a dedicated database for n8n workflow automation

-- Create n8n database
CREATE DATABASE n8n;

-- Grant all privileges to the ifcpipeline user
GRANT ALL PRIVILEGES ON DATABASE n8n TO ifcpipeline;

-- Connect to the n8n database
\c n8n;

-- Create public schema (if not exists) and grant permissions
CREATE SCHEMA IF NOT EXISTS public;
GRANT ALL ON SCHEMA public TO ifcpipeline;

-- Grant privileges on all tables and sequences (for future tables created by n8n)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ifcpipeline;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ifcpipeline;

-- Set default privileges for future objects created by n8n
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ifcpipeline;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ifcpipeline;

-- Add a comment to document the database purpose
COMMENT ON DATABASE n8n IS 'n8n workflow automation database - stores workflows, credentials, executions, and other n8n data';
