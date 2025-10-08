#!/bin/bash

# Test script for n8n PostgreSQL integration
# This script verifies that n8n is properly configured to use PostgreSQL

set -e

echo "======================================"
echo "n8n PostgreSQL Integration Test"
echo "======================================"
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print success
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

# Function to print error
print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Function to print warning
print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Check if docker compose is running
echo "1. Checking if services are running..."
if ! docker compose ps | grep -q "n8n"; then
    print_error "n8n service is not running. Start with: docker compose up -d"
    exit 1
fi
print_success "n8n service is running"

if ! docker compose ps | grep -q "postgres"; then
    print_error "PostgreSQL service is not running. Start with: docker compose up -d"
    exit 1
fi
print_success "PostgreSQL service is running"
echo ""

# Check environment variables in n8n container
echo "2. Checking n8n database environment variables..."
DB_TYPE=$(docker compose exec -T n8n env | grep "^DB_TYPE=" | cut -d'=' -f2 | tr -d '\r\n' || echo "")
if [ "$DB_TYPE" = "postgresdb" ]; then
    print_success "DB_TYPE is set to postgresdb"
else
    print_error "DB_TYPE is not set to postgresdb (found: '$DB_TYPE')"
    exit 1
fi

DB_HOST=$(docker compose exec -T n8n env | grep "^DB_POSTGRESDB_HOST=" | cut -d'=' -f2 | tr -d '\r\n' || echo "")
if [ "$DB_HOST" = "postgres" ]; then
    print_success "DB_POSTGRESDB_HOST is set to postgres"
else
    print_error "DB_POSTGRESDB_HOST is not set correctly (found: '$DB_HOST')"
    exit 1
fi

DB_NAME=$(docker compose exec -T n8n env | grep "^DB_POSTGRESDB_DATABASE=" | cut -d'=' -f2 | tr -d '\r\n' || echo "")
if [ -n "$DB_NAME" ]; then
    print_success "DB_POSTGRESDB_DATABASE is set to '$DB_NAME'"
else
    print_error "DB_POSTGRESDB_DATABASE is not set"
    exit 1
fi
echo ""

# Test PostgreSQL connection from n8n container
echo "3. Testing network connectivity from n8n to PostgreSQL..."
if docker compose exec -T n8n sh -c "timeout 5 nc -zv postgres 5432" 2>&1 | grep -q "open"; then
    print_success "n8n can connect to PostgreSQL on port 5432"
else
    print_error "n8n cannot connect to PostgreSQL"
    exit 1
fi
echo ""

# Check if n8n database exists
echo "4. Checking if n8n database exists..."
if docker compose exec -T postgres psql -U ifcpipeline -lqt | grep -qw "$DB_NAME"; then
    print_success "Database '$DB_NAME' exists in PostgreSQL"
else
    print_error "Database '$DB_NAME' does not exist in PostgreSQL"
    echo "   Run: docker compose exec postgres psql -U ifcpipeline -c 'CREATE DATABASE $DB_NAME;'"
    exit 1
fi
echo ""

# Check if n8n has created tables in PostgreSQL
echo "5. Checking if n8n tables exist in PostgreSQL..."
TABLE_COUNT=$(docker compose exec -T postgres psql -U ifcpipeline -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';" | tr -d ' \r\n')

if [ "$TABLE_COUNT" -gt 0 ]; then
    print_success "Found $TABLE_COUNT n8n tables in PostgreSQL"
    echo "   Sample tables:"
    docker compose exec -T postgres psql -U ifcpipeline -d "$DB_NAME" -t -c "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name LIMIT 5;" | sed 's/^/   - /'
else
    print_warning "No tables found in PostgreSQL database '$DB_NAME'"
    echo "   This is normal if n8n hasn't been accessed yet."
    echo "   Tables will be created automatically on first n8n startup."
fi
echo ""

# Check for SQLite database (should NOT exist if using PostgreSQL)
echo "6. Checking for SQLite database (should not exist)..."
if docker compose exec -T n8n test -f /home/node/.n8n/database.sqlite 2>/dev/null; then
    print_error "SQLite database found at /home/node/.n8n/database.sqlite"
    print_error "n8n may not be using PostgreSQL!"
    echo "   This could mean:"
    echo "   - n8n was started before PostgreSQL configuration was added"
    echo "   - Environment variables are not being read correctly"
    echo ""
    echo "   To fix:"
    echo "   1. Stop n8n: docker compose stop n8n"
    echo "   2. Remove SQLite: docker compose exec n8n rm /home/node/.n8n/database.sqlite"
    echo "   3. Restart n8n: docker compose start n8n"
    exit 1
else
    print_success "No SQLite database found - n8n is using PostgreSQL"
fi
echo ""

# Check n8n logs for database connection
echo "7. Checking n8n logs for database-related messages..."
if docker compose logs n8n 2>&1 | tail -100 | grep -qi "database"; then
    print_success "Found database-related log entries"
    echo "   Recent database logs:"
    docker compose logs n8n 2>&1 | tail -100 | grep -i "database\|postgres\|connection" | tail -5 | sed 's/^/   /'
else
    print_warning "No database-related logs found (this may be normal)"
fi
echo ""

# Check n8n web interface accessibility
echo "8. Checking if n8n web interface is accessible..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:5678 | grep -q "200\|302"; then
    print_success "n8n web interface is accessible at http://localhost:5678"
else
    print_warning "n8n web interface may not be ready yet"
    echo "   Wait a moment and try accessing http://localhost:5678 manually"
fi
echo ""

# Final summary
echo "======================================"
echo "Test Summary"
echo "======================================"
print_success "n8n is properly configured to use PostgreSQL!"
echo ""
echo "Database Details:"
echo "  - Type: PostgreSQL"
echo "  - Host: postgres"
echo "  - Database: $DB_NAME"
echo "  - Tables: $TABLE_COUNT"
echo ""
echo "Next Steps:"
echo "  1. Access n8n at: http://localhost:5678"
echo "  2. Complete the n8n setup wizard if this is first start"
echo "  3. Create a test workflow and verify it persists after restart"
echo "  4. Access PostgreSQL at: http://localhost:8081 (PgWeb)"
echo ""
echo "To verify data persistence:"
echo "  docker compose restart n8n"
echo "  # Then check that your workflows still exist"
echo ""
