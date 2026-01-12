# Yeastar CRM - Hosting Documentation

**Developed by Jeffin Joe Jacob | Nexture**

---

## System Overview

Yeastar CRM is a call management and AI-powered transcription system that integrates with Yeastar Cloud PBX. The system consists of three main components:

1. **MySQL Database** - Stores contacts, call logs, extensions, and AI summaries
2. **AI Transcription Backend** - FastAPI server with GPU-accelerated transcription
3. **React Frontend** - Web-based dashboard for call management

---

## Hardware Requirements

### Recommended Specifications (Production)

| Component | Requirement |
|-----------|-------------|
| GPU | NVIDIA GPU with CUDA support (RTX 3080+ or A100 recommended) |
| VRAM | Minimum 8GB (16GB+ recommended for larger Whisper models) |
| RAM | 32GB minimum |
| Storage | 500GB SSD (for recordings and model cache) |
| CPU | 8+ cores |

### Current Deployment Environment

- **Device:** NVIDIA DGX Spark (GX10)
- **OS:** Linux 6.14.0-1015-nvidia
- **GPU:** NVIDIA GPU with CUDA support
- **Network:** 192.168.10.200 (Ethernet)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        GX10 / DGX Spark                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │   MySQL 8.0     │  │  AI Container   │  │ React Frontend  │ │
│  │   (Port 3306)   │  │  (Port 8000)    │  │  (Port 3000)    │ │
│  │                 │  │                 │  │                 │ │
│  │  - yeastar_crm  │  │  - FastAPI      │  │  - Dashboard    │ │
│  │  - call_logs    │  │  - Whisper AI   │  │  - Contacts     │ │
│  │  - contacts     │  │  - Ollama LLM   │  │  - Call History │ │
│  │  - extensions   │  │  - CUDA/GPU     │  │  - Extensions   │ │
│  │  - summaries    │  │                 │  │                 │ │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘ │
│           │                    │                    │          │
│           └────────────────────┼────────────────────┘          │
│                                │                               │
│                    ┌───────────┴───────────┐                   │
│                    │   Host Network Mode   │                   │
│                    │   (network_mode:host) │                   │
│                    └───────────────────────┘                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                   ┌────────────────────────┐
                   │   Yeastar Cloud PBX    │
                   │ alquozgtc.ras.yeastar  │
                   │      .com:443          │
                   └────────────────────────┘
```

---

## Docker Services

### 1. MySQL Database

```yaml
mysql:
  image: mysql:8.0
  container_name: yeastar-mysql
  environment:
    MYSQL_ROOT_PASSWORD: yeastar_root_2024
    MYSQL_DATABASE: yeastar_crm
    MYSQL_USER: yeastar
    MYSQL_PASSWORD: yeastar_pass_2024
  ports:
    - "3306:3306"
  volumes:
    - mysql_data:/var/lib/mysql
    - ./backend/init_db.sql:/docker-entrypoint-initdb.d/init.sql
```

**Database Tables:**
- `users` - System users and authentication
- `contacts` - Customer contact information
- `extensions` - PBX extension data
- `call_logs` - Call history synchronized from Yeastar
- `call_summaries` - AI-generated transcriptions and analysis
- `notes` - Manual notes attached to calls/contacts

### 2. AI Transcription Container

```yaml
ai-transcription:
  image: nvcr.io/nvidia/pytorch:25.11-py3
  container_name: yeastar-ai
  network_mode: host
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

**Key Features:**
- **Base Image:** NVIDIA PyTorch container with CUDA support
- **Network Mode:** Host (for WebSocket and PBX communication)
- **GPU Access:** Full NVIDIA GPU reservation for AI workloads

**AI Components:**
| Component | Purpose | Model |
|-----------|---------|-------|
| Faster-Whisper | Speech-to-text transcription | large-v3 |
| Ollama | Call summarization & analysis | llama3.2 / gemma2 |

**Environment Variables:**
```bash
NVIDIA_VISIBLE_DEVICES=all
TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
HF_HOME=/workspace/huggingface
HF_TOKEN=${HF_TOKEN:-}

# Database
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=yeastar_crm
DB_USER=yeastar
DB_PASSWORD=yeastar_pass_2024

# Yeastar PBX
YEASTAR_HOST=alquozgtc.ras.yeastar.com
YEASTAR_PORT=443
YEASTAR_USERNAME=admin
YEASTAR_PASSWORD=P@ssw0rd123
YEASTAR_CLIENT_ID=B8wFfDNwuu2CguKV1HCehwwgsMVsrWeG
YEASTAR_CLIENT_SECRET=EoOKDu8qNGLljq4sfscE8096Zv5tYH6X
```

### 3. React Frontend

The frontend runs separately (not containerized) using:
```bash
cd frontend/frontend
npm start  # Development (port 3000)
npm run build && npx serve -s build -l 3000  # Production
```

---

## CUDA & GPU Configuration

### Prerequisites

1. **NVIDIA Driver:** Version 535+ installed on host
2. **NVIDIA Container Toolkit:** For Docker GPU access
3. **CUDA:** Version 12.0+ (included in container)

### Verify GPU Access

```bash
# Check NVIDIA driver
nvidia-smi

# Test GPU in container
docker exec yeastar-ai nvidia-smi

# Verify CUDA
docker exec yeastar-ai python -c "import torch; print(torch.cuda.is_available())"
```

### GPU Memory Requirements

| Whisper Model | VRAM Required | Accuracy |
|---------------|---------------|----------|
| tiny          | ~1 GB         | Low      |
| base          | ~1 GB         | Fair     |
| small         | ~2 GB         | Good     |
| medium        | ~5 GB         | Better   |
| large-v3      | ~10 GB        | Best     |

**Current Configuration:** `large-v3` model for optimal transcription accuracy

---

## Deployment Steps

### 1. Clone Repository

```bash
git clone https://github.com/nextureamer-dev/Yeastar.git
cd Yeastar
```

### 2. Start Docker Services

```bash
# Start MySQL and AI container
docker compose up -d

# Check container status
docker compose ps

# View logs
docker compose logs -f ai-transcription
```

### 3. Verify Backend API

```bash
# Health check
curl http://localhost:8000/api/pbx/status

# Call stats
curl http://localhost:8000/api/calls/stats?days=7

# AI status
curl http://localhost:8000/api/transcription/status
```

### 4. Start Frontend

```bash
cd frontend/frontend
npm install
npm start
```

### 5. Access Dashboard

- **Local:** http://localhost:3000
- **Remote:** Configure Cloudflare Tunnel (see below)

---

## Remote Access (Cloudflare Tunnel)

For accessing from remote locations without opening firewall ports:

```bash
# Install cloudflared
sudo apt install cloudflared

# Start backend tunnel
cloudflared tunnel --url http://localhost:8000

# Start frontend tunnel (separate terminal)
cloudflared tunnel --url http://localhost:3000
```

Update frontend `.env` with backend tunnel URL:
```bash
REACT_APP_API_URL=https://<backend-tunnel>.trycloudflare.com/api
REACT_APP_WS_URL=wss://<backend-tunnel>.trycloudflare.com/ws
```

---

## Yeastar PBX Configuration

### API Credentials

The system uses Yeastar Open API v1.0 for:
- Call log synchronization
- Extension status monitoring
- CTI (Computer Telephony Integration) controls
- Recording file downloads

### Required Settings in Yeastar Admin

1. **Enable Open API:** PBX Settings → General → Open API
2. **Create API Application:** Get Client ID and Secret
3. **Whitelist IP:** Add server IP (192.168.10.200) to allowed list
4. **Enable Call Recording:** For AI transcription to work

### CDR Sync

Call logs are automatically synchronized every 5 minutes:
- Inbound calls
- Outbound calls
- Internal calls
- Missed calls
- Recording files

---

## AI Transcription Pipeline

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ Call Ends    │───▶│ Download     │───▶│ Whisper      │───▶│ Ollama LLM   │
│ Recording    │    │ .wav File    │    │ Transcribe   │    │ Summarize    │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
                                                                    │
                                                                    ▼
                                              ┌──────────────────────────────┐
                                              │ Call Summary Stored          │
                                              │ - Transcript                 │
                                              │ - Category (Enquiry/Sales/..)│
                                              │ - Sentiment (Pos/Neg/Neutral)│
                                              │ - Key Topics                 │
                                              │ - Action Items               │
                                              └──────────────────────────────┘
```

### Process Calls Manually

```bash
# Process single call
curl -X POST "http://localhost:8000/api/transcription/process/{call_id}"

# Batch process
curl -X POST "http://localhost:8000/api/transcription/batch-process?limit=10"
```

---

## Monitoring & Maintenance

### View Logs

```bash
# All services
docker compose logs -f

# AI container only
docker compose logs -f ai-transcription

# MySQL only
docker compose logs -f mysql
```

### Restart Services

```bash
# Restart all
docker compose restart

# Restart AI container (preserves GPU allocation)
docker compose restart ai-transcription

# Full rebuild
docker compose down && docker compose up -d
```

### Database Backup

```bash
# Backup
docker exec yeastar-mysql mysqldump -u root -pyeastar_root_2024 yeastar_crm > backup.sql

# Restore
docker exec -i yeastar-mysql mysql -u root -pyeastar_root_2024 yeastar_crm < backup.sql
```

---

## Ports Summary

| Service | Port | Protocol | Description |
|---------|------|----------|-------------|
| MySQL | 3306 | TCP | Database connections |
| Backend API | 8000 | HTTP | REST API & WebSocket |
| Frontend | 3000 | HTTP | React development server |
| Yeastar PBX | 443 | HTTPS | Cloud PBX API |

---

## Troubleshooting

### GPU Not Detected

```bash
# Check NVIDIA driver
nvidia-smi

# Reinstall container toolkit
sudo apt install nvidia-container-toolkit
sudo systemctl restart docker
```

### Database Connection Failed

```bash
# Check MySQL is running
docker compose ps mysql

# Test connection
mysql -h 127.0.0.1 -P 3306 -u yeastar -pyeastar_pass_2024 yeastar_crm
```

### Yeastar API "IP FORBIDDEN"

1. Login to Yeastar admin panel
2. Go to API settings
3. Add server IP to whitelist
4. Restart AI container: `docker compose restart ai-transcription`

### AI Models Not Loading

```bash
# Check Ollama status
docker exec yeastar-ai curl http://localhost:11434/api/tags

# Pull model manually
docker exec yeastar-ai ollama pull llama3.2
```

---

## Security Notes

- Change default database passwords in production
- Use environment variables for sensitive credentials
- Configure firewall to restrict port access
- Enable HTTPS for production deployments
- Rotate Yeastar API credentials periodically

---

**Last Updated:** January 2026
**Version:** 1.0
