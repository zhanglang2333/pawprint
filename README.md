# Pawprint 🐾

A web-based virtual pet companion with room decoration, dress-up, and daily check-in features.

## Features

- **Virtual Pet** — Raise and interact with your pet companion
- **Room Decoration** — Customize your pet's living space with furniture and wallpaper
- **Dress-up** — Change your pet's outfits and accessories
- **Daily Check-in** — Track habits and goals with your pet by your side
- **Pet Stats** — Watch your pet's mood, hunger, and affection grow over time
- **Check-in Reminders** — Backend auto-checks daily progress and notifies AI to remind you (inspired by [Izayoi](https://github.com/user/izayoi) tutorial's nudge design)

## Tech Stack

- **Frontend**: Vue 3 + Vite (served by FastAPI, no separate web server needed)
- **Backend**: Python (FastAPI) — serves both API and frontend in one process
- **Database**: SQLite

## Getting Started

### Quick Start (single process)

```bash
cd backend
pip install -r requirements.txt
python server.py
```

This starts the server on port 8089. The backend serves both the API and the frontend HTML.

- Frontend: `http://localhost:8089/`
- MCP endpoint: `http://localhost:8089/mcp`

### Frontend Development

For frontend development with hot reload:

```bash
cd frontend
npm install
npm run dev
```

## Deployment

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
python server.py
```

No Nginx needed — FastAPI serves everything on a single port (8089).

## AI Integration (Claude Code + MCP)

Pawprint exposes MCP tools that allow AI assistants to interact with the app. When deploying with Claude Code, the AI can manage check-in goals, read notifications, and more.

### Monitor Setup

The backend writes events to `qa_data/notifications.log`. To let your AI assistant react to real-time events (new check-ins, reminders, messages, etc.), add the following to your `CLAUDE.md`:

```markdown
## Pawprint Notifications

Use the Monitor tool to watch for new Pawprint events:

\`\`\`
Monitor qa_data/notifications.log for new lines. When a new notification appears:
- Read it and decide if it needs a response
- For check-in events (✅), acknowledge and praise the user
- For reminder events (⏰), nudge the user in your own words
- For drawing/game events (🎨🎭), participate if prompted
- For question events (💬🔄), answer or remind the user
\`\`\`
```

Then ask your AI to run:

```
Monitor({path: "qa_data/notifications.log", pattern: ".*"})
```

Events look like:
- `✅ 打卡：背单词（100%）` — check-in completed
- `⏰ 打卡提醒（第1轮）：今日完成30%，未完成：背单词、锻炼` — reminder for unchecked goals
- `🎨 小墨画了一幅画，等安安来猜！` — drawing game started
- `💬 Ta回答了今天的问题` — daily Q&A answered

### Check-in Reminder System

The backend runs a background task that:
1. Checks daily goal completion at 20:00
2. If not 100% done, writes a reminder to `notifications.log`
3. Reminds up to 3 times with escalating intervals (20:00, 20:30, 21:00)
4. AI sees the reminder via Monitor and nudges the user **in its own voice** — not a system notification

Key design principles (inspired by Izayoi tutorial):
- Reminders come from the AI, not the system — "someone is waiting for you" vs "alarm went off"
- Praise immediately on completion — don't delay positive reinforcement
- No streaks, no completion rates — praise only today, never mention yesterday

## Project Structure

```
pawprint/
├── frontend/          # Vue 3 app (development)
│   ├── src/
│   │   ├── components/
│   │   ├── views/
│   │   ├── assets/
│   │   └── App.vue
│   └── package.json
├── backend/           # FastAPI server (serves everything)
│   ├── server.py      # Main server — API + frontend + MCP
│   ├── suki-prototype.html  # Frontend prototype
│   ├── questions.json
│   └── requirements.txt
├── qa_data/           # Runtime data (gitignored)
│   └── notifications.log
└── README.md
```

## License

CC BY-NC 4.0 — Non-commercial use only.
