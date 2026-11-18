#!/usr/bin/env python3
"""Deliver an alert to Hilda: chart image first, then the text, via Telegram Bot API and/or SMTP email.
Integrated with the dedupe log: an alert is logged as delivered ONLY after at least one channel succeeded,
and an alert that is already logged is never sent again.

Configuration (environment variables, or a git-ignored .env file - see .env.example; never commit secrets):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TO (comma-separated),
  SMTP_SECURITY = starttls (default) | ssl | none
  NOTIFY_CHANNELS = telegram,email (default: every channel that is configured)

    python3 scripts/notify.py --alert alerts/2026-11-12_N10_STOP.md --chart charts/us100_N10_1112_stop.png \
        --id N10 --event STOP [--log-field instrument=US100 --log-field r=-1 ...] [--dry-run]
    python3 scripts/notify.py --check-config
Exit codes: 0 delivered (or dry run / already delivered), 1 delivery failed on every channel, 2 usage/config error.
"""
from __future__ import annotations
import argparse, json, mimetypes, os, smtplib, ssl, sys, uuid
import urllib.error, urllib.request
from email.message import EmailMessage

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alert_log  # noqa: E402

TG_API = "https://api.telegram.org"
TG_TEXT_LIMIT, TG_CAPTION_LIMIT = 4096, 1024


def load_env_file(path: str, environ=os.environ) -> None:
    """Minimal .env reader (KEY=VALUE, # comments). Existing environment variables win."""
    if not path or not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def channels(env=os.environ) -> dict:
    tg = bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"))
    em = bool(env.get("SMTP_HOST") and env.get("SMTP_FROM") and env.get("SMTP_TO"))
    want = [c.strip() for c in env.get("NOTIFY_CHANNELS", "").split(",") if c.strip()]
    on = {"telegram": tg, "email": em}
    if want:
        on = {k: v and k in want for k, v in on.items()}
    return on


# ---------------------------------------------------------------- Telegram
def _multipart(fields: dict, file_field: str, path: str) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        data = f.read()
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{os.path.basename(path)}"\r\n'
                 f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def _tg_call(token: str, method: str, body: bytes, ctype: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(f"{TG_API}/bot{token}/{method}", data=body, headers={"Content-Type": ctype}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read().decode())
    if not res.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {res.get('description')}")
    return res


def send_telegram(text: str, chart: str | None, env=os.environ, done: set | None = None) -> None:
    """Photo first, then the text in <=4096-char parts. `done` holds the parts already delivered on an earlier try
    ("photo", "text:0", "text:1", ...): they are skipped, and each part is added as soon as Telegram accepts it.
    So a retry after a failed text part never sends the chart photo twice."""
    token, chat = env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]
    done = set() if done is None else done
    if chart and "photo" not in done:
        body, ctype = _multipart({"chat_id": chat}, "photo", chart)
        _tg_call(token, "sendPhoto", body, ctype)
        done.add("photo")
    for k, i in enumerate(range(0, len(text), TG_TEXT_LIMIT)):
        if f"text:{k}" in done:
            continue
        body = json.dumps({"chat_id": chat, "text": text[i:i + TG_TEXT_LIMIT], "disable_web_page_preview": True}).encode()
        _tg_call(token, "sendMessage", body, "application/json")
        done.add(f"text:{k}")


# ---------------------------------------------------------------- Email
def build_email(subject: str, text: str, chart: str | None, env=os.environ) -> EmailMessage:
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, env["SMTP_FROM"], env["SMTP_TO"]
    m.set_content(text)
    if chart:
        with open(chart, "rb") as f:
            m.add_attachment(f.read(), maintype="image", subtype="png", filename=os.path.basename(chart))
    return m


def send_email(subject: str, text: str, chart: str | None, env=os.environ) -> None:
    host, port = env["SMTP_HOST"], int(env.get("SMTP_PORT", "587"))
    sec = env.get("SMTP_SECURITY", "starttls").lower()
    msg = build_email(subject, text, chart, env)
    if sec == "ssl":
        s = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30)
    else:
        s = smtplib.SMTP(host, port, timeout=30)
    try:
        if sec == "starttls":
            s.starttls(context=ssl.create_default_context())
        if env.get("SMTP_USER"):
            s.login(env["SMTP_USER"], env.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
    finally:
        s.quit()


# ---------------------------------------------------------------- deliver
def strip_header(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.startswith("<!--")).strip()


def deliver(text: str, chart: str | None, subject: str, dry_run: bool = False, env=os.environ,
            progress: dict | None = None) -> dict:
    """Returns {'sent': [channels], 'failed': {channel: error}, 'dry_run': bool}.
    `progress` = {channel: set of parts already delivered} from an earlier failed try (updated in place)."""
    progress = {} if progress is None else progress
    on = [k for k, v in channels(env).items() if v]
    res = {"sent": [], "failed": {}, "dry_run": dry_run, "channels": on}
    if chart and not os.path.exists(chart):
        chart = None
        res["warning"] = "chart file missing - sent text only"
    if dry_run:
        res["preview"] = {"subject": subject, "chart_first": chart, "text": text}
        return res
    if not on:
        res["failed"]["config"] = "no channel configured (set TELEGRAM_* and/or SMTP_* - see .env.example)"
        return res
    for ch in on:
        try:
            if ch == "telegram":
                send_telegram(text, chart, env, progress.setdefault("telegram", set()))
            else:
                send_email(subject, text, chart, env)
            res["sent"].append(ch)
        except (urllib.error.URLError, OSError, smtplib.SMTPException, RuntimeError, KeyError) as e:
            res["failed"][ch] = f"{type(e).__name__}: {e}"
    return res


def deliver_and_log(setup_id: str, event: str, text: str, chart: str | None, log_row: dict, log_path: str,
                    dry_run: bool = False, env=os.environ, subject: str | None = None) -> dict:
    """Dedupe -> send -> log only on success. Dry runs never log."""
    if alert_log.is_logged(log_path, setup_id, event):
        return {"skipped": "already delivered (row in alert log)", "sent": [], "failed": {}}
    ppath = progress_path(log_path)
    allp = _load_progress(ppath)
    key = f"{setup_id}|{event}"
    prog = {ch: set(parts) for ch, parts in allp.get(key, {}).items()}
    res = deliver(text, chart, subject or f"[ICT alert] {setup_id} {event}", dry_run, env, prog)
    if not dry_run and res["sent"]:
        row = {**log_row, "setup_id": setup_id, "event": event, "chart": chart or ""}
        res["logged"] = alert_log.add(log_path, row)
        allp.pop(key, None)
    else:
        res["logged"] = False
        if not dry_run and any(prog.values()):
            allp[key] = {ch: sorted(parts) for ch, parts in prog.items() if parts}
            res["partial"] = allp[key]
    if not dry_run and (key in allp or os.path.exists(ppath)):
        _save_progress(ppath, allp)
    return res


def progress_path(log_path: str) -> str:
    """Parts of an alert already delivered while the alert as a whole failed (git-ignored, next to the alert log)."""
    return os.path.join(os.path.dirname(os.path.abspath(log_path)), "delivery_progress.json")


def _load_progress(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_progress(path: str, data: dict) -> None:
    if not data:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--alert"); p.add_argument("--chart"); p.add_argument("--id"); p.add_argument("--event")
    p.add_argument("--subject"); p.add_argument("--log", default=alert_log.DEFAULT_LOG)
    p.add_argument("--log-field", action="append", default=[], help="extra alert-log column as key=value")
    p.add_argument("--env-file", default=os.path.join(ROOT, ".env")); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check-config", action="store_true")
    a = p.parse_args(argv)
    load_env_file(a.env_file)
    if a.check_config:
        print(json.dumps(channels(), indent=2)); return 0 if any(channels().values()) else 2
    if not (a.alert and a.id and a.event):
        print("error: --alert, --id and --event are required", file=sys.stderr); return 2
    text = strip_header(open(a.alert).read())
    row = dict(kv.split("=", 1) for kv in a.log_field)
    res = deliver_and_log(a.id, a.event.upper(), text, a.chart, row, a.log, a.dry_run, subject=a.subject)
    print(json.dumps(res, indent=2))
    if res.get("skipped") or a.dry_run:
        return 0
    return 0 if res["sent"] else 1


if __name__ == "__main__":
    sys.exit(main())
