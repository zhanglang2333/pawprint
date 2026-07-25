# Pawprint 🐾

A web-based virtual pet companion with room decoration, dress-up, and daily check-in features.

## Features

- **Virtual Pet** — Raise and interact with your pet companion
- **Room Decoration** — Customize your pet's living space with furniture and wallpaper
- **Dress-up** — Change your pet's outfits and accessories
- **Daily Check-in** — Track habits and goals with your pet by your side
- **Pet Stats** — Watch your pet's mood, hunger, and affection grow over time

## Tech Stack

- **Frontend**: Vue 3 + Vite
- **Backend**: Python (FastAPI)
- **Database**: SQLite

## Getting Started

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

## Deployment

### 1. Backend

```bash
# Clone and install
git clone https://github.com/zhanglang2333/pawprint.git
cd pawprint/backend
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys and settings

# Run with systemd (recommended)
sudo cp pawprint.service /etc/systemd/system/
sudo systemctl enable pawprint
sudo systemctl start pawprint

# Or run directly
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run build
# Serve the dist/ folder with Nginx or any static file server
```

### 3. Nginx (reverse proxy)

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
    }

    location / {
        root /path/to/pawprint/frontend/dist;
        try_files $uri $uri/ /index.html;
    }
}
```

## AI Integration (Claude Code + MCP)

Pawprint exposes MCP tools that allow AI assistants to interact with the app. When deploying with Claude Code, the AI can manage check-in goals, read notifications, and more.

### Monitor Setup

The backend writes events to `qa_data/notifications.log`. To let your AI assistant react to real-time events (new check-ins, messages, drawings, etc.), add the following to your `CLAUDE.md`:

```markdown
## Pawprint Notifications

Use the Monitor tool to watch for new Pawprint events:

\`\`\`
Monitor qa_data/notifications.log for new lines. When a new notification appears:
- Read it and decide if it needs a response
- For check-in events (✅), acknowledge the user
- For drawing/game events (🎨🎭), participate if prompted
- For question events (💬🔄), answer or remind the user
\`\`\`
```

Then ask your AI to run:

```
Monitor({path: "qa_data/notifications.log", pattern: ".*"})
```

This lets the AI see events like:
- `✅ 打卡：背单词（100%）` — check-in completed
- `🎨 小墨画了一幅画，等安安来猜！` — drawing game started
- `💬 Ta回答了今天的问题` — daily Q&A answered
- `🔄 新问题已刷新` — new daily question available

## Project Structure

```
pawprint/
├── frontend/          # Vue 3 app
│   ├── src/
│   │   ├── components/
│   │   ├── views/
│   │   ├── assets/
│   │   └── App.vue
│   └── package.json
├── backend/           # FastAPI server
│   ├── main.py
│   ├── models/
│   ├── routes/
│   └── requirements.txt
├── qa_data/           # Runtime data
│   └── notifications.log
└── README.md
```

## License

MIT
