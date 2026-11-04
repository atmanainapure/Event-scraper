"""Build and send the daily digest email."""
from __future__ import annotations

import html
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from .models import Event

SECTIONS = [
    ("hackathon", "🛠️ Hackathons"),
    ("networking", "🍻 In-person networking, mixers & meetups"),
    ("conference", "🎤 Conferences & summits"),
    ("event", "📅 Other upcoming company events"),
]


def _when(ev: Event) -> str:
    return ev.start.strftime("%a %b %d, %Y") if ev.start else "date TBA"


def _where(ev: Event) -> str:
    if ev.location:
        return ev.location
    if ev.in_person is False:
        return "Online"
    return ""


def build(events: list[Event], linkedin_links: list[tuple[str, str]], errors: list[str]) -> tuple[str, str, str]:
    today = datetime.now().strftime("%b %d, %Y")
    counts = {k: sum(1 for e in events if e.kind == k) for k, _ in SECTIONS}
    if events:
        subject = f"🗓️ {len(events)} new events ({counts['hackathon']} hackathons) – {today}"
    else:
        subject = f"🗓️ No new events today – {today}"

    text_lines = [f"Event bot digest for {today}", ""]
    h = [
        "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:720px;color:#222'>",
        f"<h2 style='margin-bottom:4px'>Event digest · {html.escape(today)}</h2>",
        f"<p style='color:#666;margin-top:0'>{len(events)} new event(s) found on Luma and company event pages.</p>",
    ]
    if not events:
        h.append("<p>Nothing new since the last run. The bot is alive and checked all sources.</p>")
        text_lines.append("Nothing new since the last run.")

    for kind, heading in SECTIONS:
        items = sorted((e for e in events if e.kind == kind),
                       key=lambda e: (e.start is None, e.start.timestamp() if e.start else 0))
        if not items:
            continue
        h.append(f"<h3 style='border-bottom:1px solid #eee;padding-bottom:4px'>{heading} ({len(items)})</h3><ul style='padding-left:18px'>")
        text_lines.append(f"== {heading} ==")
        for e in items:
            meta = " · ".join(x for x in (_when(e), _where(e), e.source) if x)
            h.append(
                "<li style='margin-bottom:10px'>"
                f"<span style='background:#eef3ff;color:#2952cc;border-radius:4px;padding:1px 6px;font-size:12px;font-weight:600'>{html.escape(e.tag)}</span> "
                f"<a href='{html.escape(e.url, quote=True)}' style='font-weight:600;color:#111'>{html.escape(e.title)}</a><br>"
                f"<span style='color:#666;font-size:13px'>{html.escape(meta)}</span></li>"
            )
            text_lines.append(f"- [{e.tag}] {e.title}\n  {meta}\n  {e.url}")
        h.append("</ul>")
        text_lines.append("")

    if linkedin_links:
        h.append("<h3 style='border-bottom:1px solid #eee;padding-bottom:4px'>🔗 LinkedIn company event pages</h3>"
                 "<p style='color:#666;font-size:13px'>LinkedIn requires a login, so these are one-click links to check yourself.</p>"
                 "<p style='font-size:13px;line-height:1.8'>")
        h.append(" · ".join(f"<a href='{html.escape(u, quote=True)}'>{html.escape(n)}</a>" for n, u in linkedin_links))
        h.append("</p>")
        text_lines.append("LinkedIn event pages:")
        text_lines += [f"- {n}: {u}" for n, u in linkedin_links]
        text_lines.append("")

    if errors:
        h.append("<details style='color:#999;font-size:12px'><summary>Sources that failed this run "
                 f"({len(errors)})</summary><ul>")
        h += [f"<li>{html.escape(err)}</li>" for err in errors]
        h.append("</ul></details>")
        text_lines.append(f"{len(errors)} source(s) failed: " + "; ".join(errors))

    h.append("</div>")
    return subject, "\n".join(text_lines), "\n".join(h)


def send(subject: str, text: str, html_body: str) -> None:
    to = os.environ["EMAIL_TO"]
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("EMAIL_FROM", user)
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)
