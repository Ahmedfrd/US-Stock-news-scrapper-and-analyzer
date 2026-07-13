"""
delivery.py — send the digest by email and/or Telegram.

Secrets come from environment variables, never from config.yaml:
  EMAIL_PASSWORD      Gmail App Password (or your SMTP password)
  TELEGRAM_BOT_TOKEN  from @BotFather
  TELEGRAM_CHAT_ID    your chat id (see README)
"""

from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests


def send_email(cfg: dict, subject: str, html_body: str, text_body: str) -> bool:
    password = os.environ.get("EMAIL_PASSWORD")
    if not password:
        print("[delivery] EMAIL_PASSWORD not set — skipping email.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = cfg["to_addr"]
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as server:
            server.starttls()
            server.login(cfg["from_addr"], password)
            server.sendmail(cfg["from_addr"], [cfg["to_addr"]], msg.as_string())
        print(f"[delivery] Email sent to {cfg['to_addr']}.")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[delivery] Email failed: {e}")
        return False


def send_telegram(text_body: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[delivery] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping Telegram.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram caps messages at 4096 chars — split on line boundaries.
    chunks, current = [], ""
    for line in text_body.split("\n"):
        if len(current) + len(line) + 1 > 3800:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)

    ok = True
    for chunk in chunks:
        try:
            r = requests.post(url, data={"chat_id": chat_id, "text": chunk,
                                         "disable_web_page_preview": True}, timeout=30)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"[delivery] Telegram failed: {e}")
            ok = False
    if ok:
        print("[delivery] Telegram message sent.")
    return ok


def deliver(config: dict, subject: str, html_body: str, text_body: str) -> None:
    method = config.get("delivery", {}).get("method", "email")
    dcfg = config.get("delivery", {})
    if method in ("email", "both"):
        send_email(dcfg.get("email", {}), subject, html_body, text_body)
    if method in ("telegram", "both"):
        send_telegram(text_body)
    if method == "none":
        print("[delivery] method=none — digest saved to file only.")
