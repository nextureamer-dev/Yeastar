#!/bin/bash
# AI Services Setup Script for Yeastar CRM
# This script installs Ollama with Llama 3.1 8B and Python dependencies for faster-whisper

set -e

echo "=========================================="
echo "Yeastar CRM AI Services Setup"
echo "=========================================="
echo ""
echo "Components:"
echo "  - Ollama + Llama 3.1 8B (call analysis)"
echo "  - faster-whisper (speech-to-text, installed via pip)"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Install Ollama
install_ollama() {
    echo ""
    echo -e "${GREEN}Step 1: Installing Ollama...${NC}"

    if command -v ollama &> /dev/null; then
        echo "Ollama is already installed."
        ollama --version
    else
        echo "Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
        echo -e "${GREEN}Ollama installed successfully!${NC}"
    fi
}

# Step 2: Start Ollama service
start_ollama() {
    echo ""
    echo -e "${GREEN}Step 2: Starting Ollama service...${NC}"

    # Check if ollama is running
    if pgrep -x "ollama" > /dev/null; then
        echo "Ollama is already running."
    else
        echo "Starting Ollama in background..."
        nohup ollama serve > /tmp/ollama.log 2>&1 &
        sleep 3
        echo "Ollama started."
    fi
}

# Step 3: Pull Llama 3.1 8B model
pull_llama3() {
    echo ""
    echo -e "${GREEN}Step 3: Pulling Llama 3.1 8B model...${NC}"
    echo "This will download ~4.5GB..."

    ollama pull llama3.1:8b

    echo -e "${GREEN}Llama 3.1 8B model pulled successfully!${NC}"
}

# Step 4: Install Python dependencies
install_python_deps() {
    echo ""
    echo -e "${GREEN}Step 4: Installing Python dependencies...${NC}"

    cd "$(dirname "$0")"

    if [ -d "venv" ]; then
        echo "Activating existing virtual environment..."
        source venv/bin/activate
    else
        echo "Creating virtual environment..."
        python3 -m venv venv
        source venv/bin/activate
    fi

    echo "Installing requirements (including faster-whisper)..."
    pip install --upgrade pip
    pip install -r requirements.txt

    echo -e "${GREEN}Python dependencies installed!${NC}"
}

# Step 5: Verify setup
verify_setup() {
    echo ""
    echo -e "${GREEN}Step 5: Verifying setup...${NC}"

    echo ""
    echo "Checking Ollama..."
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Ollama is running${NC}"
        MODELS=$(curl -s http://localhost:11434/api/tags | python3 -c "import sys, json; print([m['name'] for m in json.load(sys.stdin).get('models', [])])" 2>/dev/null || echo "[]")
        echo "  Available models: $MODELS"
    else
        echo -e "${RED}✗ Ollama is not running${NC}"
        echo "  Try: ollama serve"
    fi

    echo ""
    echo "Checking faster-whisper..."
    if python3 -c "import faster_whisper; print('faster-whisper version:', faster_whisper.__version__)" 2>/dev/null; then
        echo -e "${GREEN}✓ faster-whisper is installed${NC}"
    else
        echo -e "${RED}✗ faster-whisper not found${NC}"
        echo "  Try: pip install faster-whisper"
    fi

    echo ""
    echo "Checking CUDA availability..."
    python3 -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('CUDA device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')" 2>/dev/null || echo "PyTorch not installed or CUDA check failed"

    echo ""
    echo "=========================================="
    echo -e "${GREEN}Setup complete!${NC}"
    echo "=========================================="
    echo ""
    echo "To start the backend:"
    echo "  cd $(dirname "$0")"
    echo "  source venv/bin/activate"
    echo "  python -m uvicorn app.main:app --host 0.0.0.0 --port 8080"
    echo ""
    echo "The first transcription will take longer as it downloads the Whisper model (~3GB)."
    echo ""
}

# Main execution
main() {
    install_ollama
    start_ollama
    pull_llama3
    install_python_deps
    verify_setup
}

# Run main function
main
