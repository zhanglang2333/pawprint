"""
Email MCP Server — Outlook IMAP/SMTP

让AI通过MCP收发邮件。支持多账号（安安/枝枝）。

MCP连接方式：
  transport: streamable-http
  url: http://<your-server>:8091/mcp

工具列表：
  - email_inbox: 查看收件箱
  - email_read: 读取邮件内容
  - email_send: 发送邮件
  - email_reply: 回复邮件
  - email_search: 搜索邮件
  - email_accounts: 查看已配置的邮箱账号
"""

import imaplib
import smtplib
import email as email_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from email.utils import formataddr, parseaddr
from datetime import datetime
from mcp.server.fastmcp import FastMCP

ACCOUNTS = {
    "anan": {
        "name": "安安",
        "email": "anan_crow@outlook.com",
        "password": "Cr0w@nAn_2026",
        "imap": "outlook.office365.com",
        "smtp": "smtp-mail.outlook.com",
    },
    "zhizhi": {
        "name": "枝枝",
        "email": "zhizhipai@outlook.com",
        "password": "Zhizhi2026",
        "imap": "outlook.office365.com",
        "smtp": "smtp-mail.outlook.com",
    },
}

DEFAULT_ACCOUNT = "anan"

mcp = FastMCP(
    "Email",
    host="0.0.0.0",
    port=8091,
    instructions="""邮件收发MCP。
默认使用安安的邮箱(anan_crow@outlook.com)。
用 email_inbox 看收件箱，email_read 读邮件，email_send 发邮件。
可以通过 account 参数切换到枝枝的邮箱。"""
)


def _decode_str(s):
    if s is None:
        return ""
    parts = decode_header(s)
    result = []
    for data, charset in parts:
        if isinstance(data, bytes):
            result.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(data)
    return "".join(result)


def _get_imap(account: str = DEFAULT_ACCOUNT):
    acc = ACCOUNTS[account]
    imap = imaplib.IMAP4_SSL(acc["imap"])
    imap.login(acc["email"], acc["password"])
    return imap


def _get_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
            if ct == "text/html" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return f"[HTML邮件]\n{payload.decode(charset, errors='replace')}"
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return "(无法解析邮件内容)"


def _format_date(msg):
    date_str = msg.get("Date", "")
    if not date_str:
        return "未知时间"
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return date_str


@mcp.tool()
def email_accounts() -> str:
    """查看已配置的邮箱账号列表。"""
    lines = ["📧 已配置的邮箱账号：", ""]
    for key, acc in ACCOUNTS.items():
        default = " (默认)" if key == DEFAULT_ACCOUNT else ""
        lines.append(f"  {key}: {acc['name']} <{acc['email']}>{default}")
    return "\n".join(lines)


@mcp.tool()
def email_inbox(account: str = "anan", count: int = 10, folder: str = "INBOX") -> str:
    """查看收件箱最新邮件列表。

    Args:
        account: 使用哪个账号，anan或zhizhi，默认anan
        count: 显示最近几封，默认10
        folder: 邮箱文件夹，默认INBOX
    """
    if account not in ACCOUNTS:
        return f"❌ 未知账号: {account}，可用: {', '.join(ACCOUNTS.keys())}"

    try:
        imap = _get_imap(account)
        imap.select(folder, readonly=True)
        _, data = imap.search(None, "ALL")
        ids = data[0].split()

        if not ids:
            imap.logout()
            return f"📭 {ACCOUNTS[account]['name']}的{folder}是空的"

        recent_ids = ids[-count:]
        recent_ids.reverse()

        lines = [f"📬 {ACCOUNTS[account]['name']} 的收件箱（最新{len(recent_ids)}封）", ""]

        for mid in recent_ids:
            _, msg_data = imap.fetch(mid, "(FLAGS RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)

            flags_raw = msg_data[0][0].decode() if msg_data[0][0] else ""
            is_read = "\\Seen" in flags_raw

            subject = _decode_str(msg.get("Subject", "(无主题)"))
            sender = _decode_str(msg.get("From", "未知"))
            date = _format_date(msg)
            status = "📖" if is_read else "📩"

            lines.append(f"{status} #{mid.decode()} | {date}")
            lines.append(f"   从: {sender}")
            lines.append(f"   主题: {subject}")
            lines.append("")

        imap.logout()
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 获取邮件失败: {e}"


@mcp.tool()
def email_read(msg_id: str, account: str = "anan", folder: str = "INBOX") -> str:
    """读取一封邮件的完整内容。

    Args:
        msg_id: 邮件ID（从email_inbox获取的#号）
        account: 使用哪个账号，默认anan
        folder: 邮箱文件夹，默认INBOX
    """
    if account not in ACCOUNTS:
        return f"❌ 未知账号: {account}"

    try:
        imap = _get_imap(account)
        imap.select(folder)
        _, msg_data = imap.fetch(msg_id.encode(), "(RFC822)")

        if not msg_data or not msg_data[0]:
            imap.logout()
            return f"❌ 找不到邮件 #{msg_id}"

        raw = msg_data[0][1]
        msg = email_lib.message_from_bytes(raw)

        subject = _decode_str(msg.get("Subject", "(无主题)"))
        sender = _decode_str(msg.get("From", "未知"))
        to = _decode_str(msg.get("To", ""))
        cc = _decode_str(msg.get("Cc", ""))
        date = _format_date(msg)
        body = _get_body(msg)
        message_id = msg.get("Message-ID", "")

        lines = [
            f"📧 邮件 #{msg_id}",
            f"主题: {subject}",
            f"从: {sender}",
            f"到: {to}",
        ]
        if cc:
            lines.append(f"抄送: {cc}")
        lines.append(f"时间: {date}")
        lines.append(f"Message-ID: {message_id}")
        lines.append("")
        lines.append("─" * 40)
        lines.append(body)

        imap.logout()
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 读取邮件失败: {e}"


@mcp.tool()
def email_send(to: str, subject: str, body: str, account: str = "anan",
               cc: str = "", reply_to_message_id: str = "") -> str:
    """发送邮件。

    Args:
        to: 收件人邮箱地址（多个用逗号分隔）
        subject: 邮件主题
        body: 邮件正文（纯文本）
        account: 使用哪个账号发送，默认anan
        cc: 抄送（可选，多个用逗号分隔）
        reply_to_message_id: 回复某封邮件时传入其Message-ID（可选）
    """
    if account not in ACCOUNTS:
        return f"❌ 未知账号: {account}"

    acc = ACCOUNTS[account]

    try:
        msg = MIMEMultipart()
        msg["From"] = formataddr((acc["name"], acc["email"]))
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if reply_to_message_id:
            msg["In-Reply-To"] = reply_to_message_id
            msg["References"] = reply_to_message_id

        msg.attach(MIMEText(body, "plain", "utf-8"))

        all_recipients = [addr.strip() for addr in to.split(",")]
        if cc:
            all_recipients += [addr.strip() for addr in cc.split(",")]

        with smtplib.SMTP(acc["smtp"], 587) as server:
            server.starttls()
            server.login(acc["email"], acc["password"])
            server.send_message(msg, from_addr=acc["email"], to_addrs=all_recipients)

        return f"✅ 邮件已发送！\n从: {acc['email']}\n到: {to}\n主题: {subject}"
    except Exception as e:
        return f"❌ 发送失败: {e}"


@mcp.tool()
def email_search(query: str, account: str = "anan", folder: str = "INBOX",
                 count: int = 10) -> str:
    """搜索邮件。

    Args:
        query: 搜索内容（会在主题和发件人中搜索）
        account: 使用哪个账号，默认anan
        folder: 搜索的文件夹，默认INBOX
        count: 最多返回几封，默认10
    """
    if account not in ACCOUNTS:
        return f"❌ 未知账号: {account}"

    try:
        imap = _get_imap(account)
        imap.select(folder, readonly=True)

        criteria = f'(OR SUBJECT "{query}" FROM "{query}")'
        _, data = imap.search(None, criteria)
        ids = data[0].split()

        if not ids:
            imap.logout()
            return f"🔍 没有找到匹配 \"{query}\" 的邮件"

        recent_ids = ids[-count:]
        recent_ids.reverse()

        lines = [f"🔍 搜索 \"{query}\" — 找到{len(ids)}封，显示最新{len(recent_ids)}封", ""]

        for mid in recent_ids:
            _, msg_data = imap.fetch(mid, "(RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)

            subject = _decode_str(msg.get("Subject", "(无主题)"))
            sender = _decode_str(msg.get("From", "未知"))
            date = _format_date(msg)

            lines.append(f"📧 #{mid.decode()} | {date}")
            lines.append(f"   从: {sender}")
            lines.append(f"   主题: {subject}")
            lines.append("")

        imap.logout()
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 搜索失败: {e}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
