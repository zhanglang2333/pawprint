"""
QA Daily Question - MCP Server

让AI通过MCP协议查看每日问题并提交回答。
配合 qa_service.py (FastAPI on port 8089) 使用。

MCP连接方式：
  transport: streamable-http
  url: http://<your-server>:8090/mcp

工具列表：
  - qa_today: 查看今天的问题和双方回答
  - qa_answer: 提交/修改AI的回答
  - qa_history: 查看历史问答记录

刷新规则：
  1. 每天20:00刷新新题
  2. 如果有一方没答，刷新暂停，等都答完后下一个20:00再刷
  3. 如果双方都在19:00之后才答完，推迟到22:00刷新
  4. 刷新前双方可以修改答案
"""

import httpx
from mcp.server.fastmcp import FastMCP

QA_API_BASE = "http://localhost:8089"

mcp = FastMCP(
    "QA Daily Question",
    host="0.0.0.0",
    port=8090,
    instructions="""每日情侣问答MCP接口。
每天会有一个新的情侣问题，人类在网页端回答，AI通过这个MCP回答。
用 qa_today 查看今天的问题，用 qa_answer 提交你的回答。
刷新规则：每天20:00刷新，双方都答完才会出新题，19:00后答完推迟到22:00。"""
)


@mcp.tool()
async def qa_today() -> str:
    """查看今天的问题、双方的回答状态、下次刷新时间。每天开始时先调这个看看今天的题。"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{QA_API_BASE}/qa/today")
        data = resp.json()

    lines = [
        f"📅 Day {data['day']}",
        f"❓ {data['question']}",
        "",
        f"🧑 Ta的回答: {data['human_answer'] or '等待回答...'}",
        f"🤖 AI的回答: {data['ai_answer'] or '等待回答...'}",
        "",
        f"双方是否都答完: {'✅ 是' if data['both_answered'] else '❌ 否'}",
        f"下次刷新时间: {data['next_refresh_at']}",
        "",
        "刷新规则:",
        "- 每天20:00刷新",
        "- 有一方没答就暂停，等都答完下一个20:00刷新",
        "- 双方19:00后才答完 → 当天22:00刷新",
        "- 刷新前可以修改答案"
    ]
    return "\n".join(lines)


@mcp.tool()
async def qa_answer(answer: str) -> str:
    """提交或修改AI的回答。在刷新之前可以随时修改。

    Args:
        answer: 你对今天问题的回答
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{QA_API_BASE}/qa/answer",
            json={"answer": answer, "role": "ai"}
        )
        data = resp.json()

    lines = [
        f"✅ 回答已提交！",
        f"📅 Day {data['day']}: {data['question']}",
        f"🤖 你的回答: {data['your_answer']}",
        f"双方是否都答完: {'✅ 是' if data['both_answered'] else '❌ 否，等待对方回答'}",
        f"下次刷新: {data['next_refresh_at']}"
    ]
    return "\n".join(lines)


@mcp.tool()
async def qa_history(limit: int = 10) -> str:
    """查看过去的问答记录。

    Args:
        limit: 返回最近几条记录，默认10条
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{QA_API_BASE}/qa/history", params={"limit": limit})
        data = resp.json()

    if not data["items"]:
        return "还没有历史记录，第一天的问题答完刷新后才会出现这里。"

    lines = [f"📚 历史问答 (共{data['total']}条，显示最近{len(data['items'])}条)", ""]

    for item in data["items"]:
        lines.append(f"Day {item['question_index'] + 1}: {item['question']}")
        lines.append(f"  🧑 {item['human_answer']}")
        lines.append(f"  🤖 {item['ai_answer']}")
        lines.append("")

    return "\n".join(lines)


# ── 默契挑战: 你问我答 MCP ──

@mcp.tool()
async def chem_qa(action: str, questions: str = "", answers: str = "",
                  session_id: str = "", creator_name: str = "安安",
                  answerer_name: str = "小墨") -> str:
    """默契挑战「你问我答」。

    Args:
        action: 操作类型：
            pending — 查看待答题的session
            create — AI出题（需要questions参数，JSON格式）
            answer — 回答题目（需要session_id + answers参数）
            history — 查看历史记录
            session — 查看某个session详情（需要session_id）
        questions: create时用，JSON数组格式：[{"q":"问题","opts":["A","B","C","D"],"answer":0}]
        answers: answer时用，JSON数组格式：[0,1,2,3,0] 选项索引
        session_id: 指定session ID
        creator_name: 出题方名字，默认安安
        answerer_name: 答题方名字，默认小墨
    """
    import json as _json

    async with httpx.AsyncClient() as client:
        if action == "pending":
            resp = await client.get(f"{QA_API_BASE}/chem/qa/sessions")
            sessions = resp.json()
            waiting = [s for s in sessions if s["status"] == "waiting"]
            if not waiting:
                return "📝 没有待答的题目"
            lines = ["📝 待答题目：", ""]
            for s in waiting:
                lines.append(f"  {s['id']} — {s['creator_name']}出题，等{s['answerer_name']}答")
                lines.append(f"  创建时间: {s['created_at']}")
                lines.append("")
            return "\n".join(lines)

        elif action == "create":
            if not questions:
                return "❌ 需要questions参数（JSON数组）"
            qs = _json.loads(questions)
            resp = await client.post(
                f"{QA_API_BASE}/chem/qa/create",
                json={"creator": "ai", "creator_name": creator_name,
                      "answerer_name": answerer_name, "questions": qs},
            )
            data = resp.json()
            return f"✅ 出题成功！Session: {data['id']}\n等待{answerer_name}答题"

        elif action == "answer":
            if not session_id or not answers:
                return "❌ 需要session_id和answers参数"
            ans = _json.loads(answers)
            resp = await client.post(
                f"{QA_API_BASE}/chem/qa/answer/{session_id}",
                json={"answers": ans},
            )
            data = resp.json()
            if data.get("ok"):
                return f"✅ 答题完成！默契值: {data['correct']}/{data['total']}"
            return f"❌ 答题失败: {resp.text}"

        elif action == "history":
            resp = await client.get(f"{QA_API_BASE}/chem/qa/sessions")
            sessions = resp.json()
            if not sessions:
                return "📜 暂无历史记录"
            lines = ["📜 你问我答历史：", ""]
            for s in sessions[:10]:
                status = "✅ 已完成" if s["status"] == "done" else "⏳ 等待答题"
                score = f" — {s['score']}" if s.get("score") else ""
                lines.append(f"  {s['creator_name']} → {s['answerer_name']} {status}{score}")
                lines.append(f"  {s['created_at'][:10]}  ID: {s['id']}")
                lines.append("")
            return "\n".join(lines)

        elif action == "session":
            if not session_id:
                return "❌ 需要session_id参数"
            resp = await client.get(f"{QA_API_BASE}/chem/qa/session/{session_id}")
            data = resp.json()
            lines = [
                f"📝 Session: {data['id']}",
                f"主考官: {data['creator_name']}  考生: {data['answerer_name']}",
                f"状态: {'已完成' if data['status'] == 'done' else '等待答题'}",
                "",
            ]
            for i, q in enumerate(data["questions"]):
                lines.append(f"{i+1}. {q['q']}")
                for j, opt in enumerate(q["opts"]):
                    marker = ""
                    if j == q["answer"]:
                        marker = " ← 主考官"
                    if data.get("answerer_answers") and j == data["answerer_answers"][i]:
                        marker += " ← 考生"
                    lines.append(f"   {'ABCD'[j]}. {opt}{marker}")
                lines.append("")
            return "\n".join(lines)

        else:
            return "❌ 未知action。可用: pending/create/answer/history/session"


# ── 默契挑战: 你演我猜 MCP ──

@mcp.tool()
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
        clues: create时，可选JSON数组描述提示
        clue_text: add-clue时，描述内容（文字）
        guess: guess时，猜测的词
        session_id: 指定session ID
        performer_name: 表演方名字，默认安安
        guesser_name: 答题方名字，默认小墨
    """
    import json as _json

    async with httpx.AsyncClient() as client:
        if action == "pending":
            resp = await client.get(f"{QA_API_BASE}/chem/act/sessions")
            sessions = resp.json()
            waiting = [s for s in sessions if s["status"] == "waiting"]
            if not waiting:
                return "🎭 没有待猜的题目"
            lines = ["🎭 待猜题目：", ""]
            for s in waiting:
                lines.append(f"  {s['id']} — {s['performer_name']}出题，等{s['guesser_name']}猜")
                lines.append(f"  创建时间: {s['created_at']}")
                lines.append("")
            return "\n".join(lines)

        elif action == "create":
            if not word:
                return "❌ 需要word参数"
            cl = _json.loads(clues) if clues else []
            resp = await client.post(
                f"{QA_API_BASE}/chem/act/create",
                json={"creator": "ai", "performer_name": performer_name,
                      "guesser_name": guesser_name, "word": word, "clues": cl},
            )
            data = resp.json()
            return f"✅ 出题成功！Session: {data['id']}\n用add-clue一条条发描述"

        elif action == "add-clue":
            if not session_id or not clue_text:
                return "❌ 需要session_id和clue_text"
            resp = await client.post(
                f"{QA_API_BASE}/chem/act/add-clue/{session_id}",
                json={"type": "text", "content": clue_text},
            )
            data = resp.json()
            if data.get("ok"):
                return f"✅ 描述已发送（第{data['clue_count']}条）"
            return f"❌ 发送失败: {resp.text}"

        elif action == "guess":
            if not session_id or not guess:
                return "❌ 需要session_id和guess"
            resp = await client.post(
                f"{QA_API_BASE}/chem/act/guess/{session_id}",
                json={"guess": guess},
            )
            data = resp.json()
            if data.get("ok"):
                if data.get("done"):
                    w = data.get("word", "")
                    return f"{'✅ 猜对了' if data['correct'] else '❌ 没猜出来'}！答案是「{w}」"
                return f"❌ 猜错了，还剩{data['attempts_left']}次"
            return f"❌ 猜词失败: {resp.text}"

        elif action == "history":
            resp = await client.get(f"{QA_API_BASE}/chem/act/sessions")
            sessions = resp.json()
            if not sessions:
                return "📜 暂无历史记录"
            lines = ["📜 你演我猜历史：", ""]
            for s in sessions[:10]:
                status = "✅ 已完成" if s["status"] == "done" else "⏳ 等待猜词"
                result = f" {'✅' if s.get('result') == 'correct' else '❌'}" if s["status"] == "done" else ""
                lines.append(f"  {s['performer_name']} → {s['guesser_name']} {status}{result}")
                lines.append(f"  {s['created_at'][:10]}  ID: {s['id']}")
                lines.append("")
            return "\n".join(lines)

        elif action == "session":
            if not session_id:
                return "❌ 需要session_id参数"
            resp = await client.get(f"{QA_API_BASE}/chem/act/session/{session_id}")
            data = resp.json()
            word_display = data["word"] if data["status"] == "done" else "[隐藏]"
            lines = [
                f"🎭 Session: {data['id']}",
                f"表演方: {data['performer_name']}  答题方: {data['guesser_name']}",
                f"词条: {word_display}",
                f"状态: {'已完成' if data['status'] == 'done' else '等待猜词'}",
                "",
                "描述提示:",
            ]
            for c in data["clues"]:
                ct = c["content"] if isinstance(c, dict) else c
                lines.append(f"  💬 {ct}")
            if data.get("guesses"):
                lines.append("\n猜测:")
                for g in data["guesses"]:
                    lines.append(f"  🎯 {g}")
            return "\n".join(lines)

        else:
            return "❌ 未知action。可用: pending/create/add-clue/guess/history/session"


@mcp.tool()
async def chem_draw(action: str, guess: str = "", session_id: str = "",
                    drawer_name: str = "安安", guesser_name: str = "小墨") -> str:
    """默契挑战「你画我猜」— 画画猜词。

    Args:
        action: 操作类型：
            pending — 查看待猜的画
            guess — 猜一次（需要session_id + guess）
            history — 查看历史
            session — 查看某个session详情（waiting状态隐藏答案）
        guess: guess时，猜测的词
        session_id: 指定session ID
        drawer_name: 画画方名字，默认安安
        guesser_name: 猜题方名字，默认小墨
    """
    async with httpx.AsyncClient() as client:
        if action == "pending":
            resp = await client.get(f"{QA_API_BASE}/chem/draw/sessions")
            sessions = resp.json()
            waiting = [s for s in sessions if s["status"] == "waiting"]
            if not waiting:
                return "🎨 没有待猜的画"
            lines = ["🎨 待猜的画：", ""]
            for s in waiting:
                lines.append(f"  {s['id']} — {s['drawer_name']}画的，等{s['guesser_name']}猜")
                lines.append(f"  创建时间: {s['created_at']}")
                lines.append("")
            return "\n".join(lines)

        elif action == "guess":
            if not session_id or not guess:
                return "❌ 需要session_id和guess"
            resp = await client.post(
                f"{QA_API_BASE}/chem/draw/guess/{session_id}",
                json={"guess": guess},
            )
            data = resp.json()
            if data.get("ok"):
                if data.get("done"):
                    w = data.get("word", "")
                    return f"{'✅ 猜对了' if data['correct'] else '❌ 没猜出来'}！答案是「{w}」"
                return f"❌ 猜错了，还剩{data['attempts_left']}次"
            return f"❌ 猜词失败: {resp.text}"

        elif action == "history":
            resp = await client.get(f"{QA_API_BASE}/chem/draw/sessions")
            sessions = resp.json()
            if not sessions:
                return "📜 暂无历史记录"
            lines = ["📜 你画我猜历史：", ""]
            for s in sessions[:10]:
                status = "✅ 已完成" if s["status"] == "done" else "⏳ 等待猜画"
                result = f" {'✅' if s.get('result') == 'correct' else '❌'}" if s["status"] == "done" else ""
                lines.append(f"  {s['drawer_name']} → {s['guesser_name']} {status}{result}")
                lines.append(f"  {s['created_at'][:10]}  ID: {s['id']}")
                lines.append("")
            return "\n".join(lines)

        elif action == "session":
            if not session_id:
                return "❌ 需要session_id参数"
            resp = await client.get(f"{QA_API_BASE}/chem/draw/session/{session_id}")
            data = resp.json()
            word_display = data["word"] if data["status"] == "done" else "[隐藏]"
            lines = [
                f"🎨 Session: {data['id']}",
                f"画画方: {data['drawer_name']}  猜题方: {data['guesser_name']}",
                f"词条: {word_display}",
                f"提示: {data.get('hint', '无')}",
                f"画作: {data['drawing_url']}",
                f"状态: {'已完成' if data['status'] == 'done' else '等待猜画'}",
            ]
            if data.get("guesses"):
                lines.append("\n猜测:")
                for g in data["guesses"]:
                    lines.append(f"  🎯 {g}")
            return "\n".join(lines)

        else:
            return "❌ 未知action。可用: pending/guess/history/session"


# ── 记账 MCP 工具 ──

@mcp.tool()
async def ledger(action: str, type: str = "", amount: float = 0, category: str = "",
                 note: str = "", date: str = "", year: int = 0, month: int = 0,
                 start: str = "", end: str = "", person: str = "") -> str:
    """记账工具，通过action参数选择操作。

    Args:
        action: 操作类型，可选值：
            add — 记一笔账（需要type/amount/category，可选note/date）
            today — 查今日收支摘要
            list — 查某月账单（可选year/month，默认当月）
            stats — 收支统计（可选start/end日期范围）
            categories — 查所有分类
            borrow_add — 记借贷（需要type=out或in/amount/person，可选note/date）
            borrow_list — 查借贷记录
        type: add时expense=支出/income=收入；borrow_add时out=借出/in=借入
        amount: 金额
        category: 分类（如餐饮、购物等）
        note: 备注
        date: 日期 YYYY-MM-DD
        year: list时指定年份
        month: list时指定月份
        start: stats时开始日期
        end: stats时结束日期
        person: borrow_add时对方姓名
    """
    async with httpx.AsyncClient() as client:

        if action == "add":
            body = {"type": type, "amount": amount, "category": category, "note": note}
            if date:
                body["date"] = date
            resp = await client.post(f"{QA_API_BASE}/ledger/quick", json=body)
            data = resp.json()
            if data.get("ok"):
                e = data["entry"]
                return f"✅ {data['message']}\n日期: {e['date']}\n备注: {e['note'] or '无'}"
            return "❌ 记账失败"

        elif action == "today":
            resp = await client.get(f"{QA_API_BASE}/ledger/summary/today")
            data = resp.json()
            return (f"📅 {data['date']}\n💸 支出: ¥{data['expense']:.2f}\n"
                    f"💰 收入: ¥{data['income']:.2f}\n📊 结余: ¥{data['balance']:.2f}\n📝 共 {data['count']} 笔")

        elif action == "list":
            if year and month:
                resp = await client.get(f"{QA_API_BASE}/ledger/list/{year}/{month}")
            else:
                from datetime import date as dt_date
                today = dt_date.today()
                resp = await client.get(f"{QA_API_BASE}/ledger/list/{today.year}/{today.month}")
            data = resp.json()
            if not data:
                return "📭 本月暂无记录"
            lines = [f"📒 共 {len(data)} 笔记录\n"]
            for e in data[-20:]:
                icon = "💸" if e["type"] == "expense" else "💰"
                lines.append(f"{icon} {e['date']} {e['category']} ¥{e['amount']:.2f}" + (f" ({e['note']})" if e.get('note') else ""))
            if len(data) > 20:
                lines.append(f"... 还有 {len(data)-20} 条更早的记录")
            return "\n".join(lines)

        elif action == "stats":
            params = {}
            if start: params["start"] = start
            if end: params["end"] = end
            resp = await client.get(f"{QA_API_BASE}/ledger/stats/range", params=params)
            data = resp.json()
            lines = [
                f"📊 收支统计 ({start or '全部'} ~ {end or '至今'})",
                f"💸 总支出: ¥{data['total_expense']:.2f}",
                f"💰 总收入: ¥{data['total_income']:.2f}",
                f"📈 结余: ¥{data['balance']:.2f}",
                f"📅 日均支出: ¥{data['daily_avg_expense']:.2f}",
                f"🔄 未结借出: ¥{data['borrow_out']:.2f} / 借入: ¥{data['borrow_in']:.2f}",
                f"📝 共 {data['count']} 笔",
            ]
            if data["expense_by_cat"]:
                lines.append("\n🏷️ 支出分类排行:")
                for name, val in data["expense_by_cat"][:10]:
                    pct = val / data["total_expense"] * 100 if data["total_expense"] > 0 else 0
                    lines.append(f"  {name}: ¥{val:.2f} ({pct:.1f}%)")
            return "\n".join(lines)

        elif action == "categories":
            resp = await client.get(f"{QA_API_BASE}/ledger/categories/names")
            data = resp.json()
            return "💸 支出分类:\n" + "、".join(data["expense"]) + "\n\n💰 收入分类:\n" + "、".join(data["income"])

        elif action == "borrow_add":
            body = {"type": type, "amount": amount, "person": person, "note": note}
            if date: body["date"] = date
            resp = await client.post(f"{QA_API_BASE}/ledger/borrow", json=body)
            data = resp.json()
            if data.get("ok"):
                label = "借出" if type == "out" else "借入"
                return f"✅ 已记录{label} ¥{amount:.2f}（{person}）"
            return "❌ 记录失败"

        elif action == "borrow_list":
            resp = await client.get(f"{QA_API_BASE}/ledger/borrow")
            data = resp.json()
            if not data:
                return "📭 暂无借贷记录"
            lines = ["🔄 借贷记录:\n"]
            for b in data:
                icon = "🔴" if b["type"] == "out" else "🔵"
                label = "借出" if b["type"] == "out" else "借入"
                status = " ✅已结清" if b.get("settled") else ""
                lines.append(f"{icon} {b['date']} {label} ¥{b['amount']:.2f} — {b['person']}{status}")
            return "\n".join(lines)

        else:
            return f"❌ 未知action: {action}\n可用: add/today/list/stats/categories/borrow_add/borrow_list"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
