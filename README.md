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
└── README.md
```

## License

MIT
