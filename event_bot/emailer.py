"""Build and send the daily digest email."""
from __future__ import annotations

import html
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from .models import Event



def _when(ev: Event) -> str:
    return ev.start.strftime("%a %b %d, %Y") if ev.start else "date TBA"


def _where(ev: Event) -> str:
    if ev.location:
        return ev.location
    if ev.in_person is False:
        return "Online"
    return ""


def _item_html(e, new_keys) -> str:
    meta = " · ".join(x for x in (_when(e), _where(e), e.source) if x)
    cos = ", ".join(e.extra.get("companies") or [])
    badge = ("<span style='background:#e6f7ec;color:#137333;border-radius:4px;padding:1px 6px;"
             "font-size:11px;font-weight:700;margin-right:4px'>NEW</span>") if e.key in new_keys else ""
    return ("<li style='margin-bottom:12px'>"
            f"{badge}<span style='background:#eef3ff;color:#2952cc;border-radius:4px;padding:1px 6px;font-size:12px;font-weight:600'>{html.escape(e.tag)}</span> "
            f"<a href='{html.escape(e.url, quote=True)}' style='font-weight:600;color:#111'>{html.escape(e.title)}</a><br>"
            f"<span style='color:#666;font-size:13px'>{html.escape(meta)}</span>"
            + (f"<br><span style='font-size:13px'><b>Company:</b> {html.escape(cos)}</span>" if cos else "")
            + "</li>")


def _item_text(e, new_keys) -> str:
    meta = " · ".join(x for x in (_when(e), _where(e), e.source) if x)
    cos = ", ".join(e.extra.get("companies") or [])
    return (f"- {'[NEW] ' if e.key in new_keys else ''}[{e.tag}] {e.title}\n  {meta}"
            + (f"\n  Company: {cos}" if cos else "") + f"\n  {e.url}")


def build_daily(hacks, nets, leads, searched: bool, new_keys: set, company_names: list[str],
                errors: list[str]) -> tuple[str, str, str]:
    """Morning email: top-company hackathons and networking from Luma / Meetup / Cerebral
    Valley, plus Google leads when no top-company hackathon was found there."""
    today = datetime.now().strftime("%a %b %d")
    order = lambda e: (e.start is None, e.start.timestamp() if e.start else 0)  # noqa: E731
    n_new = sum(1 for e in hacks + nets + leads if e.key in new_keys)
    total = len(hacks) + len(nets) + len(leads)
    subject = (f"☀️ {len(hacks)} hackathons, {len(nets)} networking events with top tech companies"
               + (f", {len(leads)} Google leads" if leads else "") + (f" ({n_new} new)" if n_new else "")
               + f" – {today}") if total else f"☀️ No top-company hackathons or networking in NYC today – {today}"
    cos = ", ".join(company_names)
    h = ["<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:720px;color:#222'>",
         f"<h2 style='margin-bottom:4px'>Your morning event brief · {html.escape(today)}</h2>",
         f"<p style='color:#666;margin-top:0'>In-person NYC/NJ events on Luma, Meetup and Cerebral Valley "
         f"involving: {html.escape(cos)}.</p>"]
    t = [f"Morning event brief – {today}", f"Companies: {cos}", ""]

    def section(title, items, empty):
        h.append(f"<h3 style='border-bottom:1px solid #eee;padding-bottom:4px'>{title} ({len(items)})</h3>")
        t.append(f"== {title} ({len(items)}) ==")
        if not items:
            h.append(f"<p style='color:#666'>{html.escape(empty)}</p>")
            t.append(empty)
        else:
            h.append("<ul style='padding-left:18px'>")
            for e in sorted(items, key=order):
                h.append(_item_html(e, new_keys))
                t.append(_item_text(e, new_keys))
            h.append("</ul>")
        t.append("")

    section("🛠️ Hackathons with top companies", hacks,
            "None on Luma, Meetup or Cerebral Valley today, so I searched Google for each company below.")
    section("🤝 Networking events with top companies", nets,
            "None found on Luma, Meetup or Cerebral Valley today.")
    if searched:
        section("🔎 Google leads: \"[company] hackathon New York City\"", leads,
                "Google search found no upcoming NYC hackathons from these companies."
                if not any("Google fallback search is off" in e for e in errors)
                else "Google search is not set up yet (needs a SERPAPI_KEY secret).")
    if errors:
        h.append("<details style='color:#999;font-size:12px'><summary>Sources with problems this run "
                 f"({len(errors)})</summary><ul>")
        h += [f"<li>{html.escape(err)}</li>" for err in errors]
        h.append("</ul></details>")
        t.append(f"{len(errors)} source problem(s): " + "; ".join(errors))
    h.append("</div>")
    return subject, "\n".join(t), "\n".join(h)


def send(subject: str, text: str, html_body: str) -> None:
    to = os.environ["EMAIL_TO"].strip()
    user = os.environ["SMTP_USER"].strip()
    # App passwords are shown as "abcd efgh ijkl mnop"; pasted secrets often carry
    # those spaces or a trailing newline, which makes Gmail drop the connection.
    password = "".join(os.environ["SMTP_PASSWORD"].split())
    host = (os.environ.get("SMTP_HOST") or "smtp.gmail.com").strip()
    port = int((os.environ.get("SMTP_PORT") or "465").strip())

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = (os.environ.get("EMAIL_FROM") or user).strip()
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    # Safe diagnostics: shapes only, never the values.
    print(
        f"SMTP check: host={host} port={port} user_len={len(user)} user_has_at={'@' in user} "
        f"user_domain={user.rsplit('@', 1)[-1].lower() if '@' in user else '-'} "
        f"pw_len={len(password)} pw_ascii={password.isascii()} pw_alnum={password.isalnum()}",
        flush=True,
    )
    errors = []
    for p, mech in ((port, None), (587, None), (465, "LOGIN"), (587, "LOGIN")):
        try:
            _deliver(msg, host, p, user, password, mech)
            print(f"SMTP sent via port {p} auth={mech or 'default'}", flush=True)
            return
        except Exception as e:  # noqa: BLE001
            print(f"SMTP attempt port={p} auth={mech or 'default'} failed: {type(e).__name__}: {e}", flush=True)
            errors.append(e)
    raise RuntimeError(
        "Gmail login failed on every method. Check that SMTP_USER is the full Gmail address and "
        "SMTP_PASSWORD is a 16-character App Password created on that same account."
    ) from errors[-1]


def _login(s: smtplib.SMTP, user: str, password: str, mech: str | None) -> None:
    if mech == "LOGIN":
        s.ehlo()
        s.user, s.password = user, password
        s.auth("LOGIN", s.auth_login, initial_response_ok=False)
    else:
        s.login(user, password)


def _deliver(msg: EmailMessage, host: str, port: int, user: str, password: str, mech: str | None = None) -> None:
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            print(f"  server auth methods: {s.ehlo() and s.esmtp_features.get('auth', '?')}", flush=True)
            _login(s, user, password, mech)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.ehlo()
            _login(s, user, password, mech)
            s.send_message(msg)
