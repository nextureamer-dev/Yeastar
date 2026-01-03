# Yeastar CRM Integration

A full-featured CRM integration for Yeastar PBX systems with real-time call management.

## Features

- **User Authentication** - JWT-based login/registration with role management
- **Contact Management** - Create, edit, organize contacts with favorites and tags
- **Contact Details** - Full contact view with call history and activity notes
- **Call History** - Detailed call logs with filtering by direction, status, date
- **Click-to-Call** - Initiate calls directly from contacts or dialer
- **Dialer Component** - Full dial pad with hold, transfer, hangup controls
- **Call Popup** - Real-time incoming call notifications with caller info
- **Extension Management** - Monitor and manage PBX extensions
- **CDR Sync** - Automatic/manual call detail record synchronization
- **Dashboard** - Overview of call statistics and PBX status
- **Real-time Updates** - WebSocket-based live updates

## Architecture

```
├── backend/                    # Python FastAPI backend
│   ├── app/
│   │   ├── main.py            # FastAPI application
│   │   ├── config.py          # Configuration settings
│   │   ├── database.py        # Database connection
│   │   ├── models/            # SQLAlchemy models
│   │   │   ├── contact.py
│   │   │   ├── call_log.py
│   │   │   ├── extension.py
│   │   │   ├── note.py
│   │   │   └── user.py
│   │   ├── schemas/           # Pydantic schemas
│   │   ├── routers/           # API endpoints
│   │   │   ├── auth.py
│   │   │   ├── contacts.py
│   │   │   ├── calls.py
│   │   │   ├── extensions.py
│   │   │   ├── notes.py
│   │   │   ├── pbx.py
│   │   │   └── webhook.py
│   │   └── services/          # Business logic
│   │       ├── yeastar_client.py
│   │       ├── auth.py
│   │       ├── cdr_sync.py
│   │       ├── webhook_handler.py
│   │       └── websocket_manager.py
│   ├── requirements.txt
│   └── init_db.sql
│
└── frontend/                   # React TypeScript frontend
    └── src/
        ├── api/client.ts       # API client with auth
        ├── components/
        │   ├── Layout.tsx
        │   ├── CallPopup.tsx
        │   └── Dialer.tsx
        ├── pages/
        │   ├── Login.tsx
        │   ├── Dashboard.tsx
        │   ├── Contacts.tsx
        │   ├── ContactDetail.tsx
        │   ├── CallHistory.tsx
        │   ├── Extensions.tsx
        │   └── Settings.tsx
        └── hooks/
            └── useWebSocket.ts
```

## Prerequisites

- Python 3.9+
- Node.js 16+
- MySQL 8.0+
- Yeastar PBX with API access enabled

## Installation

### 1. Database Setup

```bash
# Option 1: Run the SQL init script
mysql -u root -p < backend/init_db.sql

# Option 2: Create database manually
mysql -u root -p -e "CREATE DATABASE yeastar_crm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

### 2. Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your settings
```

### 3. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install
```

## Configuration

Edit `backend/.env` with your settings:

```env
# Yeastar PBX Configuration
YEASTAR_HOST=192.168.1.100
YEASTAR_PORT=8088
YEASTAR_USERNAME=api
YEASTAR_PASSWORD=your_password

# MySQL Database Configuration
DB_HOST=localhost
DB_PORT=3306
DB_NAME=yeastar_crm
DB_USER=root
DB_PASSWORD=your_db_password

# Application Settings
SECRET_KEY=your-secret-key-change-in-production
API_PORT=8000
WEBHOOK_PORT=8001
```

## Running the Application

### Start Backend

```bash
cd backend
source venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Start Frontend

```bash
cd frontend
npm start
```

Access the CRM at: http://localhost:3000

**First user registered becomes admin.**

## Yeastar PBX Configuration

### Enable API Access

1. Login to Yeastar PBX web interface
2. Go to Settings > PBX > General > API
3. Enable API
4. Create API user credentials
5. Note the port number (default: 8088)

### Configure Webhooks

Configure your Yeastar PBX to send events to the CRM webhook:

1. Go to Settings > PBX > General > API
2. Set the Application Server URL to: `http://YOUR_CRM_IP:8000/api/webhook`
3. Enable event notifications for:
   - Call events
   - CDR events
   - Extension status

## API Endpoints

### Authentication
- `POST /api/auth/login` - Login and get JWT token
- `POST /api/auth/register` - Register new user
- `GET /api/auth/me` - Get current user info
- `PUT /api/auth/me` - Update current user
- `GET /api/auth/users` - List users (admin)
- `DELETE /api/auth/users/{id}` - Delete user (admin)

### Contacts
- `GET /api/contacts` - List contacts
- `GET /api/contacts/{id}` - Get contact
- `GET /api/contacts/lookup?phone=` - Lookup by phone
- `POST /api/contacts` - Create contact
- `PUT /api/contacts/{id}` - Update contact
- `DELETE /api/contacts/{id}` - Delete contact

### Calls
- `GET /api/calls` - List call logs
- `GET /api/calls/stats` - Get statistics
- `GET /api/calls/active` - Get active calls
- `POST /api/calls/dial` - Make a call
- `POST /api/calls/hangup` - End a call
- `POST /api/calls/hold` - Hold a call
- `POST /api/calls/unhold` - Resume call
- `POST /api/calls/transfer` - Transfer a call
- `POST /api/calls/sync` - Sync CDR from PBX

### Notes
- `GET /api/notes` - List notes
- `POST /api/notes` - Create note
- `PUT /api/notes/{id}` - Update note
- `DELETE /api/notes/{id}` - Delete note

### Extensions
- `GET /api/extensions` - List extensions
- `GET /api/extensions/sync` - Sync from PBX
- `GET /api/extensions/{number}` - Get extension
- `GET /api/extensions/{number}/voicemails` - Get voicemails

### PBX
- `GET /api/pbx/info` - Get PBX info
- `GET /api/pbx/status` - Get connection status
- `POST /api/pbx/login` - Force PBX login
- `POST /api/pbx/logout` - Logout from PBX
- `GET /api/pbx/queues` - Get queue status

### Webhook
- `POST /api/webhook` - Receive PBX events
- `POST /api/webhook/cdr` - Receive CDR events

### WebSocket
- `ws://localhost:8000/ws` - Real-time events
- `ws://localhost:8000/ws?extension=1001` - Extension-specific events

## Screenshots

The CRM includes:
- Dashboard with call statistics and PBX status
- Contact list with search, favorites, and quick actions
- Contact detail page with full call history
- Call history with filtering and status badges
- Extensions overview with live status
- Settings page with PBX connection info
- Click-to-call dialer with full controls

## License

MIT
