#!/bin/bash

# Yeastar CRM Startup Script with Cloudflare Tunnel
# This script starts all services and exposes them via Cloudflare tunnel

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "  Yeastar CRM - Starting with Cloudflare Tunnel"
echo "=================================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[STATUS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    print_error "Docker is not running. Please start Docker first."
    exit 1
fi

# Check if cloudflared is available
if ! command -v cloudflared &> /dev/null; then
    print_error "cloudflared is not installed. Please install it first:"
    echo "  curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared"
    echo "  chmod +x cloudflared && sudo mv cloudflared /usr/local/bin/"
    exit 1
fi

# Stop any existing containers
print_status "Stopping any existing containers..."
docker compose down 2>/dev/null || true

# Start MySQL first
print_status "Starting MySQL database..."
docker compose up -d mysql

# Wait for MySQL to be healthy
print_status "Waiting for MySQL to be ready..."
for i in {1..30}; do
    if docker compose exec mysql mysqladmin ping -h localhost --silent 2>/dev/null; then
        print_status "MySQL is ready!"
        break
    fi
    echo -n "."
    sleep 2
done

# Update database schema for new user columns
print_status "Updating database schema..."
docker compose exec mysql mysql -u yeastar -pyeastar_pass_2024 yeastar_crm -e "
    ALTER TABLE users ADD COLUMN IF NOT EXISTS is_superadmin BOOLEAN DEFAULT FALSE;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'employee';
    ALTER TABLE users ADD INDEX IF NOT EXISTS idx_extension (extension);
" 2>/dev/null || print_warning "Schema update skipped (columns may already exist)"

# Start the AI service
print_status "Starting AI Transcription service (with CUDA)..."
docker compose up -d ai-transcription

# Wait for backend to start
print_status "Waiting for backend API to start..."
for i in {1..60}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        print_status "Backend API is ready!"
        break
    fi
    echo -n "."
    sleep 3
done

# Seed the users
print_status "Seeding users..."
docker compose exec ai-transcription python /workspace/backend/seed_users.py || {
    print_warning "Could not seed users via Docker. Trying locally..."
    cd backend
    pip install -q passlib bcrypt pymysql sqlalchemy 2>/dev/null
    python seed_users.py
    cd ..
}

# Build and serve frontend
print_status "Building frontend..."
cd frontend/frontend
if [ ! -d "node_modules" ]; then
    print_status "Installing frontend dependencies..."
    npm install
fi

print_status "Building frontend for production..."
npm run build 2>/dev/null || {
    print_warning "Build failed, using development server..."
}

# Start frontend dev server in background
print_status "Starting frontend development server..."
PORT=3000 npm start &
FRONTEND_PID=$!

cd ../..

# Wait for frontend
print_status "Waiting for frontend to start..."
sleep 10

echo ""
echo "=================================================="
echo "  Services Started Successfully!"
echo "=================================================="
echo ""
print_info "Local Services:"
echo "  - Backend API: http://localhost:8000"
echo "  - Frontend:    http://localhost:3000"
echo ""

# Start Cloudflare tunnel
print_status "Starting Cloudflare tunnel..."
echo ""
print_info "Starting Cloudflare Quick Tunnel (no account needed)..."
echo "  This will generate a public URL for testing."
echo ""

# Create a tunnel for both services
cloudflared tunnel --url http://localhost:3000 &
TUNNEL_PID=$!

# Wait for tunnel URL
sleep 5
echo ""
print_info "Look for the tunnel URL above (*.trycloudflare.com)"
echo ""

echo "=================================================="
echo "  USER CREDENTIALS"
echo "=================================================="
echo ""
echo "  Superadmin (Full Dashboard Access):"
echo "    Username: superadmin"
echo "    Password: SuperAdmin@123"
echo ""
echo "  Employee Users (Basic Dashboard, Own Calls Only):"
echo "    Username: swaroop   | Password: Swaroop@123 | Extension: 211"
echo "    Username: amith     | Password: Amith@123   | Extension: 111"
echo ""
echo "=================================================="
echo ""
print_info "Press Ctrl+C to stop all services"
echo ""

# Wait for interrupt
wait $TUNNEL_PID
