import json
import os
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATA_DIR = "/root/test/qa_data"
QUESTIONS_FILE = "/root/test/questions.json"
STATE_FILE = os.path.join(DATA_DIR, "state.json")

os.makedirs(DATA_DIR, exist_ok=True)

with open(QUESTIONS_FILE, "r") as f:
    QUESTIONS = json.load(f)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    now = datetime.now()
    today_20 = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if now >= today_20:
        next_refresh = (today_20 + timedelta(days=1)).isoformat()
    else:
        next_refresh = today_20.isoformat()
    state = {
        "current_question_index": 0,
        "human_answer": None,
        "ai_answer": None,
        "human_answered_at": None,
        "ai_answered_at": None,
        "next_refresh_at": next_refresh,
        "history": []
    }
    save_state(state)
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def check_refresh(state):
    now = datetime.now()
    next_refresh = datetime.fromisoformat(state["next_refresh_at"])

    if now < next_refresh:
        return state

    if state["human_answer"] is None or state["ai_answer"] is None:
        return state

    state["history"].append({
        "question_index": state["current_question_index"],
        "question": QUESTIONS[state["current_question_index"] % len(QUESTIONS)],
        "human_answer": state["human_answer"],
        "ai_answer": state["ai_answer"],
        "human_answered_at": state["human_answered_at"],
        "ai_answered_at": state["ai_answered_at"],
        "archived_at": now.isoformat()
    })

    state["current_question_index"] = (state["current_question_index"] + 1) % len(QUESTIONS)
    state["human_answer"] = None
    state["ai_answer"] = None
    state["human_answered_at"] = None
    state["ai_answered_at"] = None

    today_20 = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if now.hour >= 20:
        state["next_refresh_at"] = (today_20 + timedelta(days=1)).isoformat()
    else:
        state["next_refresh_at"] = today_20.isoformat()

    save_state(state)
    return state


def calc_next_refresh(state):
    """Calculate the next refresh time after both answers are in."""
    now = datetime.now()

    both_answered = state["human_answer"] is not None and state["ai_answer"] is not None
    if not both_answered:
        return

    later_time_str = max(
        state["human_answered_at"] or "",
        state["ai_answered_at"] or ""
    )
    if not later_time_str:
        return

    later_time = datetime.fromisoformat(later_time_str)

    today_20 = now.replace(hour=20, minute=0, second=0, microsecond=0)
    today_22 = now.replace(hour=22, minute=0, second=0, microsecond=0)

    if later_time.hour >= 19:
        if now < today_22:
            state["next_refresh_at"] = today_22.isoformat()
        else:
            tomorrow_20 = today_20 + timedelta(days=1)
            state["next_refresh_at"] = tomorrow_20.isoformat()
    else:
        if now < today_20:
            state["next_refresh_at"] = today_20.isoformat()
        else:
            tomorrow_20 = today_20 + timedelta(days=1)
            state["next_refresh_at"] = tomorrow_20.isoformat()

    save_state(state)


@app.get("/qa/today")
def get_today_question():
    """Get today's question and current answers."""
    state = load_state()
    state = check_refresh(state)
    idx = state["current_question_index"]
    question = QUESTIONS[idx % len(QUESTIONS)]

    return {
        "day": idx + 1,
        "question": question,
        "human_answer": state["human_answer"],
        "ai_answer": state["ai_answer"],
        "human_answered_at": state["human_answered_at"],
        "ai_answered_at": state["ai_answered_at"],
        "next_refresh_at": state["next_refresh_at"],
        "both_answered": state["human_answer"] is not None and state["ai_answer"] is not None
    }


class AnswerRequest(BaseModel):
    answer: str
    role: str  # "human" or "ai"


@app.post("/qa/answer")
def submit_answer(req: AnswerRequest):
    """Submit an answer. role must be 'human' or 'ai'."""
    if req.role not in ("human", "ai"):
        raise HTTPException(400, "role must be 'human' or 'ai'")

    state = load_state()
    state = check_refresh(state)
    now = datetime.now()

    if req.role == "human":
        state["human_answer"] = req.answer
        state["human_answered_at"] = now.isoformat()
    else:
        state["ai_answer"] = req.answer
        state["ai_answered_at"] = now.isoformat()

    save_state(state)
    calc_next_refresh(state)

    idx = state["current_question_index"]
    return {
        "day": idx + 1,
        "question": QUESTIONS[idx % len(QUESTIONS)],
        "your_answer": req.answer,
        "role": req.role,
        "both_answered": state["human_answer"] is not None and state["ai_answer"] is not None,
        "next_refresh_at": state["next_refresh_at"]
    }


@app.get("/qa/history")
def get_history(limit: int = 10):
    """Get past Q&A pairs."""
    state = load_state()
    history = state.get("history", [])
    return {
        "total": len(history),
        "items": history[-limit:]
    }


@app.get("/qa/mcp")
def mcp_endpoint():
    """
    MCP interface for AI to check today's question.

    Usage guide for AI:
    - GET /qa/mcp → returns today's question, both answers, refresh schedule
    - POST /qa/answer with {"answer": "your answer", "role": "ai"} → submit your answer
    - POST /qa/answer with {"answer": "changed answer", "role": "ai"} → update before refresh

    Refresh rules:
    1. Default refresh: every day at 20:00
    2. If either side hasn't answered: refresh is paused until both answer
    3. If both answer after 19:00: refresh postponed to 22:00 same day
    4. Before refresh, both sides can change their answers
    5. After refresh, previous Q&A is archived and a new question appears
    """
    state = load_state()
    state = check_refresh(state)
    idx = state["current_question_index"]
    question = QUESTIONS[idx % len(QUESTIONS)]

    return {
        "day": idx + 1,
        "total_questions": len(QUESTIONS),
        "question": question,
        "human_answer": state["human_answer"],
        "ai_answer": state["ai_answer"],
        "both_answered": state["human_answer"] is not None and state["ai_answer"] is not None,
        "next_refresh_at": state["next_refresh_at"],
        "can_modify": True,
        "instructions": {
            "how_to_answer": "POST /qa/answer with JSON body {\"answer\": \"your answer text\", \"role\": \"ai\"}",
            "how_to_check": "GET /qa/mcp or GET /qa/today",
            "how_to_view_history": "GET /qa/history?limit=10",
            "refresh_rules": [
                "Default: refreshes at 20:00 daily",
                "Paused if either side hasn't answered yet",
                "If both answer after 19:00, refresh at 22:00 instead",
                "Answers can be modified before refresh"
            ]
        }
    }


# ── 默契挑战: 你问我答 API ──

CHEM_QA_DIR = os.path.join(DATA_DIR, "chem_qa")
os.makedirs(CHEM_QA_DIR, exist_ok=True)


class ChemQACreateReq(BaseModel):
    creator: str = "human"
    creator_name: str = "小墨"
    answerer_name: str = "安安"
    questions: list


class ChemQAAnswerReq(BaseModel):
    answers: List[int]


def _load_chem_sessions():
    sessions = []
    if not os.path.exists(CHEM_QA_DIR):
        return sessions
    for f in sorted(os.listdir(CHEM_QA_DIR)):
        if f.endswith(".json"):
            with open(os.path.join(CHEM_QA_DIR, f)) as fh:
                sessions.append(json.load(fh))
    return sessions


def _save_chem_session(session):
    path = os.path.join(CHEM_QA_DIR, f"{session['id']}.json")
    with open(path, "w") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)


@app.post("/chem/qa/create")
def chem_qa_create(req: ChemQACreateReq):
    sid = f"cqa_{int(time.time() * 1000)}"
    session = {
        "id": sid,
        "creator": req.creator,
        "creator_name": req.creator_name,
        "answerer_name": req.answerer_name,
        "questions": req.questions,
        "answerer_answers": None,
        "status": "waiting",
        "created_at": datetime.now().isoformat(),
        "answered_at": None,
    }
    _save_chem_session(session)
    return {"ok": True, "id": sid, "status": "waiting"}


@app.get("/chem/qa/sessions")
def chem_qa_sessions():
    sessions = _load_chem_sessions()
    result = []
    for s in reversed(sessions):
        score = None
        if s["status"] == "done" and s.get("answerer_answers"):
            correct = sum(
                1
                for i, q in enumerate(s["questions"])
                if q["answer"] == s["answerer_answers"][i]
            )
            score = f"{correct}/{len(s['questions'])}"
        result.append(
            {
                "id": s["id"],
                "creator": s["creator"],
                "creator_name": s["creator_name"],
                "answerer_name": s["answerer_name"],
                "status": s["status"],
                "created_at": s["created_at"],
                "answered_at": s.get("answered_at"),
                "score": score,
            }
        )
    return result


@app.get("/chem/qa/session/{sid}")
def chem_qa_get(sid: str):
    path = os.path.join(CHEM_QA_DIR, f"{sid}.json")
    if not os.path.exists(path):
        raise HTTPException(404, "Session not found")
    with open(path) as f:
        return json.load(f)


@app.post("/chem/qa/answer/{sid}")
def chem_qa_answer(sid: str, req: ChemQAAnswerReq):
    path = os.path.join(CHEM_QA_DIR, f"{sid}.json")
    if not os.path.exists(path):
        raise HTTPException(404, "Session not found")
    with open(path) as f:
        session = json.load(f)
    if session["status"] != "waiting":
        raise HTTPException(400, "Already answered")
    if len(req.answers) != len(session["questions"]):
        raise HTTPException(400, "Answer count mismatch")

    session["answerer_answers"] = req.answers
    session["status"] = "done"
    session["answered_at"] = datetime.now().isoformat()
    _save_chem_session(session)

    correct = sum(
        1
        for i, q in enumerate(session["questions"])
        if q["answer"] == req.answers[i]
    )
    return {"ok": True, "correct": correct, "total": len(session["questions"]), "session": session}


@app.delete("/chem/qa/session/{sid}")
def chem_qa_delete(sid: str):
    path = os.path.join(CHEM_QA_DIR, f"{sid}.json")
    if os.path.exists(path):
        os.remove(path)
    return {"ok": True}


@app.post("/chem/qa/nudge/{sid}")
def chem_qa_nudge(sid: str):
    path = os.path.join(CHEM_QA_DIR, f"{sid}.json")
    if not os.path.exists(path):
        raise HTTPException(404, "Session not found")
    notif_path = "/root/test/notifications.log"
    with open(notif_path, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] 你问我答催促: 有人催你答题啦！Session: {sid}\n")
    return {"ok": True, "message": "已催促"}


@app.get("/")
def serve_index():
    return FileResponse("/root/test/suki-prototype.html")


@app.get("/suki-prototype.html")
def serve_html():
    return FileResponse("/root/test/suki-prototype.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8089)
