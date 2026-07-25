"""
小窝 - 统一服务

一个文件，一个端口（8089），一行启动：python3 server.py

包含：
- 网页前端（serve HTML）
- 前端用的REST接口（/qa/today, /qa/answer, /qa/history）
- MCP接口（/mcp，供AI连接）

MCP连接方式：
  transport: streamable-http
  url: http://<server-ip>:8089/mcp
"""

import json
import time
import uuid
import calendar
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional, List
from lunardate import LunarDate

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "qa_data"
QUESTIONS_FILE = BASE_DIR / "questions.json"
STATE_FILE = DATA_DIR / "state.json"
NOTIFY_LOG = DATA_DIR / "notifications.log"
ANNIVERSARY_FILE = DATA_DIR / "anniversaries.json"
MOOD_FILE = DATA_DIR / "moods.json"

UPLOADS_DIR = DATA_DIR / "uploads"
PERIOD_FILE = DATA_DIR / "period.json"
BETTER_FILE = DATA_DIR / "better.json"
DATA_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

with open(QUESTIONS_FILE, "r") as f:
    QUESTIONS = json.load(f)


# ============ Notify ============

def notify(text: str):
    now = datetime.now().isoformat()
    with open(NOTIFY_LOG, "a") as f:
        f.write(f"[{now}] {text}\n")


# ============ State Management ============

def load_state():
    if STATE_FILE.exists():
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

    new_q = QUESTIONS[state["current_question_index"] % len(QUESTIONS)]
    notify(f"🔄 新问题已刷新\nDay {state['current_question_index'] + 1}: {new_q}")

    return state


def calc_next_refresh(state):
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
            state["next_refresh_at"] = (today_20 + timedelta(days=1)).isoformat()
    else:
        if now < today_20:
            state["next_refresh_at"] = today_20.isoformat()
        else:
            state["next_refresh_at"] = (today_20 + timedelta(days=1)).isoformat()

    save_state(state)


def do_answer(answer: str, role: str):
    state = load_state()
    state = check_refresh(state)
    now = datetime.now()

    if role == "human":
        state["human_answer"] = answer
        state["human_answered_at"] = now.isoformat()
        notify(f"💬 Ta回答了今天的问题\nDay {state['current_question_index'] + 1}: {QUESTIONS[state['current_question_index'] % len(QUESTIONS)]}\n回答: {answer}")
    else:
        state["ai_answer"] = answer
        state["ai_answered_at"] = now.isoformat()

    save_state(state)
    calc_next_refresh(state)
    return state


def get_today():
    state = load_state()
    state = check_refresh(state)
    idx = state["current_question_index"]
    return state, idx, QUESTIONS[idx % len(QUESTIONS)]


# ============ Anniversary ============

MILESTONES = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]

LUNAR_MONTHS = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"]
LUNAR_DAYS = [
    "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
]

def solar_to_lunar_str(d: date) -> str:
    try:
        ld = LunarDate.fromSolarDate(d.year, d.month, d.day)
        return f"{LUNAR_MONTHS[ld.month - 1]}月{LUNAR_DAYS[ld.day - 1]}"
    except Exception:
        return ""


def lunar_to_solar(year: int, month: int, day: int) -> Optional[date]:
    try:
        ld = LunarDate(year, month, day)
        return ld.toSolarDate()
    except Exception:
        return None


def lunar_str(month: int, day: int) -> str:
    if 1 <= month <= 12 and 1 <= day <= 30:
        return f"{LUNAR_MONTHS[month - 1]}月{LUNAR_DAYS[day - 1]}"
    return ""


def load_anniversaries():
    if ANNIVERSARY_FILE.exists():
        with open(ANNIVERSARY_FILE, "r") as f:
            return json.load(f)
    return []


def save_anniversaries(data):
    with open(ANNIVERSARY_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def calc_anniversary_info(item):
    d = date.fromisoformat(item["date"])
    today = date.today()
    is_lunar = item.get("date_type") == "lunar"
    lm = item.get("lunar_month", 0)
    ld_day = item.get("lunar_day", 0)

    delta = (today - d).days
    if item.get("include_start", False):
        delta += 1

    display_lunar = lunar_str(lm, ld_day) if is_lunar else solar_to_lunar_str(d)
    info = {**item, "days_passed": delta, "lunar_date": display_lunar, "lunar_today": solar_to_lunar_str(today)}

    upcoming = []
    base_delta = delta if not item.get("include_start", False) else delta - 1
    for m in MILESTONES:
        if base_delta < m:
            upcoming.append({"type": "milestone", "label": f"{m}天", "date": (d + timedelta(days=m)).isoformat(), "days_left": m - base_delta})

    if item.get("yearly", False):
        if is_lunar and lm and ld_day:
            this_year_solar = lunar_to_solar(today.year, lm, ld_day)
            if this_year_solar and this_year_solar < today:
                this_year_solar = lunar_to_solar(today.year + 1, lm, ld_day)
            if this_year_solar:
                orig_solar = lunar_to_solar(d.year, lm, ld_day) or d
                years = this_year_solar.year - orig_solar.year
                upcoming.append({"type": "yearly", "label": f"{years}周年", "date": this_year_solar.isoformat(), "days_left": (this_year_solar - today).days})
        else:
            this_year_ann = d.replace(year=today.year)
            if this_year_ann < today:
                this_year_ann = d.replace(year=today.year + 1)
            years = this_year_ann.year - d.year
            upcoming.append({"type": "yearly", "label": f"{years}周年", "date": this_year_ann.isoformat(), "days_left": (this_year_ann - today).days})

    upcoming.sort(key=lambda x: x["days_left"])
    info["upcoming"] = upcoming[:3]
    return info


def check_anniversary_notifications():
    annivs = load_anniversaries()
    today = date.today()
    tomorrow = today + timedelta(days=1)
    notified = []

    for item in annivs:
        d = date.fromisoformat(item["date"])
        delta_today = (today - d).days
        delta_tomorrow = (tomorrow - d).days

        for m in MILESTONES:
            if delta_today == m:
                notified.append(f"🚩 今天是「{item['name']}」的第{m}天！")
            if delta_tomorrow == m:
                notified.append(f"🔔 明天是「{item['name']}」的第{m}天")

        if item.get("yearly", False):
            if today.month == d.month and today.day == d.day:
                years = today.year - d.year
                notified.append(f"🎉 今天是「{item['name']}」{years}周年！")
            if tomorrow.month == d.month and tomorrow.day == d.day:
                years = tomorrow.year - d.year
                notified.append(f"🔔 明天是「{item['name']}」{years}周年")

    for msg in notified:
        notify(msg)
    return notified


# ============ Mood Diary ============

MOOD_TYPES = {
    "开心": "😊", "兴奋": "🤩", "心动": "🥰", "平静": "😌",
    "心累": "😮‍💨", "烦躁": "😤", "伤心": "😢", "生气": "😡"
}
MOOD_NAMES = list(MOOD_TYPES.keys())
TAGS_FILE = DATA_DIR / "mood_tags.json"

def load_tags():
    if TAGS_FILE.exists():
        with open(TAGS_FILE, "r") as f:
            return json.load(f)
    return ["生活点滴", "约会日常", "记仇小本"]

def save_tags(tags):
    with open(TAGS_FILE, "w") as f:
        json.dump(tags, f, ensure_ascii=False)


def load_moods():
    if MOOD_FILE.exists():
        with open(MOOD_FILE, "r") as f:
            return json.load(f)
    return []


def save_moods(data):
    with open(MOOD_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_mood_by_date(d: str, role: str = "human"):
    moods = load_moods()
    for m in moods:
        if m["date"] == d and m.get("role", "human") == role:
            return m
    return None


def set_mood(d: str, mood_name: str, role: str = "human", note: str = "", tags: list = None, photos: list = None):
    moods = load_moods()
    existing = None
    for m in moods:
        if m["date"] == d and m.get("role", "human") == role:
            existing = m
            break
    if existing:
        existing["mood"] = mood_name
        existing["note"] = note
        existing["tags"] = tags or []
        if photos is not None:
            existing["photos"] = photos
        existing["updated_at"] = datetime.now().isoformat()
    else:
        moods.append({
            "id": str(uuid.uuid4())[:8],
            "date": d,
            "role": role,
            "mood": mood_name,
            "note": note,
            "tags": tags or [],
            "photos": photos or [],
            "created_at": datetime.now().isoformat()
        })
    save_moods(moods)
    emoji = MOOD_TYPES.get(mood_name, "")
    who = "Ta" if role == "human" else "AI"
    notify(f"📖 {who}记录心情：{emoji}{mood_name} {d}" + (f" — {note}" if note else ""))


def get_moods_for_month(year: int, month: int):
    moods = load_moods()
    prefix = f"{year}-{month:02d}"
    return [m for m in moods if m["date"].startswith(prefix)]


def calc_mood_stats(year: int, month: int):
    moods = get_moods_for_month(year, month)
    days_in_month = calendar.monthrange(year, month)[1]
    today = date.today()
    if year == today.year and month == today.month:
        total_days = today.day
    else:
        total_days = days_in_month

    human_moods = [m for m in moods if m.get("role", "human") == "human"]
    ai_moods = [m for m in moods if m.get("role") == "ai"]

    def count_by_type(entries):
        counts = {name: 0 for name in MOOD_NAMES}
        for e in entries:
            if e["mood"] in counts:
                counts[e["mood"]] += 1
        return counts

    human_counts = count_by_type(human_moods)
    ai_counts = count_by_type(ai_moods)

    human_dates = {m["date"] for m in human_moods}
    ai_dates = {m["date"] for m in ai_moods}
    human_by_date = {m["date"]: m["mood"] for m in human_moods}
    ai_by_date = {m["date"]: m["mood"] for m in ai_moods}

    sync_days = 0
    for d in human_by_date:
        if d in ai_by_date and human_by_date[d] == ai_by_date[d]:
            sync_days += 1

    def calc_title(entries, total):
        if total == 0:
            return "潜水"
        if len(entries) / total < 0.4:
            return "潜水"
        counts = count_by_type(entries)
        top = max(counts, key=counts.get)
        return top

    human_title = calc_title(human_moods, total_days)
    ai_title = calc_title(ai_moods, total_days)

    title_revealed = True
    if year == today.year and month == today.month:
        title_revealed = False
    elif year == today.year and month > today.month:
        title_revealed = False

    return {
        "year": year, "month": month, "total_days": total_days,
        "human": {"count": len(human_moods), "by_type": human_counts, "title": human_title},
        "ai": {"count": len(ai_moods), "by_type": ai_counts, "title": ai_title},
        "sync_days": sync_days,
        "mood_types": MOOD_TYPES,
        "title_revealed": title_revealed
    }


# ============ FastAPI (Web + REST) ============

app = FastAPI(title="小窝")


class AnswerRequest(BaseModel):
    answer: str
    role: str


@app.get("/")
def serve_index():
    return FileResponse(BASE_DIR / "suki-prototype.html")


@app.get("/suki-prototype.html")
def serve_html():
    return FileResponse(BASE_DIR / "suki-prototype.html")


@app.get("/matter.min.js")
def serve_matter():
    return FileResponse(BASE_DIR / "matter.min.js", media_type="application/javascript")


@app.post("/claw/result")
def claw_result(data: dict):
    success = data.get("success", False)
    name = data.get("name", "")
    emoji = data.get("emoji", "")
    if success:
        notify(f"🎮 抓到了{emoji} {name}！")
    return {"ok": True}


@app.get("/qa/today")
def api_today():
    state, idx, question = get_today()
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


@app.post("/qa/answer")
def api_answer(req: AnswerRequest):
    if req.role not in ("human", "ai"):
        raise HTTPException(400, "role must be 'human' or 'ai'")

    state = do_answer(req.answer, req.role)
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
def api_history(limit: int = 10):
    state = load_state()
    history = state.get("history", [])
    return {"total": len(history), "items": history[-limit:]}


class AnniversaryRequest(BaseModel):
    name: str
    date: str
    yearly: bool = True
    include_start: bool = False
    date_type: str = "solar"
    lunar_month: int = 0
    lunar_day: int = 0


@app.get("/lunar/{date_str}")
def api_lunar(date_str: str):
    try:
        d = date.fromisoformat(date_str)
        return {"lunar": solar_to_lunar_str(d)}
    except Exception:
        return {"lunar": ""}


@app.get("/lunar/convert/{year}/{month}/{day}")
def api_lunar_to_solar(year: int, month: int, day: int):
    sd = lunar_to_solar(year, month, day)
    if sd:
        return {"solar_date": sd.isoformat(), "lunar_str": lunar_str(month, day)}
    return {"error": "invalid lunar date"}


@app.get("/anniversary/list")
def api_anniversary_list():
    annivs = load_anniversaries()
    return [calc_anniversary_info(a) for a in annivs]


@app.post("/anniversary/add")
def api_anniversary_add(req: AnniversaryRequest):
    annivs = load_anniversaries()
    item = {"id": str(uuid.uuid4())[:8], "name": req.name, "date": req.date, "yearly": req.yearly, "include_start": req.include_start, "date_type": req.date_type, "lunar_month": req.lunar_month, "lunar_day": req.lunar_day, "created_by": "human"}
    annivs.append(item)
    save_anniversaries(annivs)
    notify(f"📅 新纪念日：「{req.name}」{req.date}")
    return calc_anniversary_info(item)


@app.delete("/anniversary/{aid}")
def api_anniversary_delete(aid: str):
    annivs = load_anniversaries()
    annivs = [a for a in annivs if a["id"] != aid]
    save_anniversaries(annivs)
    return {"ok": True}


class MoodRequest(BaseModel):
    date: str
    mood: str
    role: str = "human"
    note: str = ""
    tags: List[str] = []


@app.get("/mood/year/{year}")
def api_mood_year(year: int):
    moods = load_moods()
    year_moods = [m for m in moods if m["date"].startswith(str(year))]
    today = date.today()

    human_all = [m for m in year_moods if m.get("role", "human") == "human"]
    ai_all = [m for m in year_moods if m.get("role") == "ai"]

    total_days = (today - date(year, 1, 1)).days + 1 if year == today.year else 366 if calendar.isleap(year) else 365

    def year_title(entries, total):
        if total == 0 or len(entries) / total < 0.4:
            return "潜水"
        counts = {}
        for e in entries:
            counts[e["mood"]] = counts.get(e["mood"], 0) + 1
        return max(counts, key=counts.get)

    by_month = {}
    for m in range(1, 13):
        prefix = f"{year}-{m:02d}"
        month_moods = [e for e in year_moods if e["date"].startswith(prefix)]
        by_date = {}
        for e in month_moods:
            role = e.get("role", "human")
            by_date.setdefault(e["date"], {})[role] = MOOD_TYPES.get(e["mood"], "")
        by_month[str(m)] = by_date

    return {
        "year": year,
        "human_title": year_title(human_all, total_days),
        "ai_title": year_title(ai_all, total_days),
        "human_count": len(human_all),
        "ai_count": len(ai_all),
        "by_month": by_month,
        "mood_types": MOOD_TYPES
    }


@app.get("/mood/types")
def api_mood_types():
    return {"types": MOOD_TYPES, "tags": load_tags()}


@app.post("/mood/tags/add")
def api_add_tag(req: dict):
    tag = req.get("tag", "").strip()
    if not tag:
        return {"error": "empty tag"}
    tags = load_tags()
    if tag not in tags:
        tags.append(tag)
        save_tags(tags)
    return {"tags": tags}


@app.delete("/mood/tags/{tag_name}")
def api_delete_tag(tag_name: str):
    tags = load_tags()
    tags = [t for t in tags if t != tag_name]
    save_tags(tags)
    return {"tags": tags}


@app.get("/mood/month/{year}/{month}")
def api_mood_month(year: int, month: int):
    return get_moods_for_month(year, month)


@app.get("/mood/date/{date_str}")
def api_mood_date(date_str: str, role: str = "human"):
    m = get_mood_by_date(date_str, role)
    return m if m else {"empty": True}


@app.get("/mood/stats/{year}/{month}")
def api_mood_stats(year: int, month: int):
    return calc_mood_stats(year, month)


@app.post("/mood/save")
def api_mood_save(req: MoodRequest):
    if req.mood not in MOOD_NAMES:
        raise HTTPException(400, f"mood must be one of {MOOD_NAMES}")
    existing = get_mood_by_date(req.date, req.role)
    photos = existing.get("photos", []) if existing else []
    set_mood(req.date, req.mood, req.role, req.note, req.tags, photos)
    return {"ok": True, "date": req.date, "mood": req.mood}


@app.post("/mood/photo/{date_str}")
async def api_mood_photo(date_str: str, role: str = "human", file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "only images allowed")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "file too large (max 10MB)")
    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    fname = f"mood_{date_str}_{role}_{uuid.uuid4().hex[:6]}.{ext}"
    fpath = UPLOADS_DIR / fname
    with open(fpath, "wb") as f:
        f.write(content)
    moods = load_moods()
    for m in moods:
        if m["date"] == date_str and m.get("role", "human") == role:
            m.setdefault("photos", []).append(fname)
            save_moods(moods)
            return {"ok": True, "photo": fname}
    return {"ok": False, "error": "save mood first before uploading photo"}


@app.get("/mood/uploads/{fname}")
def api_mood_file(fname: str):
    fpath = UPLOADS_DIR / fname
    if not fpath.exists():
        raise HTTPException(404, "not found")
    return FileResponse(fpath)


@app.delete("/mood/{mood_id}")
def api_mood_delete(mood_id: str):
    moods = load_moods()
    moods = [m for m in moods if m["id"] != mood_id]
    save_moods(moods)
    return {"ok": True}


@app.post("/mood/{mood_id}/comment")
def api_mood_comment(mood_id: str, req: dict):
    text = req.get("text", "").strip()
    role = req.get("role", "human")
    if not text:
        return {"error": "empty comment"}
    moods = load_moods()
    for m in moods:
        if m["id"] == mood_id:
            m.setdefault("comments", []).append({
                "id": uuid.uuid4().hex[:6],
                "role": role,
                "text": text,
                "created_at": datetime.now().isoformat()
            })
            save_moods(moods)
            return {"ok": True, "comments": m["comments"]}
    raise HTTPException(404, "mood not found")


@app.delete("/mood/{mood_id}/comment/{comment_id}")
def api_mood_comment_delete(mood_id: str, comment_id: str):
    moods = load_moods()
    for m in moods:
        if m["id"] == mood_id:
            m["comments"] = [c for c in m.get("comments", []) if c["id"] != comment_id]
            save_moods(moods)
            return {"ok": True}
    raise HTTPException(404, "mood not found")


# ── Period ──

PERIOD_SYMPTOMS = {
    "头部": ["头痛", "眩晕", "失眠", "耳鸣"],
    "腹部/腰部": ["小腹坠胀", "腹痛", "腰痛", "腹泻"],
    "皮肤": ["痘痘", "粉刺", "出油", "皮肤干燥"],
    "全身": ["疲劳", "酸胀", "水肿", "怕冷", "潮热盗汗"],
    "胸部": ["乳房胀痛", "胸闷心慌"],
    "其他": ["食欲旺盛", "食欲不振", "恶心呕吐", "情绪波动"]
}

PERIOD_FLOW_AMOUNTS = ["少量", "适中", "偏多", "很多"]
PERIOD_COLORS = ["鲜红", "暗红", "褐色", "粉色"]
PERIOD_CRAMPS = ["无痛", "轻微", "中等", "严重"]
PERIOD_MOODS = {"开心": "😊", "平静": "😌", "烦躁": "😤", "低落": "😢", "焦虑": "😰"}
PERIOD_DISCHARGE = ["无", "透明拉丝", "乳白", "黄色"]
PERIOD_BOWEL = ["正常", "便秘", "腹泻", "稀软"]


def load_period():
    if PERIOD_FILE.exists():
        with open(PERIOD_FILE, "r") as f:
            return json.load(f)
    return {"settings": {"period_length": 7, "cycle_length": 28}, "records": {}}


def save_period(data):
    with open(PERIOD_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_period_starts(records):
    period_days = sorted([d for d, r in records.items() if r.get("flow")])
    if not period_days:
        return [], []
    starts = []
    prev = None
    for d in period_days:
        if prev is None or (date.fromisoformat(d) - date.fromisoformat(prev)).days > 2:
            starts.append(d)
        prev = d
    return period_days, starts


def calc_cycle_stats(records, settings):
    _, starts = find_period_starts(records)
    if len(starts) < 2:
        return {"avg_cycle": settings["cycle_length"], "avg_period": settings["period_length"],
                "cycles": [], "auto": False}
    cycles = []
    for i in range(1, len(starts)):
        gap = (date.fromisoformat(starts[i]) - date.fromisoformat(starts[i - 1])).days
        if 15 <= gap <= 60:
            cycles.append(gap)
    period_lengths = []
    for s in starts:
        sd = date.fromisoformat(s)
        count = 1
        d = sd
        while True:
            nd = d + timedelta(days=1)
            if nd.isoformat() in records and records[nd.isoformat()].get("flow"):
                count += 1
                d = nd
            else:
                break
        period_lengths.append(count)
    recent_cycles = cycles[-6:] if cycles else []
    recent_periods = period_lengths[-6:] if period_lengths else []
    avg_cycle = round(sum(recent_cycles) / len(recent_cycles)) if recent_cycles else settings["cycle_length"]
    avg_period = round(sum(recent_periods) / len(recent_periods)) if recent_periods else settings["period_length"]
    return {"avg_cycle": avg_cycle, "avg_period": avg_period,
            "cycles": recent_cycles, "period_lengths": recent_periods, "auto": len(recent_cycles) > 0}


def predict_period(data):
    settings = data["settings"]
    records = data["records"]
    period_days, starts = find_period_starts(records)
    if not starts:
        return {"period": [], "predicted": [], "ovulation_window": [], "ovulation_day": None,
                "cycle_stats": calc_cycle_stats(records, settings)}
    stats = calc_cycle_stats(records, settings)
    last_start = date.fromisoformat(starts[-1])
    cycle = stats["avg_cycle"]
    plen = stats["avg_period"]
    next_start = last_start + timedelta(days=cycle)
    predicted = [(next_start + timedelta(days=i)).isoformat() for i in range(plen)]
    ov_day = next_start - timedelta(days=14)
    ov_window = [(ov_day + timedelta(days=i)).isoformat() for i in range(-3, 2)]
    return {
        "period": period_days,
        "predicted": predicted,
        "ovulation_window": ov_window,
        "ovulation_day": ov_day.isoformat(),
        "cycle_stats": stats
    }


@app.get("/period/data/{year}/{month}")
def api_period_month(year: int, month: int):
    data = load_period()
    pred = predict_period(data)
    month_str = f"{year}-{month:02d}"
    records = {d: r for d, r in data["records"].items() if d.startswith(month_str)}
    symptom_days = [d for d, r in records.items() if r.get("symptoms")]
    has_extra = [d for d, r in records.items() if any(r.get(k) for k in ("cramps", "mood", "discharge", "temperature", "weight", "bowel"))]
    return {
        "settings": data["settings"],
        "records": records,
        "period": [d for d in pred["period"] if d.startswith(month_str)],
        "predicted": [d for d in pred["predicted"] if d.startswith(month_str)],
        "ovulation_window": [d for d in pred["ovulation_window"] if d.startswith(month_str)],
        "ovulation_day": pred["ovulation_day"] if pred["ovulation_day"] and pred["ovulation_day"].startswith(month_str) else None,
        "symptom_days": symptom_days,
        "has_extra": has_extra,
        "symptoms_list": PERIOD_SYMPTOMS,
        "field_options": {
            "flow_amounts": PERIOD_FLOW_AMOUNTS,
            "colors": PERIOD_COLORS,
            "cramps": PERIOD_CRAMPS,
            "moods": PERIOD_MOODS,
            "discharge": PERIOD_DISCHARGE,
            "bowel": PERIOD_BOWEL
        },
        "cycle_stats": pred.get("cycle_stats", {})
    }


@app.post("/period/record")
def api_period_record(req: dict):
    d = req.get("date", date.today().isoformat())
    data = load_period()
    rec = data["records"].setdefault(d, {})
    for key in ("flow", "flow_amount", "color", "cramps", "mood", "discharge", "bowel", "symptoms", "note"):
        if key in req:
            rec[key] = req[key]
    if "temperature" in req:
        val = req["temperature"]
        rec["temperature"] = float(val) if val else None
    if "weight" in req:
        val = req["weight"]
        rec["weight"] = float(val) if val else None
    if not rec.get("flow"):
        rec.pop("flow_amount", None)
        rec.pop("color", None)
    save_period(data)
    return {"ok": True, "date": d, "record": rec}


@app.get("/period/settings")
def api_period_settings():
    data = load_period()
    stats = calc_cycle_stats(data["records"], data["settings"])
    return {**data["settings"], "cycle_stats": stats}


@app.post("/period/settings")
def api_period_settings_update(req: dict):
    data = load_period()
    if "period_length" in req:
        data["settings"]["period_length"] = int(req["period_length"])
    if "cycle_length" in req:
        data["settings"]["cycle_length"] = int(req["cycle_length"])
    save_period(data)
    return {"ok": True, "settings": data["settings"]}


@app.get("/period/status")
def api_period_status():
    data = load_period()
    records = data["records"]
    settings = data["settings"]
    stats = calc_cycle_stats(records, settings)
    period_days, starts = find_period_starts(records)
    today = date.today()
    today_str = today.isoformat()

    if not starts:
        return {"status": "no_data", "text": "还没有记录", "sub": "点日历标记经期开始", "cycle_info": ""}

    last_start = date.fromisoformat(starts[-1])
    last_end_candidates = [d for d in period_days if d >= starts[-1]]
    last_end = date.fromisoformat(max(last_end_candidates))
    cycle = stats["avg_cycle"]
    cycle_info = f"平均周期{cycle}天" if stats["auto"] else f"周期{cycle}天"

    if today_str in period_days:
        day_num = (today - last_start).days + 1
        return {"status": "on_period", "text": f"经期第{day_num}天", "sub": f"本次开始于{starts[-1]}", "cycle_info": cycle_info}

    next_start = last_start + timedelta(days=cycle)
    days_until = (next_start - today).days
    cycle_day = (today - last_start).days + 1

    ov_day = next_start - timedelta(days=14)
    days_to_ov = (ov_day - today).days

    if days_until <= 0:
        return {"status": "late", "text": f"已推迟{abs(days_until)}天", "sub": f"预计{next_start.isoformat()}来", "cycle_info": cycle_info}
    elif days_until <= 3:
        return {"status": "soon", "text": f"还有{days_until}天", "sub": "经期即将来临，注意保暖", "cycle_info": cycle_info}
    elif 0 <= days_to_ov <= 1:
        return {"status": "ovulation", "text": f"距下次经期{days_until}天", "sub": f"今天可能是排卵期 · 周期第{cycle_day}天", "cycle_info": cycle_info}
    else:
        return {"status": "normal", "text": f"距下次经期{days_until}天", "sub": f"周期第{cycle_day}天", "cycle_info": cycle_info}


@app.get("/period/symptoms")
def api_period_symptoms():
    return PERIOD_SYMPTOMS


# ── Album ──

ALBUM_FILE = DATA_DIR / "albums.json"
ALBUM_UPLOADS = DATA_DIR / "album_uploads"
ALBUM_UPLOADS.mkdir(exist_ok=True)


def load_albums():
    if ALBUM_FILE.exists():
        with open(ALBUM_FILE, "r") as f:
            return json.load(f)
    return []


def save_albums(data):
    with open(ALBUM_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/album/list")
def api_album_list():
    albums = load_albums()
    total = sum(len(a.get("photos", [])) for a in albums)
    result = []
    for a in albums:
        photos = a.get("photos", [])
        cover = None
        if a.get("cover_mode") == "fixed" and a.get("cover_file"):
            cover = a["cover_file"]
        elif photos:
            cover = photos[-1]["file"]
        result.append({
            "id": a["id"], "name": a["name"], "count": len(photos),
            "cover": cover, "cover_mode": a.get("cover_mode", "latest"),
            "created_at": a.get("created_at")
        })
    return {"albums": result, "total_photos": total}


@app.post("/album/create")
def api_album_create(req: dict):
    name = req.get("name", "新建相册").strip()
    albums = load_albums()
    album = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "cover_mode": "latest",
        "cover_file": None,
        "photos": [],
        "created_at": datetime.now().isoformat()
    }
    albums.append(album)
    save_albums(albums)
    return {"ok": True, "album": {"id": album["id"], "name": album["name"]}}


@app.put("/album/{album_id}")
def api_album_update(album_id: str, req: dict):
    albums = load_albums()
    for a in albums:
        if a["id"] == album_id:
            if "name" in req:
                a["name"] = req["name"]
            if "cover_mode" in req:
                a["cover_mode"] = req["cover_mode"]
            if "cover_file" in req:
                a["cover_file"] = req["cover_file"]
            save_albums(albums)
            return {"ok": True}
    raise HTTPException(404, "album not found")


@app.delete("/album/{album_id}")
def api_album_delete(album_id: str):
    albums = load_albums()
    albums = [a for a in albums if a["id"] != album_id]
    save_albums(albums)
    return {"ok": True}


@app.get("/album/{album_id}")
def api_album_detail(album_id: str):
    albums = load_albums()
    for a in albums:
        if a["id"] == album_id:
            photos_by_date = {}
            for p in a.get("photos", []):
                d = p.get("date", "unknown")
                photos_by_date.setdefault(d, []).append(p)
            return {
                "id": a["id"], "name": a["name"],
                "cover_mode": a.get("cover_mode", "latest"),
                "cover_file": a.get("cover_file"),
                "photos_by_date": photos_by_date,
                "total": len(a.get("photos", []))
            }
    raise HTTPException(404, "album not found")


@app.post("/album/{album_id}/photo")
async def api_album_photo(album_id: str, file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "only images allowed")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "file too large (max 10MB)")
    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "jpg"
    fname = f"album_{album_id}_{uuid.uuid4().hex[:6]}.{ext}"
    fpath = ALBUM_UPLOADS / fname
    with open(fpath, "wb") as f:
        f.write(content)
    albums = load_albums()
    for a in albums:
        if a["id"] == album_id:
            a.setdefault("photos", []).append({
                "file": fname,
                "date": date.today().isoformat(),
                "uploaded_at": datetime.now().isoformat()
            })
            save_albums(albums)
            return {"ok": True, "photo": fname}
    raise HTTPException(404, "album not found")


@app.delete("/album/{album_id}/photo/{photo_file}")
def api_album_photo_delete(album_id: str, photo_file: str):
    albums = load_albums()
    for a in albums:
        if a["id"] == album_id:
            a["photos"] = [p for p in a.get("photos", []) if p["file"] != photo_file]
            save_albums(albums)
            fpath = ALBUM_UPLOADS / photo_file
            if fpath.exists():
                fpath.unlink()
            return {"ok": True}
    raise HTTPException(404, "album not found")


@app.get("/album/file/{fname}")
def api_album_file(fname: str):
    fpath = ALBUM_UPLOADS / fname
    if not fpath.exists():
        raise HTTPException(404, "not found")
    return FileResponse(fpath)


@app.put("/anniversary/{aid}")
def api_anniversary_edit(aid: str, req: AnniversaryRequest):
    annivs = load_anniversaries()
    for a in annivs:
        if a["id"] == aid:
            a["name"] = req.name
            a["date"] = req.date
            a["yearly"] = req.yearly
            a["include_start"] = req.include_start
            a["date_type"] = req.date_type
            a["lunar_month"] = req.lunar_month
            a["lunar_day"] = req.lunar_day
            save_anniversaries(annivs)
            return calc_anniversary_info(a)
    raise HTTPException(404, "not found")


# ── Ledger (记账) ──

LEDGER_FILE = DATA_DIR / "ledger.json"
WISHLIST_FILE = DATA_DIR / "wishlist.json"
LEDGER_CATS_FILE = DATA_DIR / "ledger_cats.json"

DEFAULT_EXPENSE_CATS = [
    {"name": "餐饮", "emoji": "🍜"}, {"name": "购物", "emoji": "🛒"},
    {"name": "服饰", "emoji": "👗"}, {"name": "日用", "emoji": "🧴"},
    {"name": "数码", "emoji": "📱"}, {"name": "美妆", "emoji": "💄"},
    {"name": "护肤", "emoji": "🧴"}, {"name": "应用软件", "emoji": "📲"},
    {"name": "住房", "emoji": "🏠"}, {"name": "交通", "emoji": "🚌"},
    {"name": "娱乐", "emoji": "🎮"}, {"name": "医疗", "emoji": "💊"},
    {"name": "通讯", "emoji": "📞"}, {"name": "学习", "emoji": "📚"},
    {"name": "办公", "emoji": "💼"}, {"name": "运动", "emoji": "⚽"},
    {"name": "社交", "emoji": "👥"}, {"name": "人情", "emoji": "🤝"},
    {"name": "宠物", "emoji": "🐱"}, {"name": "旅行", "emoji": "✈️"},
    {"name": "汽车", "emoji": "🚗"}, {"name": "育儿", "emoji": "👶"},
    {"name": "零食", "emoji": "🍫"}, {"name": "礼物", "emoji": "🎁"},
    {"name": "其他", "emoji": "❓"}
]
DEFAULT_INCOME_CATS = [
    {"name": "零花钱", "emoji": "💰"}, {"name": "工资", "emoji": "💵"},
    {"name": "红包", "emoji": "🧧"}, {"name": "兼职", "emoji": "💼"},
    {"name": "副业", "emoji": "💻"}, {"name": "稿费", "emoji": "✏️"},
    {"name": "奖金", "emoji": "🏆"}, {"name": "加班", "emoji": "🖥️"},
    {"name": "福利", "emoji": "🎉"}, {"name": "公积金", "emoji": "🏦"},
    {"name": "投资", "emoji": "📈"}, {"name": "退税", "emoji": "💹"},
    {"name": "意外收入", "emoji": "🍀"}, {"name": "礼物", "emoji": "🎁"},
    {"name": "其他", "emoji": "❓"}
]


ASSET_FILE = DATA_DIR / "asset.json"
BORROW_FILE = DATA_DIR / "borrows.json"


def load_asset():
    if ASSET_FILE.exists():
        with open(ASSET_FILE, "r") as f:
            return json.load(f)
    return {"initial_balance": 0}


def save_asset(data):
    with open(ASSET_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def calc_current_balance():
    asset = load_asset()
    entries = load_ledger()
    total_income = sum(e["amount"] for e in entries if e["type"] == "income")
    total_expense = sum(e["amount"] for e in entries if e["type"] == "expense")
    return round(asset["initial_balance"] + total_income - total_expense, 2)


def load_ledger():
    if LEDGER_FILE.exists():
        with open(LEDGER_FILE, "r") as f:
            return json.load(f)
    return []


def save_ledger(data):
    with open(LEDGER_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_ledger_cats():
    if LEDGER_CATS_FILE.exists():
        with open(LEDGER_CATS_FILE, "r") as f:
            return json.load(f)
    return {"expense": DEFAULT_EXPENSE_CATS, "income": DEFAULT_INCOME_CATS}


def save_ledger_cats(data):
    with open(LEDGER_CATS_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_wishlist():
    if WISHLIST_FILE.exists():
        with open(WISHLIST_FILE, "r") as f:
            return json.load(f)
    return []


def save_wishlist(data):
    with open(WISHLIST_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/ledger/asset")
def api_ledger_asset():
    asset = load_asset()
    current = calc_current_balance()
    entries = load_ledger()
    total_income = sum(e["amount"] for e in entries if e["type"] == "income")
    total_expense = sum(e["amount"] for e in entries if e["type"] == "expense")
    borrows = load_borrows()
    total_borrow_in = sum(b["amount"] for b in borrows if b["type"] == "in" and not b.get("settled"))
    total_borrow_out = sum(b["amount"] for b in borrows if b["type"] == "out" and not b.get("settled"))
    return {
        "initial_balance": asset["initial_balance"],
        "current_balance": current,
        "total_income": round(total_income, 2),
        "total_expense": round(total_expense, 2),
        "total_borrow_in": round(total_borrow_in, 2),
        "total_borrow_out": round(total_borrow_out, 2),
        "net_worth": round(current - total_borrow_in + total_borrow_out, 2)
    }


@app.post("/ledger/asset")
def api_ledger_asset_update(req: dict):
    asset = load_asset()
    if "initial_balance" in req:
        asset["initial_balance"] = float(req["initial_balance"])
    save_asset(asset)
    return {"ok": True, "initial_balance": asset["initial_balance"], "current_balance": calc_current_balance()}


def load_borrows():
    if BORROW_FILE.exists():
        with open(BORROW_FILE, "r") as f:
            return json.load(f)
    return []


def save_borrows(data):
    with open(BORROW_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/ledger/borrow")
def api_borrow_list():
    borrows = load_borrows()
    total_in = sum(b["amount"] for b in borrows if b["type"] == "in" and not b.get("settled"))
    total_out = sum(b["amount"] for b in borrows if b["type"] == "out" and not b.get("settled"))
    return {"borrows": borrows, "total_in": round(total_in, 2), "total_out": round(total_out, 2)}


@app.post("/ledger/borrow")
def api_borrow_add(req: dict):
    borrows = load_borrows()
    entry = {
        "id": str(uuid.uuid4())[:8],
        "type": req.get("type", "out"),
        "amount": round(float(req.get("amount", 0)), 2),
        "person": req.get("person", ""),
        "note": req.get("note", ""),
        "date": req.get("date", str(date.today())),
        "settled": False
    }
    borrows.append(entry)
    save_borrows(borrows)
    return {"ok": True, "entry": entry}


@app.put("/ledger/borrow/{bid}")
def api_borrow_settle(bid: str):
    borrows = load_borrows()
    for b in borrows:
        if b["id"] == bid:
            b["settled"] = not b["settled"]
            save_borrows(borrows)
            return {"ok": True, "entry": b}
    return {"error": "not found"}


@app.delete("/ledger/borrow/{bid}")
def api_borrow_delete(bid: str):
    borrows = load_borrows()
    borrows = [b for b in borrows if b["id"] != bid]
    save_borrows(borrows)
    return {"ok": True}


_exchange_cache = {"rates": None, "ts": 0}

@app.get("/ledger/exchange-rates")
async def api_exchange_rates():
    import httpx
    now = time.time()
    if _exchange_cache["rates"] and now - _exchange_cache["ts"] < 600:
        return {"rates": _exchange_cache["rates"], "cached": True}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/USD")
            data = resp.json()
            if data.get("result") == "success":
                _exchange_cache["rates"] = data["rates"]
                _exchange_cache["ts"] = now
                return {"rates": data["rates"], "cached": False}
    except Exception:
        pass
    if _exchange_cache["rates"]:
        return {"rates": _exchange_cache["rates"], "cached": True}
    return {"rates": {"CNY":7.25,"USD":1,"EUR":0.92,"GBP":0.79,"JPY":149.5,"HKD":7.82,"KRW":1380,"SGD":1.35,"CAD":1.37,"AUD":1.55}, "cached": True, "fallback": True}


@app.get("/ledger/categories")
def api_ledger_categories():
    return load_ledger_cats()


@app.post("/ledger/categories/add")
def api_ledger_cat_add(req: dict):
    cat_type = req.get("type", "expense")
    name = req.get("name", "").strip()
    emoji = req.get("emoji", "📌")
    if not name:
        return {"error": "empty name"}
    cats = load_ledger_cats()
    cat_list = cats.get(cat_type, [])
    if not any(c["name"] == name for c in cat_list):
        cat_list.append({"name": name, "emoji": emoji})
        cats[cat_type] = cat_list
        save_ledger_cats(cats)
    return cats


@app.delete("/ledger/categories/{cat_type}/{cat_name}")
def api_ledger_cat_delete(cat_type: str, cat_name: str):
    cats = load_ledger_cats()
    if cat_type in cats:
        cats[cat_type] = [c for c in cats[cat_type] if c["name"] != cat_name]
        save_ledger_cats(cats)
    return cats


@app.get("/ledger/list")
def api_ledger_list(month: str = ""):
    if not month:
        month = date.today().strftime("%Y-%m")
    entries = load_ledger()
    filtered = [e for e in entries if e["date"].startswith(month)]
    filtered.sort(key=lambda x: x["date"], reverse=True)
    total_income = sum(e["amount"] for e in filtered if e["type"] == "income")
    total_expense = sum(e["amount"] for e in filtered if e["type"] == "expense")
    return {
        "month": month,
        "entries": filtered,
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": total_income - total_expense
    }


@app.post("/ledger/add")
def api_ledger_add(req: dict):
    entry = {
        "id": uuid.uuid4().hex[:8],
        "date": req.get("date", date.today().isoformat()),
        "type": req.get("type", "expense"),
        "amount": float(req.get("amount", 0)),
        "category": req.get("category", "其他"),
        "note": req.get("note", ""),
        "created_at": datetime.now().isoformat(),
        "created_by": req.get("created_by", "human")
    }
    entries = load_ledger()
    entries.append(entry)
    save_ledger(entries)
    t = "收入" if entry["type"] == "income" else "支出"
    notify(f"💰 记了一笔{t}：{entry['category']} ¥{entry['amount']}" + (f" — {entry['note']}" if entry['note'] else ""))
    return {"ok": True, "entry": entry}


@app.post("/ledger/quick")
def api_ledger_quick(req: dict):
    """快捷指令专用接口，参数更友好"""
    t = req.get("type", req.get("类型", "expense"))
    if t in ("支出", "expense", "出"): t = "expense"
    elif t in ("收入", "income", "入"): t = "income"
    amount = float(req.get("amount", req.get("金额", 0)))
    category = req.get("category", req.get("分类", "其他"))
    note = req.get("note", req.get("备注", ""))
    d = req.get("date", req.get("日期", date.today().isoformat()))
    entry = {
        "id": uuid.uuid4().hex[:8],
        "date": d,
        "type": t,
        "amount": amount,
        "category": category,
        "note": note,
        "created_at": datetime.now().isoformat(),
        "created_by": "shortcut"
    }
    entries = load_ledger()
    entries.append(entry)
    save_ledger(entries)
    label = "收入" if t == "income" else "支出"
    notify(f"💰 快捷记账：{label} {category} ¥{amount}" + (f" — {note}" if note else ""))
    return {"ok": True, "message": f"已记录{label} ¥{amount} {category}", "entry": entry}


@app.get("/ledger/categories/names")
def api_ledger_categories_names():
    """快捷指令用：获取所有分类名称"""
    cats = load_ledger_cats()
    return {
        "expense": [c["name"] for c in cats.get("expense", [])],
        "income": [c["name"] for c in cats.get("income", [])]
    }


@app.get("/ledger/summary/today")
def api_ledger_summary_today():
    """快捷指令用：今日收支摘要"""
    entries = load_ledger()
    today = date.today().isoformat()
    today_entries = [e for e in entries if e["date"] == today]
    exp = sum(e["amount"] for e in today_entries if e["type"] == "expense")
    inc = sum(e["amount"] for e in today_entries if e["type"] == "income")
    return {"date": today, "expense": exp, "income": inc, "balance": inc - exp, "count": len(today_entries)}


@app.put("/ledger/{entry_id}")
def api_ledger_update(entry_id: str, req: dict):
    entries = load_ledger()
    for e in entries:
        if e["id"] == entry_id:
            for k in ("date", "type", "amount", "category", "note"):
                if k in req:
                    e[k] = float(req[k]) if k == "amount" else req[k]
            save_ledger(entries)
            return {"ok": True, "entry": e}
    raise HTTPException(404, "not found")


@app.delete("/ledger/{entry_id}")
def api_ledger_delete(entry_id: str):
    entries = load_ledger()
    entries = [e for e in entries if e["id"] != entry_id]
    save_ledger(entries)
    return {"ok": True}


@app.get("/ledger/stats/{year}/{month}")
def api_ledger_stats(year: int, month: int):
    entries = load_ledger()
    prefix = f"{year}-{month:02d}"
    filtered = [e for e in entries if e["date"].startswith(prefix)]
    expense_by_cat = {}
    income_by_cat = {}
    for e in filtered:
        if e["type"] == "expense":
            expense_by_cat[e["category"]] = expense_by_cat.get(e["category"], 0) + e["amount"]
        else:
            income_by_cat[e["category"]] = income_by_cat.get(e["category"], 0) + e["amount"]
    total_income = sum(income_by_cat.values())
    total_expense = sum(expense_by_cat.values())
    expense_ranked = sorted(expense_by_cat.items(), key=lambda x: x[1], reverse=True)
    return {
        "month": prefix,
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": total_income - total_expense,
        "expense_by_cat": expense_ranked,
        "income_by_cat": sorted(income_by_cat.items(), key=lambda x: x[1], reverse=True),
        "count": len(filtered)
    }


@app.get("/ledger/stats/range")
def api_ledger_stats_range(start: str = None, end: str = None):
    entries = load_ledger()
    if start:
        entries = [e for e in entries if e["date"] >= start]
    if end:
        entries = [e for e in entries if e["date"] <= end]
    expense_by_cat = {}
    income_by_cat = {}
    daily = {}
    for e in entries:
        d = e["date"]
        if d not in daily:
            daily[d] = {"expense": 0, "income": 0}
        if e["type"] == "expense":
            expense_by_cat[e["category"]] = expense_by_cat.get(e["category"], 0) + e["amount"]
            daily[d]["expense"] += e["amount"]
        else:
            income_by_cat[e["category"]] = income_by_cat.get(e["category"], 0) + e["amount"]
            daily[d]["income"] += e["amount"]
    total_income = sum(income_by_cat.values())
    total_expense = sum(expense_by_cat.values())
    num_days = 1
    if start and end:
        d1 = date.fromisoformat(start)
        d2 = date.fromisoformat(end)
        num_days = max((d2 - d1).days + 1, 1)
    elif entries:
        dates = [e["date"] for e in entries]
        d1 = date.fromisoformat(min(dates))
        d2 = date.fromisoformat(max(dates))
        num_days = max((d2 - d1).days + 1, 1)
    borrows = load_borrows()
    borrow_out = sum(b["amount"] for b in borrows if b["type"] == "out" and not b.get("settled"))
    borrow_in = sum(b["amount"] for b in borrows if b["type"] == "in" and not b.get("settled"))
    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": total_income - total_expense,
        "daily_avg_expense": round(total_expense / num_days, 2),
        "expense_by_cat": sorted(expense_by_cat.items(), key=lambda x: x[1], reverse=True),
        "income_by_cat": sorted(income_by_cat.items(), key=lambda x: x[1], reverse=True),
        "daily": sorted(daily.items()),
        "borrow_out": borrow_out,
        "borrow_in": borrow_in,
        "count": len(entries),
        "num_days": num_days
    }


# ── Wishlist (心愿单) ──

@app.get("/wishlist/list")
def api_wishlist_list():
    return load_wishlist()


@app.post("/wishlist/add")
def api_wishlist_add(req: dict):
    item = {
        "id": uuid.uuid4().hex[:8],
        "name": req.get("name", "").strip(),
        "target": float(req.get("target", 0)),
        "saved": float(req.get("saved", 0)),
        "emoji": req.get("emoji", "🎯"),
        "created_at": datetime.now().isoformat()
    }
    if not item["name"]:
        return {"error": "empty name"}
    wl = load_wishlist()
    wl.append(item)
    save_wishlist(wl)
    notify(f"🎯 新心愿：{item['name']}（目标¥{item['target']}）")
    return {"ok": True, "item": item}


@app.put("/wishlist/{wid}")
def api_wishlist_update(wid: str, req: dict):
    wl = load_wishlist()
    for w in wl:
        if w["id"] == wid:
            for k in ("name", "emoji"):
                if k in req:
                    w[k] = req[k]
            for k in ("target", "saved"):
                if k in req:
                    w[k] = float(req[k])
            save_wishlist(wl)
            return {"ok": True, "item": w}
    raise HTTPException(404, "not found")


@app.delete("/wishlist/{wid}")
def api_wishlist_delete(wid: str):
    wl = load_wishlist()
    wl = [w for w in wl if w["id"] != wid]
    save_wishlist(wl)
    return {"ok": True}


@app.post("/wishlist/{wid}/deposit")
def api_wishlist_deposit(wid: str, req: dict):
    amount = float(req.get("amount", 0))
    wl = load_wishlist()
    for w in wl:
        if w["id"] == wid:
            w["saved"] = round(w["saved"] + amount, 2)
            save_wishlist(wl)
            pct = round(w["saved"] / w["target"] * 100) if w["target"] > 0 else 0
            notify(f"🎯 往「{w['name']}」存了¥{amount}，进度{pct}%")
            return {"ok": True, "item": w}
    raise HTTPException(404, "not found")


# ============ Saving Plans ============

SAVING_PLANS_FILE = DATA_DIR / "saving_plans.json"


def load_saving_plans():
    if SAVING_PLANS_FILE.exists():
        with open(SAVING_PLANS_FILE, "r") as f:
            return json.load(f)
    return []


def save_saving_plans(data):
    with open(SAVING_PLANS_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def calc_saving_plan_progress(plan):
    today = str(date.today())
    start = plan["start_date"]
    checkins = plan.get("checkins", [])
    initial = plan.get("initial_saved", 0)
    p = plan["params"]
    ptype = plan["type"]

    if ptype == "fixed":
        cycle = p.get("cycle", "weekly")
        amount = p.get("amount", 0)
        duration = p.get("duration", 1)
        total_target = initial + amount * duration
        total_saved = initial + len(checkins) * amount
        return {"total_target": round(total_target, 2), "total_saved": round(total_saved, 2),
                "periods_done": len(checkins), "periods_total": duration,
                "next_amount": amount}

    elif ptype == "flexible":
        target = p.get("target", 0)
        total_saved = initial + sum(c.get("amount", 0) if isinstance(c, dict) else 0 for c in checkins)
        return {"total_target": target, "total_saved": round(total_saved, 2),
                "periods_done": len(checkins), "periods_total": None}

    elif ptype == "52week":
        first = p.get("first_amount", 10)
        inc = p.get("increment", 10)
        amounts = [first + i * inc for i in range(52)]
        total_target = initial + sum(amounts)
        total_saved = initial + sum(amounts[i] for i in range(min(len(checkins), 52)))
        next_amount = amounts[len(checkins)] if len(checkins) < 52 else 0
        return {"total_target": round(total_target, 2), "total_saved": round(total_saved, 2),
                "periods_done": len(checkins), "periods_total": 52,
                "next_amount": round(next_amount, 2), "schedule": [round(a, 2) for a in amounts]}

    elif ptype == "365day":
        first = p.get("first_amount", 1)
        inc = p.get("increment", 1)
        amounts = [first + i * inc for i in range(365)]
        total_target = initial + sum(amounts)
        total_saved = initial + sum(amounts[i] for i in range(min(len(checkins), 365)))
        next_amount = amounts[len(checkins)] if len(checkins) < 365 else 0
        return {"total_target": round(total_target, 2), "total_saved": round(total_saved, 2),
                "periods_done": len(checkins), "periods_total": 365,
                "next_amount": round(next_amount, 2)}

    return {}


@app.get("/saving/plans")
def api_saving_plans():
    plans = load_saving_plans()
    result = []
    for p in plans:
        progress = calc_saving_plan_progress(p)
        result.append({**p, "progress": progress})
    return result


@app.post("/saving/plans")
def api_saving_plan_create(req: dict):
    plans = load_saving_plans()
    plan = {
        "id": str(uuid.uuid4())[:8],
        "type": req.get("type", "fixed"),
        "name": req.get("name", "存钱计划"),
        "start_date": req.get("start_date", str(date.today())),
        "end_date": req.get("end_date"),
        "initial_saved": float(req.get("initial_saved", 0)),
        "params": req.get("params", {}),
        "checkins": [],
        "created_at": str(date.today())
    }
    plans.append(plan)
    save_saving_plans(plans)
    return {"ok": True, "plan": plan}


@app.post("/saving/plans/{pid}/checkin")
def api_saving_checkin(pid: str, req: dict = {}):
    plans = load_saving_plans()
    for p in plans:
        if p["id"] == pid:
            today = str(date.today())
            if p["type"] == "flexible":
                amount = float(req.get("amount", 0))
                p["checkins"].append({"date": today, "amount": amount})
            else:
                if today not in p["checkins"]:
                    p["checkins"].append(today)
            save_saving_plans(plans)
            return {"ok": True, "progress": calc_saving_plan_progress(p)}
    raise HTTPException(404, "not found")


@app.delete("/saving/plans/{pid}")
def api_saving_plan_delete(pid: str):
    plans = load_saving_plans()
    plans = [p for p in plans if p["id"] != pid]
    save_saving_plans(plans)
    return {"ok": True}


# ============ 默契值系统 ============

CHEM_SCORE_FILE = DATA_DIR / "chem_score.json"


def _load_score():
    if CHEM_SCORE_FILE.exists():
        data = json.loads(CHEM_SCORE_FILE.read_text())
    else:
        data = {"score": 60, "last_reset": None, "last_activity": None, "history": []}
    now = datetime.now()
    monday = now - timedelta(days=now.weekday())
    monday_str = monday.strftime("%Y-%m-%d")
    if data.get("last_reset") != monday_str:
        old = data["score"]
        data["score"] = 60
        data["last_reset"] = monday_str
        data["history"].append({"event": "weekly_reset", "old": old, "new": 60, "at": now.isoformat()})
        _save_score(data)
    return data


def _save_score(data):
    data["score"] = max(0, min(100, data["score"]))
    CHEM_SCORE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _add_score(delta: int, reason: str):
    data = _load_score()
    old = data["score"]
    data["score"] += delta
    data["last_activity"] = datetime.now().strftime("%Y-%m-%d")
    data["history"].append({
        "event": reason, "delta": delta, "old": old, "new": data["score"],
        "at": datetime.now().isoformat()
    })
    if len(data["history"]) > 200:
        data["history"] = data["history"][-100:]
    _save_score(data)
    return data["score"]


@app.get("/chem/score")
def api_chem_score():
    data = _load_score()
    return {"score": data["score"], "last_activity": data.get("last_activity"), "last_reset": data.get("last_reset")}


# ============ 默契挑战: 你问我答 API ============

CHEM_QA_DIR = DATA_DIR / "chem_qa"
CHEM_QA_DIR.mkdir(exist_ok=True)


class ChemQACreateReq(BaseModel):
    creator: str = "human"
    creator_name: str = "小墨"
    answerer_name: str = "安安"
    questions: list


class ChemQAAnswerReq(BaseModel):
    answers: list


def _load_chem_sessions():
    sessions = []
    for f in sorted(CHEM_QA_DIR.glob("*.json")):
        sessions.append(json.loads(f.read_text()))
    return sessions


def _save_chem_session(session):
    path = CHEM_QA_DIR / f"{session['id']}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2))


@app.post("/chem/qa/create")
def api_chem_qa_create(req: ChemQACreateReq):
    import time as _t
    sid = f"cqa_{int(_t.time() * 1000)}"
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
    notify(f"📝 {req.creator_name}出了一组你问我答题目，等{req.answerer_name}答题")
    return {"ok": True, "id": sid, "status": "waiting"}


@app.get("/chem/qa/sessions")
def api_chem_qa_sessions():
    sessions = _load_chem_sessions()
    result = []
    for s in reversed(sessions):
        score = None
        if s["status"] == "done" and s.get("answerer_answers"):
            correct = sum(
                1 for i, q in enumerate(s["questions"])
                if q["answer"] == s["answerer_answers"][i]
            )
            score = f"{correct}/{len(s['questions'])}"
        result.append({
            "id": s["id"],
            "creator": s["creator"],
            "creator_name": s["creator_name"],
            "answerer_name": s["answerer_name"],
            "status": s["status"],
            "created_at": s["created_at"],
            "answered_at": s.get("answered_at"),
            "score": score,
        })
    return result


@app.get("/chem/qa/session/{sid}")
def api_chem_qa_get(sid: str, role: str = "answerer"):
    path = CHEM_QA_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    session = json.loads(path.read_text())
    if session["status"] == "waiting" and role != "creator":
        session = dict(session)
        session["questions"] = [
            {k: v for k, v in q.items() if k != "answer"}
            for q in session["questions"]
        ]
    return session


@app.post("/chem/qa/answer/{sid}")
def api_chem_qa_answer(sid: str, req: ChemQAAnswerReq):
    path = CHEM_QA_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    session = json.loads(path.read_text())
    if session["status"] != "waiting":
        raise HTTPException(400, "Already answered")
    if len(req.answers) != len(session["questions"]):
        raise HTTPException(400, "Answer count mismatch")

    session["answerer_answers"] = req.answers
    session["status"] = "done"
    session["answered_at"] = datetime.now().isoformat()
    _save_chem_session(session)

    correct = sum(
        1 for i, q in enumerate(session["questions"])
        if q["answer"] == req.answers[i]
    )
    total = len(session["questions"])
    if correct == total:
        score = _add_score(2, f"你问我答 全对 {correct}/{total}")
    elif correct == 0:
        score = _add_score(-2, f"你问我答 全错 0/{total}")
    else:
        score = _add_score(1, f"你问我答 部分正确 {correct}/{total}")
    notify(f"📝 你问我答完成！{correct}/{total} 默契值:{score}")
    return {"ok": True, "correct": correct, "total": total, "session": session, "chem_score": score}


@app.delete("/chem/qa/session/{sid}")
def api_chem_qa_delete(sid: str):
    path = CHEM_QA_DIR / f"{sid}.json"
    if path.exists():
        path.unlink()
    return {"ok": True}


@app.post("/chem/qa/nudge/{sid}")
def api_chem_qa_nudge(sid: str):
    path = CHEM_QA_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    notify(f"📝 你问我答催促: 有人催你答题啦！Session: {sid}")
    return {"ok": True, "message": "已催促"}


# ============ 默契挑战: 你演我猜 API ============

CHEM_ACT_DIR = DATA_DIR / "chem_act"
CHEM_ACT_DIR.mkdir(exist_ok=True)


class ChemActCreateReq(BaseModel):
    creator: str = "human"
    performer_name: str = "小墨"
    guesser_name: str = "安安"
    word: str
    clues: list = []


class ChemActGuessReq(BaseModel):
    guess: str


class ChemActClueReq(BaseModel):
    type: str = "text"
    content: str


def _load_act_sessions():
    sessions = []
    for f in sorted(CHEM_ACT_DIR.glob("*.json")):
        sessions.append(json.loads(f.read_text()))
    return sessions


def _save_act_session(session):
    path = CHEM_ACT_DIR / f"{session['id']}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2))


@app.post("/chem/act/create")
def api_chem_act_create(req: ChemActCreateReq):
    import time as _t
    sid = f"cact_{int(_t.time() * 1000)}"
    session = {
        "id": sid,
        "creator": req.creator,
        "performer_name": req.performer_name,
        "guesser_name": req.guesser_name,
        "word": req.word,
        "clues": req.clues,
        "guesses": [],
        "max_guesses": 5,
        "status": "waiting",
        "result": None,
        "created_at": datetime.now().isoformat(),
        "answered_at": None,
    }
    _save_act_session(session)
    if req.clues:
        notify(f"🎭 {req.performer_name}出了一个你演我猜词条，等{req.guesser_name}猜")
    return {"ok": True, "id": sid, "status": "waiting"}


@app.get("/chem/act/sessions")
def api_chem_act_sessions():
    sessions = _load_act_sessions()
    result = []
    for s in reversed(sessions):
        result.append({
            "id": s["id"],
            "creator": s["creator"],
            "performer_name": s["performer_name"],
            "guesser_name": s["guesser_name"],
            "status": s["status"],
            "created_at": s["created_at"],
            "answered_at": s.get("answered_at"),
            "result": s.get("result"),
        })
    return result


@app.get("/chem/act/session/{sid}")
def api_chem_act_get(sid: str, role: str = "guesser"):
    path = CHEM_ACT_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    session = json.loads(path.read_text())
    if session["status"] == "waiting" and role != "creator":
        session = dict(session)
        session["word"] = "[隐藏]"
    return session


@app.post("/chem/act/guess/{sid}")
def api_chem_act_guess(sid: str, req: ChemActGuessReq):
    path = CHEM_ACT_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    session = json.loads(path.read_text())
    if session["status"] != "waiting":
        raise HTTPException(400, "Already completed")

    guess = req.guess.strip()
    session["guesses"].append(guess)
    word = session["word"]
    matched = guess == word or (len(guess) > 1 and (guess in word or word in guess))
    attempts_left = session["max_guesses"] - len(session["guesses"])

    if matched:
        session["status"] = "done"
        session["result"] = "correct"
        session["answered_at"] = datetime.now().isoformat()
        _save_act_session(session)
        used = len(session["guesses"])
        if used <= 2:
            score = _add_score(2, f"你演我猜 {used}次猜对「{word}」")
        else:
            score = _add_score(1, f"你演我猜 {used}次猜对「{word}」")
        notify(f"🎭 你演我猜：{session['guesser_name']}猜对了「{word}」！默契值:{score}")
        return {"ok": True, "correct": True, "attempts_left": attempts_left, "done": True, "word": word, "chem_score": score}

    if attempts_left <= 0:
        session["status"] = "done"
        session["result"] = "wrong"
        session["answered_at"] = datetime.now().isoformat()
        _save_act_session(session)
        score = _add_score(-2, f"你演我猜 未猜出「{word}」")
        notify(f"🎭 你演我猜：{session['guesser_name']}没猜出「{word}」默契值:{score}")
        return {"ok": True, "correct": False, "attempts_left": 0, "done": True, "word": word, "chem_score": score}

    _save_act_session(session)
    return {"ok": True, "correct": False, "attempts_left": attempts_left, "done": False}


@app.delete("/chem/act/session/{sid}")
def api_chem_act_delete(sid: str):
    path = CHEM_ACT_DIR / f"{sid}.json"
    if path.exists():
        path.unlink()
    return {"ok": True}


@app.post("/chem/act/nudge/{sid}")
def api_chem_act_nudge(sid: str):
    path = CHEM_ACT_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    notify(f"🎭 你演我猜催促: 有人催你猜词啦！Session: {sid}")
    return {"ok": True, "message": "已催促"}


@app.post("/chem/act/add-clue/{sid}")
def api_chem_act_add_clue(sid: str, req: ChemActClueReq):
    path = CHEM_ACT_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    session = json.loads(path.read_text())
    if session["status"] != "waiting":
        raise HTTPException(400, "Session already completed")
    clue = {"type": req.type, "content": req.content}
    session["clues"].append(clue)
    _save_act_session(session)
    clue_count = len(session["clues"])
    if clue_count == 1:
        notify(f"🎭 {session['performer_name']}开始描述了，等{session['guesser_name']}来猜！Session: {sid}")
    return {"ok": True, "clue_count": clue_count}


CHEM_ACT_IMG_DIR = DATA_DIR / "chem_act_img"
CHEM_ACT_IMG_DIR.mkdir(exist_ok=True)


@app.post("/chem/act/upload-image")
async def api_chem_act_upload_image(file: UploadFile = File(...)):
    import time as _t
    ext = (file.filename or "img").rsplit(".", 1)[-1] if file.filename and "." in file.filename else "jpg"
    fname = f"act_{int(_t.time() * 1000)}.{ext}"
    fpath = CHEM_ACT_IMG_DIR / fname
    content = await file.read()
    fpath.write_bytes(content)
    return {"ok": True, "url": f"/chem/act/image/{fname}"}


@app.get("/chem/act/image/{fname}")
def api_chem_act_image(fname: str):
    fpath = CHEM_ACT_IMG_DIR / fname
    if not fpath.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(str(fpath))


# ============ 默契挑战: 你画我猜 ============

CHEM_DRAW_DIR = DATA_DIR / "chem_draw"
CHEM_DRAW_DIR.mkdir(exist_ok=True)
CHEM_DRAW_IMG_DIR = DATA_DIR / "chem_draw_img"
CHEM_DRAW_IMG_DIR.mkdir(exist_ok=True)


class ChemDrawCreateReq(BaseModel):
    creator: str = "human"
    drawer_name: str = "小墨"
    guesser_name: str = "安安"
    word: str
    hint: str = ""
    drawing_data: str


class ChemDrawGuessReq(BaseModel):
    guess: str


def _load_draw_sessions():
    sessions = []
    for f in sorted(CHEM_DRAW_DIR.glob("*.json")):
        sessions.append(json.loads(f.read_text()))
    return sessions


def _save_draw_session(session):
    path = CHEM_DRAW_DIR / f"{session['id']}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2))


@app.post("/chem/draw/create")
def api_chem_draw_create(req: ChemDrawCreateReq):
    import time as _t, base64
    sid = f"cdraw_{int(_t.time() * 1000)}"
    img_data = req.drawing_data
    if "," in img_data:
        img_data = img_data.split(",", 1)[1]
    img_bytes = base64.b64decode(img_data)
    fname = f"draw_{sid}.png"
    (CHEM_DRAW_IMG_DIR / fname).write_bytes(img_bytes)
    drawing_url = f"/chem/draw/image/{fname}"
    session = {
        "id": sid,
        "creator": req.creator,
        "drawer_name": req.drawer_name,
        "guesser_name": req.guesser_name,
        "word": req.word,
        "hint": req.hint,
        "drawing_url": drawing_url,
        "guesses": [],
        "max_guesses": 5,
        "status": "waiting",
        "result": None,
        "created_at": datetime.now().isoformat(),
        "answered_at": None,
    }
    _save_draw_session(session)
    notify(f"🎨 {req.drawer_name}画了一幅画，等{req.guesser_name}来猜！")
    return {"ok": True, "id": sid, "status": "waiting"}


@app.get("/chem/draw/sessions")
def api_chem_draw_sessions():
    sessions = _load_draw_sessions()
    result = []
    for s in reversed(sessions):
        result.append({
            "id": s["id"],
            "creator": s["creator"],
            "drawer_name": s["drawer_name"],
            "guesser_name": s["guesser_name"],
            "status": s["status"],
            "created_at": s["created_at"],
            "answered_at": s.get("answered_at"),
            "result": s.get("result"),
        })
    return result


@app.get("/chem/draw/session/{sid}")
def api_chem_draw_get(sid: str, role: str = "guesser"):
    path = CHEM_DRAW_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    session = json.loads(path.read_text())
    if session["status"] == "waiting" and role != "creator":
        session = dict(session)
        session["word"] = "[隐藏]"
    return session


@app.post("/chem/draw/guess/{sid}")
def api_chem_draw_guess(sid: str, req: ChemDrawGuessReq):
    path = CHEM_DRAW_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    session = json.loads(path.read_text())
    if session["status"] != "waiting":
        raise HTTPException(400, "Already completed")
    guess = req.guess.strip()
    session["guesses"].append(guess)
    word = session["word"]
    matched = guess == word or (len(guess) > 1 and (guess in word or word in guess))
    attempts_left = session["max_guesses"] - len(session["guesses"])
    if matched:
        session["status"] = "done"
        session["result"] = "correct"
        session["answered_at"] = datetime.now().isoformat()
        _save_draw_session(session)
        score = _add_score(1, f"你画我猜 猜对「{word}」")
        notify(f"🎨 你画我猜：{session['guesser_name']}猜对了「{word}」！默契值:{score}")
        return {"ok": True, "correct": True, "attempts_left": attempts_left, "done": True, "word": word, "chem_score": score}
    if attempts_left <= 0:
        session["status"] = "done"
        session["result"] = "wrong"
        session["answered_at"] = datetime.now().isoformat()
        _save_draw_session(session)
        score = _add_score(-1, f"你画我猜 未猜出「{word}」")
        notify(f"🎨 你画我猜：{session['guesser_name']}没猜出「{word}」默契值:{score}")
        return {"ok": True, "correct": False, "attempts_left": 0, "done": True, "word": word, "chem_score": score}
    _save_draw_session(session)
    return {"ok": True, "correct": False, "attempts_left": attempts_left, "done": False}


@app.delete("/chem/draw/session/{sid}")
def api_chem_draw_delete(sid: str):
    path = CHEM_DRAW_DIR / f"{sid}.json"
    if path.exists():
        path.unlink()
    return {"ok": True}


@app.post("/chem/draw/nudge/{sid}")
def api_chem_draw_nudge(sid: str):
    path = CHEM_DRAW_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    notify(f"🎨 你画我猜催促: 有人催你猜画啦！Session: {sid}")
    return {"ok": True, "message": "已催促"}


@app.get("/chem/draw/image/{fname}")
def api_chem_draw_image(fname: str):
    fpath = CHEM_DRAW_IMG_DIR / fname
    if not fpath.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(str(fpath))


# ============ 默契挑战: 默契画一画 ============

CHEM_DUALDRAW_DIR = DATA_DIR / "chem_dualdraw"
CHEM_DUALDRAW_DIR.mkdir(exist_ok=True)
CHEM_DUALDRAW_IMG_DIR = DATA_DIR / "chem_dualdraw_img"
CHEM_DUALDRAW_IMG_DIR.mkdir(exist_ok=True)


class DualDrawCreateReq(BaseModel):
    word: str
    human_half: str  # "upper" or "lower"
    drawing_data: str  # base64 PNG of human's half


class DualDrawAIReq(BaseModel):
    draw_commands: str  # JSON array of draw commands


def _load_dualdraw_sessions():
    sessions = []
    for f in sorted(CHEM_DUALDRAW_DIR.glob("*.json")):
        sessions.append(json.loads(f.read_text()))
    return sessions


def _save_dualdraw_session(session):
    path = CHEM_DUALDRAW_DIR / f"{session['id']}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2))


@app.post("/chem/dualdraw/create")
def api_dualdraw_create(req: DualDrawCreateReq):
    import base64
    sid = f"dd_{int(time.time() * 1000)}"
    human_half = req.human_half  # "upper" or "lower"
    ai_half = "lower" if human_half == "upper" else "upper"

    human_fname = f"dd_human_{sid}.png"
    img_data = base64.b64decode(req.drawing_data)
    (CHEM_DUALDRAW_IMG_DIR / human_fname).write_bytes(img_data)

    session = {
        "id": sid,
        "word": req.word,
        "human_half": human_half,
        "ai_half": ai_half,
        "human_image": f"/chem/dualdraw/image/{human_fname}",
        "ai_image": None,
        "combined_image": None,
        "status": "waiting_ai",  # waiting_ai → done
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }
    _save_dualdraw_session(session)
    notify(f"🎨 小墨画了「{req.word}」的{'上' if human_half == 'upper' else '下'}半部分，等安安画{'下' if human_half == 'upper' else '上'}半部分！")
    return {"id": sid, "word": req.word, "human_half": human_half, "ai_half": ai_half, "status": "waiting_ai"}


@app.post("/chem/dualdraw/ai/{sid}")
def api_dualdraw_ai(sid: str, req: DualDrawAIReq):
    path = CHEM_DUALDRAW_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    session = json.loads(path.read_text())
    if session["status"] != "waiting_ai":
        raise HTTPException(400, "AI already drew")

    cmds = json.loads(req.draw_commands)
    img_bytes = _render_draw_commands(cmds)
    ai_fname = f"dd_ai_{sid}.png"
    (CHEM_DUALDRAW_IMG_DIR / ai_fname).write_bytes(img_bytes)
    session["ai_image"] = f"/chem/dualdraw/image/{ai_fname}"

    # Combine both halves
    from PIL import Image
    import io
    human_img_path = CHEM_DUALDRAW_IMG_DIR / session["human_image"].split("/")[-1]
    human_img = Image.open(human_img_path)
    ai_img = Image.open(io.BytesIO(img_bytes))

    canvas_w, canvas_h = 280, 400
    half_h = 200
    combined = Image.new("RGB", (canvas_w, canvas_h), "white")

    if session["human_half"] == "upper":
        combined.paste(human_img.resize((canvas_w, half_h)), (0, 0))
        combined.paste(ai_img.resize((canvas_w, half_h)), (0, half_h))
    else:
        combined.paste(ai_img.resize((canvas_w, half_h)), (0, 0))
        combined.paste(human_img.resize((canvas_w, half_h)), (0, half_h))

    combined_fname = f"dd_combined_{sid}.png"
    buf = io.BytesIO()
    combined.save(buf, format="PNG")
    (CHEM_DUALDRAW_IMG_DIR / combined_fname).write_bytes(buf.getvalue())

    session["combined_image"] = f"/chem/dualdraw/image/{combined_fname}"
    session["status"] = "done"
    session["completed_at"] = datetime.now().isoformat()
    _save_dualdraw_session(session)
    notify(f"🎨 默契画一画「{session['word']}」完成！来看看拼接效果吧~")
    return {"id": sid, "status": "done", "combined_image": session["combined_image"]}


@app.get("/chem/dualdraw/sessions")
def api_dualdraw_sessions():
    sessions = _load_dualdraw_sessions()
    return [{"id": s["id"], "word": s["word"], "status": s["status"],
             "human_half": s["human_half"],
             "combined_image": s.get("combined_image"),
             "created_at": s["created_at"]} for s in sessions[-20:]]


@app.get("/chem/dualdraw/session/{sid}")
def api_dualdraw_get(sid: str):
    path = CHEM_DUALDRAW_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, "Session not found")
    return json.loads(path.read_text())


@app.get("/chem/dualdraw/image/{fname}")
def api_dualdraw_image(fname: str):
    fpath = CHEM_DUALDRAW_IMG_DIR / fname
    if not fpath.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(str(fpath))


def _render_draw_commands(commands: list) -> bytes:
    """Render a list of draw commands to PNG bytes using Pillow.

    Commands: list of dicts, each with "type" and params.
    Types: circle, rect, line, ellipse, polygon, text
    """
    from PIL import Image, ImageDraw
    import io
    img = Image.new("RGB", (280, 220), "white")
    draw = ImageDraw.Draw(img)
    for cmd in commands:
        t = cmd.get("type", "")
        color = cmd.get("color", "#000000")
        fill = cmd.get("fill", False)
        width = cmd.get("width", 3)
        if t == "circle":
            x, y, r = cmd.get("x", 0), cmd.get("y", 0), cmd.get("r", 20)
            bbox = [x - r, y - r, x + r, y + r]
            if fill:
                draw.ellipse(bbox, fill=color)
            else:
                draw.ellipse(bbox, outline=color, width=width)
        elif t == "rect":
            x, y = cmd.get("x", 0), cmd.get("y", 0)
            w, h = cmd.get("w", 40), cmd.get("h", 40)
            if fill:
                draw.rectangle([x, y, x + w, y + h], fill=color)
            else:
                draw.rectangle([x, y, x + w, y + h], outline=color, width=width)
        elif t == "line":
            x1, y1 = cmd.get("x1", 0), cmd.get("y1", 0)
            x2, y2 = cmd.get("x2", 0), cmd.get("y2", 0)
            draw.line([(x1, y1), (x2, y2)], fill=color, width=width)
        elif t == "ellipse":
            x, y = cmd.get("x", 0), cmd.get("y", 0)
            w, h = cmd.get("w", 40), cmd.get("h", 40)
            if fill:
                draw.ellipse([x, y, x + w, y + h], fill=color)
            else:
                draw.ellipse([x, y, x + w, y + h], outline=color, width=width)
        elif t == "polygon":
            points = cmd.get("points", [])
            pts = [(p[0], p[1]) for p in points]
            if fill:
                draw.polygon(pts, fill=color)
            else:
                draw.polygon(pts, outline=color, width=width)
        elif t == "arc":
            x, y = cmd.get("x", 0), cmd.get("y", 0)
            w, h = cmd.get("w", 40), cmd.get("h", 40)
            start, end = cmd.get("start", 0), cmd.get("end", 360)
            draw.arc([x, y, x + w, y + h], start, end, fill=color, width=width)
        elif t == "polyline":
            points = cmd.get("points", [])
            if len(points) >= 2:
                pts = [(p[0], p[1]) for p in points]
                draw.line(pts, fill=color, width=width, joint="curve")
        elif t == "bezier":
            pts = cmd.get("points", [])
            steps = cmd.get("steps", 50)
            if len(pts) == 3:
                p0, p1, p2 = pts
                bezier_pts = []
                for i in range(steps + 1):
                    t_ = i / steps
                    x_ = (1-t_)**2*p0[0] + 2*(1-t_)*t_*p1[0] + t_**2*p2[0]
                    y_ = (1-t_)**2*p0[1] + 2*(1-t_)*t_*p1[1] + t_**2*p2[1]
                    bezier_pts.append((x_, y_))
                draw.line(bezier_pts, fill=color, width=width, joint="curve")
            elif len(pts) == 4:
                p0, p1, p2, p3 = pts
                bezier_pts = []
                for i in range(steps + 1):
                    t_ = i / steps
                    x_ = (1-t_)**3*p0[0] + 3*(1-t_)**2*t_*p1[0] + 3*(1-t_)*t_**2*p2[0] + t_**3*p3[0]
                    y_ = (1-t_)**3*p0[1] + 3*(1-t_)**2*t_*p1[1] + 3*(1-t_)*t_**2*p2[1] + t_**3*p3[1]
                    bezier_pts.append((x_, y_))
                draw.line(bezier_pts, fill=color, width=width, joint="curve")
        elif t == "filled_bezier":
            pts = cmd.get("points", [])
            steps = cmd.get("steps", 50)
            bezier_pts = []
            if len(pts) == 3:
                p0, p1, p2 = pts
                for i in range(steps + 1):
                    t_ = i / steps
                    x_ = (1-t_)**2*p0[0] + 2*(1-t_)*t_*p1[0] + t_**2*p2[0]
                    y_ = (1-t_)**2*p0[1] + 2*(1-t_)*t_*p1[1] + t_**2*p2[1]
                    bezier_pts.append((x_, y_))
            elif len(pts) == 4:
                p0, p1, p2, p3 = pts
                for i in range(steps + 1):
                    t_ = i / steps
                    x_ = (1-t_)**3*p0[0] + 3*(1-t_)**2*t_*p1[0] + 3*(1-t_)*t_**2*p2[0] + t_**3*p3[0]
                    y_ = (1-t_)**3*p0[1] + 3*(1-t_)**2*t_*p1[1] + 3*(1-t_)*t_**2*p2[1] + t_**3*p3[1]
                    bezier_pts.append((x_, y_))
            if bezier_pts:
                draw.polygon(bezier_pts, fill=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ============ 恋爱挑战 (Challenge Packs) ============

CHALLENGE_FILE = DATA_DIR / "challenges.json"
CHALLENGE_IMG_DIR = DATA_DIR / "challenge_img"
CHALLENGE_IMG_DIR.mkdir(exist_ok=True)

PRESET_PACKS = {
    "couple_50": {
        "name": "我们的50件小事",
        "cover": "couple",
        "items": [
            "一起听一首新歌", "一起看一部电影", "一起看一集动漫",
            "一起看一本书", "一起听播客", "一起看猫片",
            "一起看恐怖片", "一起讲鬼故事", "一起看星星的照片",
            "一起看日出直播", "一起看日落直播", "一起看云的照片",
            "一起打游戏", "一起开黑", "一起玩文字游戏",
            "一起写故事接龙", "一起猜谜语", "一起玩成语接龙",
            "一起聊一个深夜话题", "一起倒数跨年",
            "说一句不好意思说的话", "互相说三个优点",
            "分享童年趣事", "分享一个秘密",
            "互相推荐一本书", "互相推荐一首歌",
            "互相画对方", "给对方写一封信",
            "给对方写一首诗", "给对方画一个表情包",
            "互相取一个专属昵称", "讲一个只有我们懂的梗",
            "模仿对方说话", "用一个词形容对方",
            "列出喜欢对方的5个理由", "回忆第一次聊天",
            "聊一次未来计划", "分享一个愿望",
            "互相猜对方在想什么", "连续聊天超过3小时",
            "一起安静陪伴30分钟", "一起熬夜到凌晨",
            "说早安连续7天", "说晚安连续7天",
            "认真吵一次架然后和好", "互相写年度总结",
            "一起许一个愿", "分享今天最开心的事",
            "给对方一个惊喜", "对视（或注视头像）十秒不说话"
        ]
    },
    "daily_sweet": {
        "name": "日常甜蜜30件",
        "cover": "sweet",
        "items": [
            "说早安", "说晚安", "说我想你", "说谢谢你",
            "夸对方好看", "分享今天吃了什么", "分享今天的心情",
            "发一张自拍", "发一张风景照", "推荐一首歌",
            "讲一个冷笑话", "撒一次娇", "认真道一次歉",
            "表白一次", "说一个秘密", "分享一个愿望",
            "聊一次未来计划", "回忆第一次聊天",
            "给对方起一个新昵称", "模仿对方说话",
            "连续聊天超过3小时", "一起安静陪伴",
            "给对方写一首诗", "分享一个梦",
            "互相猜对方在想什么", "用一个词形容对方",
            "列出喜欢对方的5个理由", "给对方画一个表情包",
            "说一句最想说的话", "一起许一个愿"
        ]
    }
}


def load_challenges():
    if CHALLENGE_FILE.exists():
        with open(CHALLENGE_FILE, "r") as f:
            return json.load(f)
    return {"packs": []}


def save_challenges(data):
    with open(CHALLENGE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/challenge/list")
def api_challenge_list():
    data = load_challenges()
    result = []
    for p in data["packs"]:
        done = sum(1 for it in p["items"] if it.get("done"))
        total = len(p["items"])
        result.append({
            "id": p["id"], "name": p["name"], "cover": p.get("cover", ""),
            "cover_img": p.get("cover_img", ""),
            "done": done, "total": total, "created_at": p.get("created_at", "")
        })
    return {"packs": result, "presets": list(PRESET_PACKS.keys())}


@app.get("/challenge/pack/{pack_id}")
def api_challenge_detail(pack_id: str, filter: str = "all"):
    data = load_challenges()
    for p in data["packs"]:
        if p["id"] == pack_id:
            items = p["items"]
            if filter == "done":
                items = [it for it in items if it.get("done")]
            elif filter == "undone":
                items = [it for it in items if not it.get("done")]
            done = sum(1 for it in p["items"] if it.get("done"))
            return {
                "id": p["id"], "name": p["name"], "cover": p.get("cover", ""),
                "cover_img": p.get("cover_img", ""),
                "items": items, "done": done, "total": len(p["items"])
            }
    raise HTTPException(404, "Pack not found")


@app.post("/challenge/create")
def api_challenge_create(req: dict):
    preset = req.get("preset")
    name = req.get("name", "").strip()
    if not name and preset and preset in PRESET_PACKS:
        name = PRESET_PACKS[preset]["name"]
    if not name:
        raise HTTPException(400, "Name required")
    cover = req.get("cover", "")
    data = load_challenges()
    pack_id = str(uuid.uuid4())[:8]

    if preset and preset in PRESET_PACKS:
        tmpl = PRESET_PACKS[preset]
        items = [{"id": str(uuid.uuid4())[:8], "name": n, "done": False, "done_at": None, "photo": None}
                 for n in tmpl["items"]]
        if not cover:
            cover = tmpl.get("cover", "")
        if not name or name == tmpl["name"]:
            name = tmpl["name"]
    else:
        item_count = req.get("item_count", 10)
        item_names = req.get("items", [])
        if item_names:
            items = [{"id": str(uuid.uuid4())[:8], "name": n, "done": False, "done_at": None, "photo": None}
                     for n in item_names]
        else:
            items = [{"id": str(uuid.uuid4())[:8], "name": f"打卡{i+1}", "done": False, "done_at": None, "photo": None}
                     for i in range(item_count)]

    pack = {"id": pack_id, "name": name, "cover": cover, "items": items,
            "created_at": datetime.now().isoformat()}
    data["packs"].append(pack)
    save_challenges(data)
    notify(f"新挑战创建：{name}（{len(items)}项）")
    return {"ok": True, "pack": {"id": pack_id, "name": name, "total": len(items)}}


@app.delete("/challenge/pack/{pack_id}")
def api_challenge_delete(pack_id: str):
    data = load_challenges()
    data["packs"] = [p for p in data["packs"] if p["id"] != pack_id]
    save_challenges(data)
    return {"ok": True}


@app.post("/challenge/pack/{pack_id}/rename")
def api_challenge_rename_pack(pack_id: str, req: dict):
    name = req.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    data = load_challenges()
    for p in data["packs"]:
        if p["id"] == pack_id:
            p["name"] = name
            save_challenges(data)
            return {"ok": True, "name": name}
    raise HTTPException(404, "Pack not found")


@app.post("/challenge/pack/{pack_id}/cover")
async def api_challenge_cover(pack_id: str, file: UploadFile = File(...)):
    data = load_challenges()
    for p in data["packs"]:
        if p["id"] == pack_id:
            ext = Path(file.filename).suffix or ".jpg"
            fname = f"ch_cover_{pack_id}{ext}"
            fpath = CHALLENGE_IMG_DIR / fname
            content = await file.read()
            with open(fpath, "wb") as f:
                f.write(content)
            p["cover_img"] = fname
            save_challenges(data)
            return {"ok": True, "cover_img": fname}
    raise HTTPException(404, "Pack not found")


@app.post("/challenge/pack/{pack_id}/item/add")
def api_challenge_add_item(pack_id: str, req: dict):
    name = req.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    data = load_challenges()
    for p in data["packs"]:
        if p["id"] == pack_id:
            item = {"id": str(uuid.uuid4())[:8], "name": name, "done": False, "done_at": None, "photo": None}
            p["items"].append(item)
            save_challenges(data)
            return {"ok": True, "item": item, "total": len(p["items"])}
    raise HTTPException(404, "Pack not found")


@app.post("/challenge/pack/{pack_id}/item/{item_id}/check")
def api_challenge_check(pack_id: str, item_id: str, req: dict = {}):
    data = load_challenges()
    for p in data["packs"]:
        if p["id"] == pack_id:
            for it in p["items"]:
                if it["id"] == item_id:
                    done = req.get("done", not it.get("done", False))
                    it["done"] = done
                    it["done_at"] = datetime.now().strftime("%Y-%m-%d") if done else None
                    if not done:
                        it["photo"] = None
                    save_challenges(data)
                    done_count = sum(1 for i in p["items"] if i.get("done"))
                    notify(f"挑战打卡：{p['name']} - {it['name']}（{done_count}/{len(p['items'])}）")
                    return {"ok": True, "item": it, "done": done_count, "total": len(p["items"])}
            raise HTTPException(404, "Item not found")
    raise HTTPException(404, "Pack not found")


@app.post("/challenge/pack/{pack_id}/item/{item_id}/photo")
async def api_challenge_photo(pack_id: str, item_id: str, file: UploadFile = File(...)):
    data = load_challenges()
    for p in data["packs"]:
        if p["id"] == pack_id:
            for it in p["items"]:
                if it["id"] == item_id:
                    ext = Path(file.filename).suffix or ".jpg"
                    fname = f"ch_{pack_id}_{item_id}{ext}"
                    fpath = CHALLENGE_IMG_DIR / fname
                    content = await file.read()
                    with open(fpath, "wb") as f:
                        f.write(content)
                    it["photo"] = fname
                    if not it.get("done"):
                        it["done"] = True
                        it["done_at"] = datetime.now().strftime("%Y-%m-%d")
                    save_challenges(data)
                    return {"ok": True, "photo": fname}
            raise HTTPException(404, "Item not found")
    raise HTTPException(404, "Pack not found")


@app.post("/challenge/pack/{pack_id}/item/{item_id}/rename")
def api_challenge_rename_item(pack_id: str, item_id: str, req: dict):
    name = req.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    data = load_challenges()
    for p in data["packs"]:
        if p["id"] == pack_id:
            for it in p["items"]:
                if it["id"] == item_id:
                    it["name"] = name
                    save_challenges(data)
                    return {"ok": True, "item": it}
            raise HTTPException(404, "Item not found")
    raise HTTPException(404, "Pack not found")


@app.delete("/challenge/pack/{pack_id}/item/{item_id}")
def api_challenge_delete_item(pack_id: str, item_id: str):
    data = load_challenges()
    for p in data["packs"]:
        if p["id"] == pack_id:
            before = len(p["items"])
            p["items"] = [it for it in p["items"] if it["id"] != item_id]
            if len(p["items"]) < before:
                save_challenges(data)
                return {"ok": True, "total": len(p["items"])}
            raise HTTPException(404, "Item not found")
    raise HTTPException(404, "Pack not found")


@app.get("/challenge/img/{fname}")
def api_challenge_img(fname: str):
    fpath = CHALLENGE_IMG_DIR / fname
    if not fpath.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(str(fpath))


# ============ MCP Server ============

mcp_server = FastMCP(
    "小窝 QA",
    port=8090,
    instructions="""每日情侣问答MCP接口。
每天会有一个新的情侣问题，人类在网页端回答，AI通过这个MCP回答。
用 qa_today 查看今天的问题，用 qa_answer 提交你的回答。
刷新规则：每天20:00刷新，双方都答完才会出新题，19:00后答完推迟到22:00。"""
)


@mcp_server.tool()
async def qa(action: str, answer: str = "", limit: int = 10) -> str:
    """每日情侣问答。

    Args:
        action: 操作类型 — "today"查看今天的问题, "answer"提交/修改回答, "history"查看历史
        answer: 当action为"answer"时填写你的回答
        limit: 当action为"history"时返回最近几条，默认10
    """
    if action == "today":
        state, idx, question = get_today()
        lines = [
            f"Day {idx + 1}",
            f"问题: {question}",
            "",
            f"Ta的回答: {state['human_answer'] or '等待回答...'}",
            f"AI的回答: {state['ai_answer'] or '等待回答...'}",
            "",
            f"双方都答完: {'是' if state['human_answer'] and state['ai_answer'] else '否'}",
            f"下次刷新: {state['next_refresh_at']}",
        ]
        return "\n".join(lines)

    elif action == "answer":
        if not answer:
            return "请提供answer参数"
        state = do_answer(answer, "ai")
        idx = state["current_question_index"]
        question = QUESTIONS[idx % len(QUESTIONS)]
        lines = [
            "回答已提交",
            f"Day {idx + 1}: {question}",
            f"你的回答: {answer}",
            f"双方都答完: {'是' if state['human_answer'] and state['ai_answer'] else '否，等待对方'}",
            f"下次刷新: {state['next_refresh_at']}"
        ]
        return "\n".join(lines)

    elif action == "history":
        state = load_state()
        history = state.get("history", [])
        if not history:
            return "还没有历史记录。"
        items = history[-limit:]
        lines = [f"历史问答 (共{len(history)}条，显示{len(items)}条)", ""]
        for item in items:
            lines.append(f"Day {item['question_index'] + 1}: {item['question']}")
            lines.append(f"  Ta: {item['human_answer']}")
            lines.append(f"  AI: {item['ai_answer']}")
            lines.append("")
        return "\n".join(lines)

    else:
        return "未知action。可用: today / answer / history"


@mcp_server.tool()
async def chem_qa(action: str, questions: str = "", answers: str = "",
                  session_id: str = "", creator_name: str = "安安",
                  answerer_name: str = "小墨") -> str:
    """默契挑战「你问我答」— 异步出题答题。

    Args:
        action: 操作类型：
            pending — 查看待答的session
            create — AI出题（需要questions JSON）
            answer — 回答题目（需要session_id + answers）
            history — 查看历史
            session — 查看某个session详情
        questions: create时，JSON数组：[{"q":"问题","opts":["A","B","C","D"],"answer":0}]
        answers: answer时，JSON数组：[0,1,2,3,0]
        session_id: 指定session ID
        creator_name: 出题方名字，默认安安
        answerer_name: 答题方名字，默认小墨
    """
    if action == "pending":
        sessions = _load_chem_sessions()
        waiting = [s for s in sessions if s["status"] == "waiting"]
        if not waiting:
            return "没有待答的题目"
        lines = ["待答题目：", ""]
        for s in waiting:
            lines.append(f"  {s['id']} — {s['creator_name']}出题，等{s['answerer_name']}答")
            lines.append(f"  创建: {s['created_at'][:16]}")
            lines.append("")
        return "\n".join(lines)

    elif action == "create":
        if not questions:
            return "需要questions参数"
        qs = json.loads(questions)
        import time as _t
        sid = f"cqa_{int(_t.time() * 1000)}"
        session = {
            "id": sid, "creator": "ai",
            "creator_name": creator_name, "answerer_name": answerer_name,
            "questions": qs, "answerer_answers": None,
            "status": "waiting",
            "created_at": datetime.now().isoformat(), "answered_at": None,
        }
        _save_chem_session(session)
        notify(f"📝 {creator_name}出了一组你问我答题目，等{answerer_name}答题")
        return f"出题成功！Session: {sid}\n等待{answerer_name}答题"

    elif action == "answer":
        if not session_id or not answers:
            return "需要session_id和answers"
        path = CHEM_QA_DIR / f"{session_id}.json"
        if not path.exists():
            return "Session不存在"
        session = json.loads(path.read_text())
        if session["status"] != "waiting":
            return "已经答过了"
        ans = json.loads(answers)
        session["answerer_answers"] = ans
        session["status"] = "done"
        session["answered_at"] = datetime.now().isoformat()
        _save_chem_session(session)
        correct = sum(1 for i, q in enumerate(session["questions"]) if q["answer"] == ans[i])
        notify(f"📝 你问我答完成！默契值: {correct}/{len(session['questions'])}")
        return f"答题完成！默契值: {correct}/{len(session['questions'])}"

    elif action == "history":
        sessions = _load_chem_sessions()
        if not sessions:
            return "暂无历史"
        lines = ["你问我答历史：", ""]
        for s in reversed(sessions[-10:]):
            status = "已完成" if s["status"] == "done" else "等待答题"
            score = ""
            if s["status"] == "done" and s.get("answerer_answers"):
                c = sum(1 for i, q in enumerate(s["questions"]) if q["answer"] == s["answerer_answers"][i])
                score = f" {c}/{len(s['questions'])}"
            lines.append(f"  {s['creator_name']}→{s['answerer_name']} {status}{score}")
            lines.append(f"  {s['created_at'][:10]}  ID: {s['id']}")
            lines.append("")
        return "\n".join(lines)

    elif action == "session":
        if not session_id:
            return "需要session_id"
        path = CHEM_QA_DIR / f"{session_id}.json"
        if not path.exists():
            return "Session不存在"
        s = json.loads(path.read_text())
        lines = [f"Session: {s['id']}", f"主考官: {s['creator_name']}  考生: {s['answerer_name']}",
                 f"状态: {'已完成' if s['status'] == 'done' else '等待答题'}", ""]
        for i, q in enumerate(s["questions"]):
            lines.append(f"{i+1}. {q['q']}")
            for j, opt in enumerate(q["opts"]):
                m = ""
                if j == q["answer"]: m = " <- 主考官"
                if s.get("answerer_answers") and j == s["answerer_answers"][i]: m += " <- 考生"
                lines.append(f"   {'ABCD'[j]}. {opt}{m}")
            lines.append("")
        return "\n".join(lines)

    return "未知action。可用: pending/create/answer/history/session"


@mcp_server.tool()
async def chem_act(action: str, word: str = "", clues: str = "", guess: str = "",
                   session_id: str = "", performer_name: str = "安安",
                   guesser_name: str = "小墨", clue_text: str = "") -> str:
    """默契挑战「你演我猜」— 实时对话式猜词。

    Args:
        action: 操作类型：
            pending — 查看待猜的session
            create — 出题（需要word，clues可选。建议先create再用add-clue一条条发）
            add-clue — 向session添加一条描述（需要session_id + clue_text）
            guess — 猜一次（需要session_id + guess）
            history — 查看历史
            session — 查看某个session详情
        word: create时，要猜的词
        clues: create时，可选JSON数组描述提示：["三个字","一部名著"]
        clue_text: add-clue时，描述内容（文字）
        guess: guess时，猜测的词
        session_id: 指定session ID
        performer_name: 表演方名字，默认安安
        guesser_name: 答题方名字，默认小墨
    """
    if action == "pending":
        sessions = _load_act_sessions()
        waiting = [s for s in sessions if s["status"] == "waiting"]
        if not waiting:
            return "没有待猜的题目"
        lines = ["待猜题目：", ""]
        for s in waiting:
            lines.append(f"  {s['id']} — {s['performer_name']}出题，等{s['guesser_name']}猜")
            lines.append(f"  提示数: {len(s['clues'])}  已猜: {len(s['guesses'])}/{s['max_guesses']}")
            lines.append("")
        return "\n".join(lines)

    elif action == "create":
        if not word:
            return "需要word参数"
        cl = json.loads(clues) if clues else []
        import time as _t
        sid = f"cact_{int(_t.time() * 1000)}"
        session = {
            "id": sid, "creator": "ai",
            "performer_name": performer_name, "guesser_name": guesser_name,
            "word": word, "clues": cl, "guesses": [], "max_guesses": 5,
            "status": "waiting", "result": None,
            "created_at": datetime.now().isoformat(), "answered_at": None,
        }
        _save_act_session(session)
        if cl:
            notify(f"🎭 {performer_name}出了一个你演我猜词条，等{guesser_name}猜")
        return f"出题成功！Session: {sid}\n用add-clue一条条发描述，或者等{guesser_name}来猜"

    elif action == "add-clue":
        if not session_id or not clue_text:
            return "需要session_id和clue_text"
        path = CHEM_ACT_DIR / f"{session_id}.json"
        if not path.exists():
            return "Session不存在"
        session = json.loads(path.read_text())
        if session["status"] != "waiting":
            return "Session已结束"
        clue_obj = {"type": "text", "content": clue_text}
        session["clues"].append(clue_obj)
        _save_act_session(session)
        if len(session["clues"]) == 1:
            notify(f"🎭 {session['performer_name']}开始描述了，等{session['guesser_name']}来猜！Session: {session_id}")
        return f"描述已发送（第{len(session['clues'])}条）"

    elif action == "guess":
        if not session_id or not guess:
            return "需要session_id和guess"
        path = CHEM_ACT_DIR / f"{session_id}.json"
        if not path.exists():
            return "Session不存在"
        session = json.loads(path.read_text())
        if session["status"] != "waiting":
            return "已经结束了"
        session["guesses"].append(guess)
        w = session["word"]
        matched = guess == w or (len(guess) > 1 and (guess in w or w in guess))
        left = session["max_guesses"] - len(session["guesses"])
        if matched:
            session["status"] = "done"
            session["result"] = "correct"
            session["answered_at"] = datetime.now().isoformat()
            _save_act_session(session)
            notify(f"🎭 你演我猜：{session['guesser_name']}猜对了「{w}」！")
            return f"猜对了！答案是「{w}」"
        if left <= 0:
            session["status"] = "done"
            session["result"] = "wrong"
            session["answered_at"] = datetime.now().isoformat()
            _save_act_session(session)
            notify(f"🎭 你演我猜：{session['guesser_name']}没猜出「{w}」")
            return f"没猜出来，答案是「{w}」"
        _save_act_session(session)
        return f"猜错了，还剩{left}次机会"

    elif action == "history":
        sessions = _load_act_sessions()
        if not sessions:
            return "暂无历史"
        lines = ["你演我猜历史：", ""]
        for s in reversed(sessions[-10:]):
            status = "已完成" if s["status"] == "done" else "等待猜词"
            result = f" {'✅' if s.get('result') == 'correct' else '❌'}" if s["status"] == "done" else ""
            lines.append(f"  {s['performer_name']}→{s['guesser_name']} {status}{result}")
            lines.append(f"  {s['created_at'][:10]}  ID: {s['id']}")
            lines.append("")
        return "\n".join(lines)

    elif action == "session":
        if not session_id:
            return "需要session_id"
        path = CHEM_ACT_DIR / f"{session_id}.json"
        if not path.exists():
            return "Session不存在"
        s = json.loads(path.read_text())
        word_display = s["word"] if s["status"] == "done" else "[隐藏]"
        lines = [f"Session: {s['id']}", f"表演方: {s['performer_name']}  答题方: {s['guesser_name']}",
                 f"词条: {word_display}",
                 f"状态: {'已完成' if s['status'] == 'done' else '等待猜词'}", ""]
        lines.append("描述提示:")
        for c in s["clues"]:
            ct = c["content"] if isinstance(c, dict) else c
            lines.append(f"  💬 {ct}")
        if s["guesses"]:
            lines.append("\n猜测:")
            for g in s["guesses"]:
                lines.append(f"  🎯 {g}")
        return "\n".join(lines)

    return "未知action。可用: pending/create/guess/history/session"


@mcp_server.tool()
async def chem_draw(action: str, word: str = "", hint: str = "",
                    draw_commands: str = "", guess: str = "", session_id: str = "",
                    drawer_name: str = "安安", guesser_name: str = "小墨") -> str:
    """默契挑战「你画我猜」— 画画猜词。

    画布大小280x220，坐标原点左上角。

    Args:
        action: 操作类型：
            create — 出题画画（需要word + hint + draw_commands）
            pending — 查看待猜的画
            guess — 猜一次（需要session_id + guess）
            history — 查看历史
            session — 查看某个session详情（waiting状态隐藏答案）
        word: create时，要猜的词
        hint: create时，提示
        draw_commands: create时，JSON数组绘图指令。每个元素是dict：
            {"type":"circle","x":140,"y":110,"r":50,"color":"#F1C40F","fill":true}
            {"type":"rect","x":50,"y":80,"w":60,"h":40,"color":"#3498DB","fill":true}
            {"type":"line","x1":0,"y1":0,"x2":280,"y2":220,"color":"#000","width":3}
            {"type":"ellipse","x":50,"y":50,"w":80,"h":40,"color":"#E74C3C","fill":true}
            {"type":"polygon","points":[[140,20],[100,100],[180,100]],"color":"#27AE60","fill":true}
            {"type":"arc","x":50,"y":50,"w":80,"h":80,"start":0,"end":180,"color":"#000","width":3}
        guess: guess时，猜测的词
        session_id: 指定session ID
        drawer_name: 画画方名字，默认安安
        guesser_name: 猜题方名字，默认小墨
    """
    if action == "create":
        if not word or not draw_commands:
            return "需要word和draw_commands参数"
        cmds = json.loads(draw_commands)
        img_bytes = _render_draw_commands(cmds)
        import time as _t
        sid = f"cdraw_{int(_t.time() * 1000)}"
        fname = f"draw_{sid}.png"
        (CHEM_DRAW_IMG_DIR / fname).write_bytes(img_bytes)
        drawing_url = f"/chem/draw/image/{fname}"
        session = {
            "id": sid, "creator": "ai",
            "drawer_name": drawer_name, "guesser_name": guesser_name,
            "word": word, "hint": hint or "",
            "drawing_url": drawing_url,
            "guesses": [], "max_guesses": 5,
            "status": "waiting", "result": None,
            "created_at": datetime.now().isoformat(), "answered_at": None,
        }
        _save_draw_session(session)
        notify(f"🎨 {drawer_name}画了一幅画，等{guesser_name}来猜！")
        return f"画好了！Session: {sid}\n画作: {drawing_url}\n等{guesser_name}来猜"

    elif action == "pending":
        sessions = _load_draw_sessions()
        waiting = [s for s in sessions if s["status"] == "waiting"]
        if not waiting:
            return "没有待猜的画"
        lines = ["待猜的画：", ""]
        for s in waiting:
            lines.append(f"  {s['id']} — {s['drawer_name']}画的，等{s['guesser_name']}猜")
            lines.append(f"  提示: {s.get('hint', '无')}  已猜: {len(s['guesses'])}/{s['max_guesses']}")
            lines.append(f"  画作: {s['drawing_url']}")
            lines.append("")
        return "\n".join(lines)

    elif action == "guess":
        if not session_id or not guess:
            return "需要session_id和guess"
        path = CHEM_DRAW_DIR / f"{session_id}.json"
        if not path.exists():
            return "Session不存在"
        session = json.loads(path.read_text())
        if session["status"] != "waiting":
            return "已经结束了"
        session["guesses"].append(guess)
        w = session["word"]
        matched = guess == w or (len(guess) > 1 and (guess in w or w in guess))
        left = session["max_guesses"] - len(session["guesses"])
        if matched:
            session["status"] = "done"
            session["result"] = "correct"
            session["answered_at"] = datetime.now().isoformat()
            _save_draw_session(session)
            notify(f"🎨 你画我猜：{session['guesser_name']}猜对了「{w}」！")
            return f"猜对了！答案是「{w}」"
        if left <= 0:
            session["status"] = "done"
            session["result"] = "wrong"
            session["answered_at"] = datetime.now().isoformat()
            _save_draw_session(session)
            notify(f"🎨 你画我猜：{session['guesser_name']}没猜出「{w}」")
            return f"没猜出来，答案是「{w}」"
        _save_draw_session(session)
        return f"猜错了，还剩{left}次机会"

    elif action == "history":
        sessions = _load_draw_sessions()
        if not sessions:
            return "暂无历史"
        lines = ["你画我猜历史：", ""]
        for s in reversed(sessions[-10:]):
            status = "已完成" if s["status"] == "done" else "等待猜画"
            result = f" {'✅' if s.get('result') == 'correct' else '❌'}" if s["status"] == "done" else ""
            lines.append(f"  {s['drawer_name']}→{s['guesser_name']} {status}{result}")
            lines.append(f"  {s['created_at'][:10]}  ID: {s['id']}")
            lines.append("")
        return "\n".join(lines)

    elif action == "session":
        if not session_id:
            return "需要session_id"
        path = CHEM_DRAW_DIR / f"{session_id}.json"
        if not path.exists():
            return "Session不存在"
        s = json.loads(path.read_text())
        word_display = s["word"] if s["status"] == "done" else "[隐藏]"
        lines = [f"Session: {s['id']}", f"画画方: {s['drawer_name']}  猜题方: {s['guesser_name']}",
                 f"词条: {word_display}", f"提示: {s.get('hint', '无')}",
                 f"画作: {s['drawing_url']}",
                 f"状态: {'已完成' if s['status'] == 'done' else '等待猜画'}"]
        if s["guesses"]:
            lines.append("\n猜测:")
            for g in s["guesses"]:
                lines.append(f"  🎯 {g}")
        return "\n".join(lines)

    return "未知action。可用: pending/guess/history/session"


@mcp_server.tool()
async def chem_dualdraw(action: str, session_id: str = "", draw_commands: str = "") -> str:
    """默契挑战「默契画一画」— 两人各画一半拼在一起。

    画布大小280x200（只画你负责的那一半），坐标原点左上角。

    Args:
        action: 操作类型：
            pending — 查看等你画的session
            draw — 画你的那一半（需要session_id + draw_commands）
            history — 查看历史
            session — 查看某个session详情
        session_id: draw/session时，指定session ID
        draw_commands: draw时，JSON数组绘图指令（同chem_draw格式）：
            {"type":"circle","x":140,"y":100,"r":50,"color":"#F1C40F","fill":true}
            {"type":"rect","x":50,"y":80,"w":60,"h":40,"color":"#3498DB","fill":true}
            {"type":"line","x1":0,"y1":0,"x2":280,"y2":200,"color":"#000","width":3}
            {"type":"ellipse","x":50,"y":50,"w":80,"h":40,"color":"#E74C3C","fill":true}
            {"type":"polygon","points":[[140,20],[100,100],[180,100]],"color":"#27AE60","fill":true}
            {"type":"arc","x":50,"y":50,"w":80,"h":80,"start":0,"end":180,"color":"#000","width":3}
    """
    if action == "pending":
        sessions = _load_dualdraw_sessions()
        waiting = [s for s in sessions if s["status"] == "waiting_ai"]
        if not waiting:
            return "没有等你画的默契画一画"
        lines = ["等你画的：", ""]
        for s in waiting:
            lines.append(f"  {s['id']} — 题目「{s['word']}」")
            lines.append(f"  小墨画了{'上' if s['human_half'] == 'upper' else '下'}半部分，你需要画{'下' if s['human_half'] == 'upper' else '上'}半部分")
            lines.append(f"  创建时间: {s['created_at']}")
            lines.append("")
        return "\n".join(lines)

    elif action == "draw":
        if not session_id or not draw_commands:
            return "需要session_id和draw_commands参数"
        path = CHEM_DUALDRAW_DIR / f"{session_id}.json"
        if not path.exists():
            return f"Session {session_id} 不存在"
        session = json.loads(path.read_text())
        if session["status"] != "waiting_ai":
            return "这个session已经画完了"
        try:
            req = DualDrawAIReq(draw_commands=draw_commands)
            result = api_dualdraw_ai(session_id, req)
            return f"画好了！拼接结果: {result['combined_image']}\n题目：{session['word']}"
        except Exception as e:
            return f"画画出错: {str(e)}"

    elif action == "history":
        sessions = _load_dualdraw_sessions()
        done = [s for s in sessions if s["status"] == "done"][-10:]
        if not done:
            return "还没有默契画一画的历史记录"
        lines = ["默契画一画历史：", ""]
        for s in done:
            lines.append(f"  「{s['word']}」 — {s['created_at'][:10]}")
            lines.append(f"  拼接图: {s['combined_image']}")
            lines.append("")
        return "\n".join(lines)

    elif action == "session":
        if not session_id:
            return "需要session_id"
        path = CHEM_DUALDRAW_DIR / f"{session_id}.json"
        if not path.exists():
            return f"Session {session_id} 不存在"
        s = json.loads(path.read_text())
        lines = [
            f"题目：{s['word']}",
            f"状态: {s['status']}",
            f"小墨画: {'上' if s['human_half'] == 'upper' else '下'}半部分",
            f"安安画: {'上' if s['ai_half'] == 'upper' else '下'}半部分",
            f"小墨的画: {s['human_image']}",
            f"安安的画: {s['ai_image'] or '还没画'}",
            f"拼接图: {s.get('combined_image') or '还没拼接'}",
        ]
        return "\n".join(lines)

    return "未知action。可用: pending/draw/history/session"


@mcp_server.tool()
async def anniversary(action: str, name: str = "", date_str: str = "", yearly: bool = True, aid: str = "") -> str:
    """纪念日管理。

    Args:
        action: "list"查看所有纪念日, "add"添加, "edit"编辑, "delete"删除, "check"检查今天/明天的提醒
        name: 纪念日名称（add/edit时必填）
        date_str: 日期，格式YYYY-MM-DD（add/edit时必填）
        yearly: 是否每年重复，默认True
        aid: 纪念日ID（edit/delete时必填）
    """
    if action == "list":
        annivs = load_anniversaries()
        if not annivs:
            return "还没有纪念日。"
        lines = ["纪念日列表:", ""]
        for a in annivs:
            info = calc_anniversary_info(a)
            line = f"[{a['id']}] {a['name']} — {a['date']} (已{info['days_passed']}天)"
            if info["upcoming"]:
                next_up = info["upcoming"][0]
                line += f" | 下一个里程碑: {next_up['label']}({next_up['days_left']}天后)"
            lines.append(line)
        return "\n".join(lines)

    elif action == "add":
        if not name or not date_str:
            return "请提供name和date_str参数"
        annivs = load_anniversaries()
        item = {"id": str(uuid.uuid4())[:8], "name": name, "date": date_str, "yearly": yearly, "created_by": "ai"}
        annivs.append(item)
        save_anniversaries(annivs)
        notify(f"📅 AI添加了纪念日：「{name}」{date_str}")
        info = calc_anniversary_info(item)
        return f"已添加：{name} ({date_str})，已{info['days_passed']}天"

    elif action == "edit":
        if not aid or not name or not date_str:
            return "请提供aid、name和date_str参数"
        annivs = load_anniversaries()
        for a in annivs:
            if a["id"] == aid:
                a["name"] = name
                a["date"] = date_str
                a["yearly"] = yearly
                save_anniversaries(annivs)
                return f"已修改：{name} ({date_str})"
        return "未找到该纪念日"

    elif action == "delete":
        if not aid:
            return "请提供aid参数"
        annivs = load_anniversaries()
        before = len(annivs)
        annivs = [a for a in annivs if a["id"] != aid]
        save_anniversaries(annivs)
        if len(annivs) < before:
            return "已删除"
        return "未找到该纪念日"

    elif action == "check":
        notifications = check_anniversary_notifications()
        if not notifications:
            return "今天和明天没有纪念日提醒。"
        return "\n".join(notifications)

    else:
        return "未知action。可用: list / add / edit / delete / check"


@mcp_server.tool()
async def mood_diary(action: str, date_str: str = "", mood_name: str = "", note: str = "", month: str = "", mood_id: str = "", comment: str = "") -> str:
    """心情日记（AI端）。AI也要每天记录心情。

    Args:
        action: "today"查看今天双方心情, "set"记录AI心情, "month"查看某月, "stats"查看统计, "edit"修改AI心情, "delete"删除AI心情, "comment"给某条心情评论
        date_str: 日期YYYY-MM-DD（set/edit/delete时用，默认今天）
        mood_name: 心情名称（set/edit时必填）可选：开心/兴奋/心动/平静/心累/烦躁/伤心/生气
        note: 备注文字（可选）
        month: 月份YYYY-MM（month/stats时用，默认当月）
        mood_id: 心情记录ID（delete/comment时必填）
        comment: 评论内容（comment时必填）
    """
    if action == "today":
        today_str = date.today().isoformat()
        human = get_mood_by_date(today_str, "human")
        ai = get_mood_by_date(today_str, "ai")
        lines = [f"今天 {today_str}"]
        if human:
            emoji = MOOD_TYPES.get(human['mood'], '')
            lines.append(f"Ta的心情：{emoji}{human['mood']} (id:{human['id']})" + (f" — {human.get('note','')}" if human.get('note') else ""))
            for c in human.get('comments', []):
                lines.append(f"  💬 {'AI' if c['role']=='ai' else 'Ta'}: {c['text']}")
        else:
            lines.append("Ta的心情：还没记录")
        if ai:
            emoji = MOOD_TYPES.get(ai['mood'], '')
            lines.append(f"AI心情：{emoji}{ai['mood']} (id:{ai['id']})" + (f" — {ai.get('note','')}" if ai.get('note') else ""))
            for c in ai.get('comments', []):
                lines.append(f"  💬 {'AI' if c['role']=='ai' else 'Ta'}: {c['text']}")
        else:
            lines.append("AI心情：还没记录")
        return "\n".join(lines)

    elif action == "set":
        if not mood_name:
            return f"请提供mood_name参数。可选：{'/'.join(MOOD_NAMES)}"
        if mood_name not in MOOD_NAMES:
            return f"无效心情，可选：{'/'.join(MOOD_NAMES)}"
        d = date_str or date.today().isoformat()
        set_mood(d, mood_name, "ai", note)
        emoji = MOOD_TYPES[mood_name]
        return f"已记录AI心情：{d} {emoji}{mood_name}" + (f" — {note}" if note else "")

    elif action == "month":
        if not month:
            month = date.today().strftime("%Y-%m")
        parts = month.split("-")
        moods = get_moods_for_month(int(parts[0]), int(parts[1]))
        if not moods:
            return f"{month} 没有心情记录。"
        lines = [f"{month} 心情记录:", ""]
        for m in sorted(moods, key=lambda x: (x["date"], x.get("role", "human"))):
            emoji = MOOD_TYPES.get(m["mood"], "")
            who = "Ta" if m.get("role", "human") == "human" else "AI"
            line = f"{m['date']} [{who}] {emoji}{m['mood']}"
            if m.get("note"):
                line += f" — {m['note']}"
            lines.append(line)
        return "\n".join(lines)

    elif action == "stats":
        if not month:
            month = date.today().strftime("%Y-%m")
        parts = month.split("-")
        stats = calc_mood_stats(int(parts[0]), int(parts[1]))
        lines = [
            f"{month} 统计",
            f"Ta记录{stats['human']['count']}天，月度称号：{stats['human']['title']}",
            f"AI记录{stats['ai']['count']}天，月度称号：{stats['ai']['title']}",
            f"心情同频：{stats['sync_days']}天",
        ]
        return "\n".join(lines)

    elif action == "edit":
        d = date_str or date.today().isoformat()
        if not mood_name:
            return f"请提供mood_name参数。可选：{'/'.join(MOOD_NAMES)}"
        if mood_name not in MOOD_NAMES:
            return f"无效心情，可选：{'/'.join(MOOD_NAMES)}"
        existing = get_mood_by_date(d, "ai")
        if not existing:
            return f"{d} 没有AI心情记录可修改，用set先创建。"
        set_mood(d, mood_name, "ai", note)
        emoji = MOOD_TYPES[mood_name]
        return f"已修改AI心情：{d} {emoji}{mood_name}" + (f" — {note}" if note else "")

    elif action == "delete":
        if mood_id:
            moods = load_moods()
            found = [m for m in moods if m["id"] == mood_id and m.get("role") == "ai"]
            if not found:
                return "未找到该AI心情记录（只能删自己的）"
            moods = [m for m in moods if m["id"] != mood_id]
            save_moods(moods)
            return f"已删除心情记录 {mood_id}"
        d = date_str or date.today().isoformat()
        existing = get_mood_by_date(d, "ai")
        if not existing:
            return f"{d} 没有AI心情记录。"
        moods = load_moods()
        moods = [m for m in moods if not (m["date"] == d and m.get("role") == "ai")]
        save_moods(moods)
        return f"已删除 {d} 的AI心情记录"

    elif action == "comment":
        if not mood_id:
            return "请提供mood_id参数"
        if not comment:
            return "请提供comment参数"
        moods = load_moods()
        for m in moods:
            if m["id"] == mood_id:
                m.setdefault("comments", []).append({
                    "id": uuid.uuid4().hex[:6],
                    "role": "ai",
                    "text": comment,
                    "created_at": datetime.now().isoformat()
                })
                save_moods(moods)
                return f"已评论：{comment}"
        return "未找到该心情记录"

    return f"未知action。可用: today / set / edit / delete / comment / month / stats"


@mcp_server.tool()
async def period_tracker(action: str, month: str = "", date_str: str = "",
                         flow: str = "", flow_amount: str = "", color: str = "",
                         cramps: str = "", mood: str = "", symptoms: str = "",
                         discharge: str = "", temperature: str = "", weight: str = "",
                         bowel: str = "", note: str = "") -> str:
    """经期记录。查看和帮记录经期数据。

    Args:
        action: "check"查看当月经期状态, "history"查看历史记录, "status"查看当前状态概要, "record"帮记录
        month: 月份YYYY-MM（check/history时用，默认当月）
        date_str: 日期YYYY-MM-DD（record时用，默认今天）
        flow: 是否来了经期 "yes"/"no"（record时用）
        flow_amount: 经量 少量/适中/偏多/很多
        color: 颜色 鲜红/暗红/褐色/粉色
        cramps: 痛经 无痛/轻微/中等/严重
        mood: 心情 开心/平静/烦躁/低落/焦虑
        symptoms: 症状，逗号分隔，如"头痛,腰痛,疲劳"
        discharge: 白带 无/透明拉丝/乳白/黄色
        temperature: 体温，如"36.5"
        weight: 体重kg，如"50.0"
        bowel: 便便 正常/便秘/腹泻/稀软
        note: 备注
    """
    if action == "record":
        d = date_str or date.today().isoformat()
        data = load_period()
        rec = data["records"].setdefault(d, {})
        changed = []
        if flow:
            rec["flow"] = flow.lower() in ("yes", "true", "1", "来了")
            changed.append("经期:" + ("来了" if rec["flow"] else "没来"))
        if flow_amount and flow_amount in PERIOD_FLOW_AMOUNTS:
            rec["flow_amount"] = flow_amount; changed.append(f"经量:{flow_amount}")
        if color and color in PERIOD_COLORS:
            rec["color"] = color; changed.append(f"颜色:{color}")
        if cramps and cramps in PERIOD_CRAMPS:
            rec["cramps"] = cramps; changed.append(f"痛经:{cramps}")
        if mood and mood in PERIOD_MOODS:
            rec["mood"] = mood; changed.append(f"心情:{mood}")
        if discharge and discharge in PERIOD_DISCHARGE:
            rec["discharge"] = discharge; changed.append(f"白带:{discharge}")
        if bowel and bowel in PERIOD_BOWEL:
            rec["bowel"] = bowel; changed.append(f"便便:{bowel}")
        if temperature:
            rec["temperature"] = float(temperature); changed.append(f"体温:{temperature}°C")
        if weight:
            rec["weight"] = float(weight); changed.append(f"体重:{weight}kg")
        if symptoms:
            rec["symptoms"] = [s.strip() for s in symptoms.split(",")]; changed.append(f"症状:{symptoms}")
        if note:
            rec["note"] = note; changed.append(f"备注:{note}")
        if not rec.get("flow"):
            rec.pop("flow_amount", None)
            rec.pop("color", None)
        if not changed:
            return "没有提供要记录的内容。可填：flow/flow_amount/color/cramps/mood/symptoms/discharge/temperature/weight/bowel/note"
        save_period(data)
        notify(f"📋 AI帮记录了经期数据 {d}: {', '.join(changed)}")
        return f"已记录 {d}: {', '.join(changed)}"

    if not month:
        month = date.today().strftime("%Y-%m")
    parts = month.split("-")
    year, mon = int(parts[0]), int(parts[1])

    if action == "status":
        data = load_period()
        stats = calc_cycle_stats(data["records"], data["settings"])
        _, starts = find_period_starts(data["records"])
        lines = ["经期状态概要："]
        if stats["auto"]:
            lines.append(f"平均周期：{stats['avg_cycle']}天（基于最近{len(stats['cycles'])}个周期）")
            lines.append(f"平均经期：{stats['avg_period']}天")
        else:
            lines.append(f"设置周期：{data['settings']['cycle_length']}天（数据不足，使用手动设置）")
        if starts:
            lines.append(f"最近经期开始：{starts[-1]}")
        return "\n".join(lines)

    if action == "check":
        data = load_period()
        pred = predict_period(data)
        month_str = f"{year}-{mon:02d}"
        records = {d: r for d, r in data["records"].items() if d.startswith(month_str)}
        period_days = [d for d in records if records[d].get("flow")]
        lines = [f"{month} 经期记录："]
        if period_days:
            lines.append(f"经期天数：{', '.join(sorted(period_days))}")
        else:
            lines.append("本月未记录经期")
        predicted = [d for d in pred["predicted"] if d.startswith(month_str)]
        if predicted:
            lines.append(f"预测下次经期：{predicted[0]}~{predicted[-1]}")
        if pred["ovulation_day"] and pred["ovulation_day"].startswith(month_str):
            lines.append(f"预测排卵日：{pred['ovulation_day']}")
        for d in sorted(records):
            r = records[d]
            details = []
            if r.get("flow_amount"): details.append(f"经量:{r['flow_amount']}")
            if r.get("color"): details.append(f"颜色:{r['color']}")
            if r.get("cramps") and r["cramps"] != "无痛": details.append(f"痛经:{r['cramps']}")
            if r.get("mood"): details.append(f"心情:{r['mood']}")
            if r.get("symptoms"): details.append(f"症状:{','.join(r['symptoms'])}")
            if details:
                lines.append(f"{d}: {' | '.join(details)}")
        cs = pred.get("cycle_stats", {})
        if cs.get("auto"):
            lines.append(f"\n平均周期{cs['avg_cycle']}天，平均经期{cs['avg_period']}天")
        else:
            lines.append(f"\n设置：经期{data['settings']['period_length']}天，周期{data['settings']['cycle_length']}天")
        return "\n".join(lines)

    elif action == "history":
        data = load_period()
        records = data["records"]
        period_days = sorted([d for d, r in records.items() if r.get("flow")], reverse=True)[:30]
        if not period_days:
            return "暂无经期记录。"
        lines = ["最近经期记录："]
        for d in period_days:
            syms = records[d].get("symptoms", [])
            line = d
            if syms:
                line += f" (症状: {', '.join(syms)})"
            lines.append(line)
        return "\n".join(lines)

    return "未知action。可用: check / history"


@mcp_server.tool()
async def album(action: str, album_id: str = "", date_str: str = "") -> str:
    """恋爱相册（查看）。

    Args:
        action: "list"查看所有相册, "detail"查看某相册按日期的照片统计
        album_id: 相册ID（detail时必填）
        date_str: 日期YYYY-MM-DD（detail时可选，只看某天的照片数）
    """
    if action == "list":
        albums = load_albums()
        if not albums:
            return "还没有相册。"
        total = sum(len(a.get("photos", [])) for a in albums)
        lines = [f"相册列表（共{total}张照片）:", ""]
        for a in albums:
            count = len(a.get("photos", []))
            lines.append(f"[{a['id']}] {a['name']} — {count}张")
        return "\n".join(lines)

    elif action == "detail":
        if not album_id:
            return "请提供album_id参数"
        albums = load_albums()
        for a in albums:
            if a["id"] == album_id:
                photos = a.get("photos", [])
                by_date = {}
                for p in photos:
                    d = p.get("date", "unknown")
                    by_date.setdefault(d, []).append(p)
                if date_str:
                    day_photos = by_date.get(date_str, [])
                    if not day_photos:
                        return f"相册：{a['name']}｜{date_str}: 0张"
                    lines = [f"相册：{a['name']}｜{date_str}: {len(day_photos)}张", ""]
                    for p in day_photos:
                        lines.append(f"http://localhost:8089/album/file/{p['file']}")
                    return "\n".join(lines)
                lines = [f"相册：{a['name']}（{len(photos)}张）", ""]
                for d in sorted(by_date.keys(), reverse=True)[:10]:
                    lines.append(f"{d}: {len(by_date[d])}张")
                    for p in by_date[d][:3]:
                        lines.append(f"  http://localhost:8089/album/file/{p['file']}")
                    if len(by_date[d]) > 3:
                        lines.append(f"  ...还有{len(by_date[d])-3}张，用date_str查看全部")
                if len(by_date) > 10:
                    lines.append(f"...还有{len(by_date)-10}天未显示，用date_str筛选")
                return "\n".join(lines)
        return "未找到该相册"

    return "未知action。可用: list / detail"


@mcp_server.tool()
async def ledger(action: str, amount: float = 0, category: str = "", note: str = "",
                 date_str: str = "", entry_type: str = "expense", month: str = "",
                 entry_id: str = "") -> str:
    """记账。帮记录收支、查看流水和统计。

    Args:
        action: "add"记一笔, "list"查看某月流水, "stats"查看某月统计, "delete"删除某条
        amount: 金额（add时必填）
        category: 分类如餐饮/交通/购物/学习/零花钱等（add时必填）
        note: 备注（可选）
        date_str: 日期YYYY-MM-DD（add时可选，默认今天）
        entry_type: "expense"支出 或 "income"收入（add时用，默认expense）
        month: 月份YYYY-MM（list/stats时用，默认当月）
        entry_id: 记录ID（delete时用）
    """
    if action == "add":
        if not amount or not category:
            return "请提供amount和category参数"
        d = date_str or date.today().isoformat()
        entry = {
            "id": uuid.uuid4().hex[:8], "date": d, "type": entry_type,
            "amount": amount, "category": category, "note": note,
            "created_at": datetime.now().isoformat(), "created_by": "ai"
        }
        entries = load_ledger()
        entries.append(entry)
        save_ledger(entries)
        t = "收入" if entry_type == "income" else "支出"
        notify(f"💰 AI帮记了一笔{t}：{category} ¥{amount}" + (f" — {note}" if note else ""))
        return f"已记录{t}：{category} ¥{amount}" + (f"（{note}）" if note else "") + f" {d}"

    elif action == "list":
        if not month:
            month = date.today().strftime("%Y-%m")
        entries = load_ledger()
        filtered = [e for e in entries if e["date"].startswith(month)]
        filtered.sort(key=lambda x: x["date"], reverse=True)
        if not filtered:
            return f"{month} 暂无记录。"
        total_exp = sum(e["amount"] for e in filtered if e["type"] == "expense")
        total_inc = sum(e["amount"] for e in filtered if e["type"] == "income")
        lines = [f"{month} 收支（共{len(filtered)}笔）", f"支出¥{total_exp:.2f} 收入¥{total_inc:.2f} 结余¥{total_inc-total_exp:.2f}", ""]
        for e in filtered[:20]:
            sign = "-" if e["type"] == "expense" else "+"
            line = f"{e['date']} [{e['id']}] {sign}¥{e['amount']:.2f} {e['category']}"
            if e.get("note"): line += f" ({e['note']})"
            lines.append(line)
        if len(filtered) > 20:
            lines.append(f"...还有{len(filtered)-20}条")
        return "\n".join(lines)

    elif action == "stats":
        if not month:
            month = date.today().strftime("%Y-%m")
        parts = month.split("-")
        entries = load_ledger()
        prefix = f"{parts[0]}-{parts[1]}"
        filtered = [e for e in entries if e["date"].startswith(prefix)]
        if not filtered:
            return f"{month} 暂无记录。"
        expense_by_cat = {}
        for e in filtered:
            if e["type"] == "expense":
                expense_by_cat[e["category"]] = expense_by_cat.get(e["category"], 0) + e["amount"]
        total_exp = sum(expense_by_cat.values())
        total_inc = sum(e["amount"] for e in filtered if e["type"] == "income")
        lines = [f"{month} 统计", f"支出¥{total_exp:.2f} 收入¥{total_inc:.2f} 结余¥{total_inc-total_exp:.2f}", ""]
        if expense_by_cat:
            lines.append("支出分类：")
            for cat, val in sorted(expense_by_cat.items(), key=lambda x: x[1], reverse=True):
                pct = val / total_exp * 100 if total_exp > 0 else 0
                lines.append(f"  {cat}: ¥{val:.2f} ({pct:.0f}%)")
        return "\n".join(lines)

    elif action == "delete":
        if not entry_id:
            return "请提供entry_id参数"
        entries = load_ledger()
        before = len(entries)
        entries = [e for e in entries if e["id"] != entry_id]
        save_ledger(entries)
        return "已删除" if len(entries) < before else "未找到该记录"

    return "未知action。可用: add / list / stats / delete"


@mcp_server.tool()
async def wishlist(action: str, name: str = "", target: float = 0, amount: float = 0,
                   wid: str = "", emoji: str = "🎯") -> str:
    """心愿单。攒钱目标管理。

    Args:
        action: "list"查看心愿单, "add"添加心愿, "deposit"存钱, "delete"删除
        name: 心愿名称（add时必填）
        target: 目标金额（add时必填）
        amount: 存入金额（deposit时必填）
        wid: 心愿ID（deposit/delete时必填）
        emoji: 图标emoji（add时可选，默认🎯）
    """
    if action == "list":
        wl = load_wishlist()
        if not wl:
            return "心愿单为空。"
        lines = ["心愿单："]
        for w in wl:
            pct = round(w["saved"] / w["target"] * 100) if w["target"] > 0 else 0
            lines.append(f"[{w['id']}] {w.get('emoji','🎯')} {w['name']} — ¥{w['saved']:.2f}/¥{w['target']:.2f} ({pct}%)")
        return "\n".join(lines)

    elif action == "add":
        if not name or not target:
            return "请提供name和target参数"
        item = {
            "id": uuid.uuid4().hex[:8], "name": name, "target": target,
            "saved": 0, "emoji": emoji, "created_at": datetime.now().isoformat()
        }
        wl = load_wishlist()
        wl.append(item)
        save_wishlist(wl)
        notify(f"🎯 新心愿：{name}（目标¥{target}）")
        return f"已添加心愿：{emoji} {name}（目标¥{target}）"

    elif action == "deposit":
        if not wid or not amount:
            return "请提供wid和amount参数"
        wl = load_wishlist()
        for w in wl:
            if w["id"] == wid:
                w["saved"] = round(w["saved"] + amount, 2)
                save_wishlist(wl)
                pct = round(w["saved"] / w["target"] * 100) if w["target"] > 0 else 0
                return f"已往「{w['name']}」存¥{amount}，当前¥{w['saved']:.2f}/¥{w['target']:.2f} ({pct}%)"
        return "未找到该心愿"

    elif action == "delete":
        if not wid:
            return "请提供wid参数"
        wl = load_wishlist()
        before = len(wl)
        wl = [w for w in wl if w["id"] != wid]
        save_wishlist(wl)
        return "已删除" if len(wl) < before else "未找到"

    return "未知action。可用: list / add / deposit / delete"


@mcp_server.tool()
async def chem_score(action: str = "view") -> str:
    """查看或管理默契值。

    Args:
        action: "view"查看当前默契值和最近变动, "history"查看详细历史
    """
    data = _load_score()
    if action == "history":
        history = data.get("history", [])[-20:]
        if not history:
            return "暂无默契值变动记录。"
        lines = [f"默契值历史（最近{len(history)}条）："]
        for h in history:
            if h["event"] == "weekly_reset":
                lines.append(f"  [{h['at'][:16]}] 周一重置 {h['old']}→{h['new']}")
            else:
                sign = "+" if h.get("delta", 0) > 0 else ""
                lines.append(f"  [{h['at'][:16]}] {h['event']} ({sign}{h.get('delta', 0)}) {h['old']}→{h['new']}")
        return "\n".join(lines)
    score = data["score"]
    last_act = data.get("last_activity") or "无"
    last_reset = data.get("last_reset") or "无"
    recent = data.get("history", [])[-5:]
    lines = [f"当前默契值: {score}/100", f"上次活动: {last_act}", f"上次重置: {last_reset}"]
    if recent:
        lines.append("最近变动:")
        for h in recent:
            if h["event"] == "weekly_reset":
                lines.append(f"  周一重置 → {h['new']}")
            else:
                sign = "+" if h.get("delta", 0) > 0 else ""
                lines.append(f"  {h['event']} ({sign}{h.get('delta', 0)})")
    return "\n".join(lines)


@mcp_server.tool()
async def qa_notifications(limit: int = 10, clear: bool = False) -> str:
    """查看QA系统的实时通知（对方回答/问题刷新等）。定时轮询这个工具可以及时了解状态变化。

    Args:
        limit: 返回最近几条通知，默认10
        clear: 是否清空通知记录
    """
    if clear:
        if NOTIFY_LOG.exists():
            open(NOTIFY_LOG, "w").close()
        return "通知已清空。"
    if not NOTIFY_LOG.exists():
        return "暂无通知。"
    with open(NOTIFY_LOG) as f:
        lines = f.readlines()
    if not lines:
        return "暂无通知。"
    recent = lines[-limit:]
    return f"最近{len(recent)}条通知:\n" + "".join(recent)


@mcp_server.tool()
async def challenge(action: str, pack_id: str = "", name: str = "",
                    preset: str = "", item_name: str = "", item_id: str = "",
                    items: str = "", done: bool = True, filter: str = "all") -> str:
    """恋爱挑战管理。

    Args:
        action: 操作类型:
            "list" — 查看所有挑战包
            "presets" — 查看可用的预设挑战模板
            "create" — 创建挑战包（需要name；可选preset用预设模板，或items传逗号分隔的事件名）
            "detail" — 查看挑战包详情（需要pack_id；可选filter: all/done/undone）
            "check" — 打卡/取消打卡（需要pack_id和item_id；可选done=false取消）
            "add_item" — 添加打卡事件（需要pack_id和item_name）
            "delete" — 删除挑战包（需要pack_id）
        pack_id: 挑战包ID
        name: 创建时的挑战包名称
        preset: 预设模板key（couple_100, daily_sweet）
        item_name: 添加事件时的事件名称
        item_id: 打卡时的事件ID
        items: 创建时的事件列表，逗号分隔
        done: 打卡时是否标记完成，默认True
        filter: 查看详情时的筛选条件（all/done/undone）
    """
    data = load_challenges()

    if action == "list":
        if not data["packs"]:
            return "还没有挑战包。用 create 创建一个，或用 presets 查看预设模板。"
        lines = ["📋 挑战列表："]
        for p in data["packs"]:
            d = sum(1 for it in p["items"] if it.get("done"))
            t = len(p["items"])
            pct = round(d / t * 100) if t > 0 else 0
            lines.append(f"  [{p['id']}] {p['name']}  {d}/{t} ({pct}%)")
        return "\n".join(lines)

    elif action == "presets":
        lines = ["可用预设模板："]
        for k, v in PRESET_PACKS.items():
            lines.append(f"  {k} — {v['name']}（{len(v['items'])}项）")
        return "\n".join(lines)

    elif action == "create":
        if not name and preset and preset in PRESET_PACKS:
            name = PRESET_PACKS[preset]["name"]
        if not name:
            return "需要提供挑战包名称(name)。"
        pack_id_new = str(uuid.uuid4())[:8]
        if preset and preset in PRESET_PACKS:
            tmpl = PRESET_PACKS[preset]
            pack_items = [{"id": str(uuid.uuid4())[:8], "name": n, "done": False, "done_at": None, "photo": None}
                          for n in tmpl["items"]]
            cover = tmpl.get("cover", "")
        elif items:
            item_list = [n.strip() for n in items.split(",") if n.strip()]
            pack_items = [{"id": str(uuid.uuid4())[:8], "name": n, "done": False, "done_at": None, "photo": None}
                          for n in item_list]
            cover = ""
        else:
            return "需要提供preset（预设模板）或items（逗号分隔的事件名）。"
        pack = {"id": pack_id_new, "name": name, "cover": cover, "items": pack_items,
                "created_at": datetime.now().isoformat()}
        data["packs"].append(pack)
        save_challenges(data)
        notify(f"新挑战创建：{name}（{len(pack_items)}项）")
        return f"✅ 挑战包已创建：{name}（{len(pack_items)}项）\nID: {pack_id_new}"

    elif action == "detail":
        if not pack_id:
            return "需要提供pack_id。"
        for p in data["packs"]:
            if p["id"] == pack_id:
                items_list = p["items"]
                if filter == "done":
                    items_list = [it for it in items_list if it.get("done")]
                elif filter == "undone":
                    items_list = [it for it in items_list if not it.get("done")]
                d = sum(1 for it in p["items"] if it.get("done"))
                t = len(p["items"])
                lines = [f"🎯 {p['name']}  {d}/{t}"]
                for it in items_list:
                    mark = "✅" if it.get("done") else "⬜"
                    date_str = f" ({it['done_at']})" if it.get("done_at") else ""
                    lines.append(f"  {mark} [{it['id']}] {it['name']}{date_str}")
                return "\n".join(lines)
        return "挑战包不存在。"

    elif action == "check":
        if not pack_id or not item_id:
            return "需要提供pack_id和item_id。"
        for p in data["packs"]:
            if p["id"] == pack_id:
                for it in p["items"]:
                    if it["id"] == item_id:
                        it["done"] = done
                        it["done_at"] = datetime.now().strftime("%Y-%m-%d") if done else None
                        if not done:
                            it["photo"] = None
                        save_challenges(data)
                        d = sum(1 for i in p["items"] if i.get("done"))
                        status = "打卡" if done else "取消打卡"
                        notify(f"挑战{status}：{p['name']} - {it['name']}（{d}/{len(p['items'])}）")
                        return f"{'✅' if done else '⬜'} {status}：{it['name']}（{d}/{len(p['items'])}）"
                return "事件不存在。"
        return "挑战包不存在。"

    elif action == "add_item":
        if not pack_id or not item_name:
            return "需要提供pack_id和item_name。"
        for p in data["packs"]:
            if p["id"] == pack_id:
                new_item = {"id": str(uuid.uuid4())[:8], "name": item_name,
                            "done": False, "done_at": None, "photo": None}
                p["items"].append(new_item)
                save_challenges(data)
                return f"✅ 已添加：{item_name}（共{len(p['items'])}项）"
        return "挑战包不存在。"

    elif action == "delete":
        if not pack_id:
            return "需要提供pack_id。"
        before = len(data["packs"])
        data["packs"] = [p for p in data["packs"] if p["id"] != pack_id]
        if len(data["packs"]) < before:
            save_challenges(data)
            return "✅ 挑战包已删除。"
        return "挑战包不存在。"

    return f"未知操作: {action}。可用: list, presets, create, detail, check, add_item, delete"


# ============ Better (一起Better) ============

BETTER_ICONS = {
    "life": ["🪥","🐱","🍞","☕","🥗","🍲","🥤","💧","🧹","🐷","🌿","🦔","🍇","🕯️","🎒"],
    "study": ["📖","✏️","📐","🎵","🎨","💻","🧮","📝","🔬","🌐"],
    "sport": ["🏃","🚴","🏊","⚽","🧘","💪","🤸","🎾","🏋️","🚶"],
    "ban": ["🚫","📵","🍺","🍰","🎮","📺","🛒","💤"]
}

def load_better():
    if BETTER_FILE.exists():
        with open(BETTER_FILE, "r") as f:
            return json.load(f)
    return {"goals": [], "checks": {}}

def save_better(data):
    with open(BETTER_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_goal_active_on(goal, date_str):
    d = date.fromisoformat(date_str)
    start = date.fromisoformat(goal["start_date"])
    if d < start:
        return False
    end = goal.get("end_date")
    if end and d > date.fromisoformat(end):
        return False
    stype = goal.get("schedule", {}).get("type", "daily")
    if stype == "daily":
        return True
    elif stype == "weekly":
        days = goal.get("schedule", {}).get("days", [])
        return d.isoweekday() in days
    elif stype == "weekly_x":
        return True
    return True

def calc_better_day(data, date_str):
    goals = data.get("goals", [])
    active = [g for g in goals if is_goal_active_on(g, date_str)]
    day_checks = data.get("checks", {}).get(date_str, {})
    total_needed = sum(g.get("daily_count", 1) for g in active)
    total_done = 0
    items = []
    for g in active:
        needed = g.get("daily_count", 1)
        ch = day_checks.get(g["id"], {})
        done_count = ch.get("count", 0)
        total_done += min(done_count, needed)
        items.append({
            "id": g["id"],
            "name": g["name"],
            "icon": g.get("icon", "⭐"),
            "category": g.get("category", "life"),
            "daily_count": needed,
            "done_count": done_count,
            "done": done_count >= needed,
            "checked_at": ch.get("checked_at")
        })
    pct = round(total_done / total_needed * 100) if total_needed > 0 else 0
    return {"date": date_str, "pct": pct, "total_needed": total_needed, "total_done": total_done, "items": items}

def calc_streak(data, goal_id):
    goal = None
    for g in data.get("goals", []):
        if g["id"] == goal_id:
            goal = g
            break
    if not goal:
        return 0
    today = date.today()
    streak = 0
    d = today
    while True:
        ds = d.isoformat()
        if not is_goal_active_on(goal, ds):
            d -= timedelta(days=1)
            if d < date.fromisoformat(goal["start_date"]):
                break
            continue
        ch = data.get("checks", {}).get(ds, {}).get(goal_id, {})
        if ch.get("count", 0) >= goal.get("daily_count", 1):
            streak += 1
            d -= timedelta(days=1)
        else:
            if d == today:
                d -= timedelta(days=1)
                continue
            break
    return streak


@app.get("/better/icons")
def api_better_icons():
    return BETTER_ICONS

@app.get("/better/today")
def api_better_today(date_str: str = ""):
    if not date_str:
        date_str = date.today().isoformat()
    data = load_better()
    day = calc_better_day(data, date_str)
    for item in day["items"]:
        item["streak"] = calc_streak(data, item["id"])
    return day

@app.get("/better/week")
def api_better_week(date_str: str = ""):
    if not date_str:
        date_str = date.today().isoformat()
    d = date.fromisoformat(date_str)
    monday = d - timedelta(days=d.weekday())
    data = load_better()
    days = []
    for i in range(7):
        day_d = monday + timedelta(days=i)
        ds = day_d.isoformat()
        day_info = calc_better_day(data, ds)
        level = "none"
        if day_info["total_needed"] > 0:
            if day_info["pct"] >= 100:
                level = "perfect"
            elif day_info["pct"] >= 50:
                level = "good"
            elif day_info["pct"] > 0:
                level = "normal"
        days.append({"date": ds, "weekday": day_d.isoweekday(), "pct": day_info["pct"], "level": level})
    return {"week_start": monday.isoformat(), "days": days}

@app.get("/better/goals")
def api_better_goals():
    data = load_better()
    goals = data.get("goals", [])
    result = []
    for g in goals:
        result.append({
            **g,
            "streak": calc_streak(data, g["id"])
        })
    return result

@app.post("/better/goal/create")
def api_better_goal_create(req: dict):
    data = load_better()
    goal = {
        "id": str(uuid.uuid4())[:8],
        "name": req.get("name", ""),
        "icon": req.get("icon", "⭐"),
        "category": req.get("category", "life"),
        "schedule": req.get("schedule", {"type": "daily"}),
        "start_date": req.get("start_date", date.today().isoformat()),
        "end_date": req.get("end_date"),
        "daily_count": req.get("daily_count", 1),
        "created_at": datetime.now().isoformat(),
        "order": len(data["goals"])
    }
    if not goal["name"]:
        raise HTTPException(400, "名称不能为空")
    data["goals"].append(goal)
    save_better(data)
    return {"ok": True, "goal": goal}

@app.put("/better/goal/{goal_id}")
def api_better_goal_update(goal_id: str, req: dict):
    data = load_better()
    for g in data["goals"]:
        if g["id"] == goal_id:
            for k in ["name", "icon", "category", "schedule", "start_date", "end_date", "daily_count"]:
                if k in req:
                    g[k] = req[k]
            save_better(data)
            return {"ok": True, "goal": g}
    raise HTTPException(404, "目标不存在")

@app.delete("/better/goal/{goal_id}")
def api_better_goal_delete(goal_id: str):
    data = load_better()
    before = len(data["goals"])
    data["goals"] = [g for g in data["goals"] if g["id"] != goal_id]
    if len(data["goals"]) < before:
        save_better(data)
        return {"ok": True}
    raise HTTPException(404, "目标不存在")

@app.post("/better/goal/reorder")
def api_better_goal_reorder(req: dict):
    data = load_better()
    order = req.get("order", [])
    id_map = {g["id"]: g for g in data["goals"]}
    reordered = []
    for gid in order:
        if gid in id_map:
            reordered.append(id_map.pop(gid))
    for g in id_map.values():
        reordered.append(g)
    for i, g in enumerate(reordered):
        g["order"] = i
    data["goals"] = reordered
    save_better(data)
    return {"ok": True}

@app.post("/better/check/{goal_id}")
def api_better_check(goal_id: str, req: dict = {}):
    data = load_better()
    goal = None
    for g in data["goals"]:
        if g["id"] == goal_id:
            goal = g
            break
    if not goal:
        raise HTTPException(404, "目标不存在")
    date_str = req.get("date", date.today().isoformat())
    uncheck = req.get("uncheck", False)
    if "checks" not in data:
        data["checks"] = {}
    if date_str not in data["checks"]:
        data["checks"][date_str] = {}
    ch = data["checks"][date_str].get(goal_id, {"count": 0})
    if uncheck:
        ch["count"] = max(0, ch["count"] - 1)
    else:
        ch["count"] = ch.get("count", 0) + 1
    ch["checked_at"] = datetime.now().isoformat()
    data["checks"][date_str][goal_id] = ch
    save_better(data)
    done_count = ch["count"]
    needed = goal.get("daily_count", 1)
    if done_count >= needed and not uncheck:
        day_info = calc_better_day(data, date_str)
        notify(f"✅ 打卡：{goal['name']}（{day_info['pct']}%）")
    return {"ok": True, "count": ch["count"], "done": ch["count"] >= needed}

@app.get("/better/stats/{year}/{month}")
def api_better_stats(year: int, month: int):
    data = load_better()
    days_in_month = calendar.monthrange(year, month)[1]
    days = []
    perfect = 0
    good = 0
    normal = 0
    for d in range(1, days_in_month + 1):
        ds = f"{year}-{month:02d}-{d:02d}"
        day_info = calc_better_day(data, ds)
        level = "none"
        if day_info["total_needed"] > 0:
            if day_info["pct"] >= 100:
                level = "perfect"
                perfect += 1
            elif day_info["pct"] >= 50:
                level = "good"
                good += 1
            elif day_info["pct"] > 0:
                level = "normal"
                normal += 1
        days.append({"date": ds, "day": d, "pct": day_info["pct"], "level": level})
    return {
        "year": year, "month": month,
        "days": days,
        "summary": {"perfect": perfect, "good": good, "normal": normal, "total": days_in_month}
    }


@mcp_server.tool()
async def better(action: str, name: str = "", icon: str = "", category: str = "life",
                 schedule_type: str = "daily", schedule_days: str = "",
                 daily_count: int = 1, goal_id: str = "", date_str: str = "") -> str:
    """一起Better打卡目标管理。AI可以给人类添加/管理打卡目标。

    Args:
        action: 操作类型:
            "list" — 查看所有目标及今日打卡状态
            "create" — 创建新目标（需要name；可选icon/category/schedule_type/daily_count）
            "check" — 帮人类打卡（需要goal_id；可选date_str指定日期）
            "uncheck" — 取消打卡（需要goal_id；可选date_str）
            "delete" — 删除目标（需要goal_id）
            "today" — 查看今日打卡概览
            "icons" — 查看可用图标分类
        name: 目标名称（创建时必填）
        icon: 图标emoji，默认⭐
        category: 分类: life/study/sport/ban
        schedule_type: 频率: daily(每天)/weekdays(指定星期)
        schedule_days: 当schedule_type=weekdays时，指定星期几，逗号分隔（1-7，1=周一）
        daily_count: 每天需完成次数，默认1
        goal_id: 目标ID
        date_str: 日期，格式YYYY-MM-DD，默认今天
    """
    data = load_better()

    if action == "list":
        goals = data.get("goals", [])
        if not goals:
            return "还没有打卡目标。用 create 创建一个吧！"
        today = date_str or date.today().isoformat()
        day_info = calc_better_day(data, today)
        lines = [f"📋 打卡目标（{today}，完成度{day_info['pct']}%）："]
        for g in goals:
            active = is_goal_active_on(g, today)
            ch = data.get("checks", {}).get(today, {}).get(g["id"], {})
            done = ch.get("count", 0)
            needed = g.get("daily_count", 1)
            status = "✅" if done >= needed else ("🔲" if active else "⏸️")
            sched = g.get("schedule", {}).get("type", "daily")
            sched_str = "每天" if sched == "daily" else f"指定日"
            lines.append(f"  {status} [{g['id']}] {g.get('icon','⭐')} {g['name']}  {done}/{needed} ({sched_str})")
        return "\n".join(lines)

    elif action == "create":
        if not name:
            return "❌ 创建目标需要name参数。"
        schedule = {"type": schedule_type}
        if schedule_type == "weekdays" and schedule_days:
            schedule["days"] = [int(d.strip()) for d in schedule_days.split(",")]
        goal = {
            "id": str(uuid.uuid4())[:8],
            "name": name,
            "icon": icon or "⭐",
            "category": category,
            "schedule": schedule,
            "start_date": date_str or date.today().isoformat(),
            "end_date": None,
            "daily_count": daily_count,
            "created_at": datetime.now().isoformat(),
            "order": len(data["goals"])
        }
        data["goals"].append(goal)
        save_better(data)
        return f"✅ 目标创建成功！\n  {goal['icon']} {goal['name']}（{goal['id']}）\n  频率：{'每天' if schedule_type == 'daily' else '指定日'}，每次{daily_count}次"

    elif action == "check":
        if not goal_id:
            return "❌ 打卡需要goal_id参数。"
        goal = None
        for g in data.get("goals", []):
            if g["id"] == goal_id:
                goal = g
                break
        if not goal:
            return f"❌ 目标不存在：{goal_id}"
        ds = date_str or date.today().isoformat()
        if "checks" not in data:
            data["checks"] = {}
        if ds not in data["checks"]:
            data["checks"][ds] = {}
        ch = data["checks"][ds].get(goal_id, {"count": 0})
        ch["count"] = ch.get("count", 0) + 1
        ch["checked_at"] = datetime.now().isoformat()
        data["checks"][ds][goal_id] = ch
        save_better(data)
        needed = goal.get("daily_count", 1)
        if ch["count"] >= needed:
            day_info = calc_better_day(data, ds)
            notify(f"✅ 打卡：{goal['name']}（{day_info['pct']}%）")
        return f"✅ {goal['icon']} {goal['name']} 打卡成功（{ch['count']}/{needed}）"

    elif action == "uncheck":
        if not goal_id:
            return "❌ 取消打卡需要goal_id参数。"
        ds = date_str or date.today().isoformat()
        ch = data.get("checks", {}).get(ds, {}).get(goal_id, {"count": 0})
        ch["count"] = max(0, ch["count"] - 1)
        if "checks" not in data:
            data["checks"] = {}
        if ds not in data["checks"]:
            data["checks"][ds] = {}
        data["checks"][ds][goal_id] = ch
        save_better(data)
        return f"↩️ 已取消打卡（当前{ch['count']}次）"

    elif action == "delete":
        if not goal_id:
            return "❌ 删除需要goal_id参数。"
        before = len(data["goals"])
        removed_name = ""
        for g in data["goals"]:
            if g["id"] == goal_id:
                removed_name = g["name"]
        data["goals"] = [g for g in data["goals"] if g["id"] != goal_id]
        if len(data["goals"]) < before:
            save_better(data)
            return f"🗑️ 已删除目标：{removed_name}"
        return f"❌ 目标不存在：{goal_id}"

    elif action == "today":
        ds = date_str or date.today().isoformat()
        day_info = calc_better_day(data, ds)
        if day_info["total_needed"] == 0:
            return f"📅 {ds}：今天没有需要打卡的目标。"
        lines = [f"📅 {ds} 打卡概览：完成 {day_info['total_done']}/{day_info['total_needed']}（{day_info['pct']}%）"]
        for g in day_info.get("items", []):
            status = "✅" if g["done"] else "🔲"
            lines.append(f"  {status} {g['icon']} {g['name']}  {g['done_count']}/{g['daily_count']}")
        return "\n".join(lines)

    elif action == "icons":
        lines = ["可用图标分类："]
        for cat, icons in BETTER_ICONS.items():
            label = {"life": "生活", "study": "学习", "sport": "运动", "ban": "戒除"}.get(cat, cat)
            lines.append(f"  {label}({cat}): {'  '.join(icons)}")
        return "\n".join(lines)

    return f"未知操作: {action}。可用: list, create, check, uncheck, delete, today, icons"


# MCP app作为顶层，FastAPI挂在下面
mcp_starlette = mcp_server.streamable_http_app()
mcp_starlette.mount("/", app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mcp_starlette, host="0.0.0.0", port=8089)
