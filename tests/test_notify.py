import io, json
import pytest
import notify
import alert_log

TG = {"TELEGRAM_BOT_TOKEN": "123:FAKE", "TELEGRAM_CHAT_ID": "42"}
SMTP = {"SMTP_HOST": "smtp.example.invalid", "SMTP_PORT": "587", "SMTP_FROM": "bot@example.invalid",
        "SMTP_TO": "hilda@example.invalid", "SMTP_USER": "u", "SMTP_PASSWORD": "p"}


class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture
def chart(tmp_path):
    p = tmp_path / "c.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    return str(p)


@pytest.fixture
def calls(monkeypatch):
    seen = []
    def fake_urlopen(req, timeout=0):
        seen.append({"url": req.full_url, "ctype": req.headers.get("Content-type"), "body": req.data})
        return FakeResp(json.dumps({"ok": True}).encode())
    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_channels_from_env():
    assert notify.channels({}) == {"telegram": False, "email": False}
    assert notify.channels({**TG, **SMTP}) == {"telegram": True, "email": True}
    assert notify.channels({**TG, **SMTP, "NOTIFY_CHANNELS": "email"}) == {"telegram": False, "email": True}


def test_telegram_chart_first_then_text(calls, chart):
    res = notify.deliver("hello", chart, "s", env=TG)
    assert res["sent"] == ["telegram"]
    assert calls[0]["url"].endswith("/bot123:FAKE/sendPhoto") and calls[0]["ctype"].startswith("multipart/form-data")
    assert b'name="photo"' in calls[0]["body"] and b"FAKE" in calls[0]["body"]
    assert calls[1]["url"].endswith("/sendMessage") and json.loads(calls[1]["body"])["text"] == "hello"


def test_long_text_is_split(calls):
    notify.deliver("x" * 5000, None, "s", env=TG)
    assert [len(json.loads(c["body"])["text"]) for c in calls] == [4096, 904]


def test_telegram_api_error_is_failure(monkeypatch):
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout=0: FakeResp(b'{"ok": false, "description": "chat not found"}'))
    res = notify.deliver("hi", None, "s", env=TG)
    assert res["sent"] == [] and "chat not found" in res["failed"]["telegram"]


def test_email_with_attachment(monkeypatch, chart):
    sent = {}
    class FakeSMTP:
        def __init__(self, host, port, timeout=0): sent["host"] = (host, port)
        def starttls(self, context=None): sent["tls"] = True
        def login(self, u, p): sent["login"] = u
        def send_message(self, m): sent["msg"] = m
        def quit(self): sent["quit"] = True
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    res = notify.deliver("body text", chart, "[ICT alert] N1 NEW", env=SMTP)
    m = sent["msg"]
    assert res["sent"] == ["email"] and sent["tls"] and sent["quit"]
    assert m["Subject"] == "[ICT alert] N1 NEW" and m["To"] == "hilda@example.invalid"
    atts = list(m.iter_attachments())
    assert atts and atts[0].get_filename() == "c.png"


def test_dry_run_and_no_config_never_log(tmp_path, calls):
    log = str(tmp_path / "log.csv")
    r = notify.deliver_and_log("N1", "NEW", "t", None, {}, log, dry_run=True, env=TG)
    assert r["logged"] is False and calls == [] and not alert_log.is_logged(log, "N1", "NEW")
    r = notify.deliver_and_log("N1", "NEW", "t", None, {}, log, env={})
    assert r["logged"] is False and "config" in r["failed"]


def test_log_only_on_success_and_dedupe(tmp_path, calls):
    log = str(tmp_path / "log.csv")
    r = notify.deliver_and_log("N1", "NEW", "t", None, {"instrument": "NAS100"}, log, env=TG)
    assert r["logged"] is True and alert_log.is_logged(log, "N1", "NEW")
    n = len(calls)
    r = notify.deliver_and_log("N1", "NEW", "t", None, {}, log, env=TG)
    assert r["skipped"] and len(calls) == n            # already delivered: nothing sent again


def test_partial_channel_failure_still_logs(tmp_path, calls, monkeypatch):
    log = str(tmp_path / "log.csv")
    def boom(*a, **k): raise OSError("smtp down")
    monkeypatch.setattr(notify, "send_email", boom)
    r = notify.deliver_and_log("N2", "STOP", "t", None, {}, log, env={**TG, **SMTP})
    assert r["sent"] == ["telegram"] and "email" in r["failed"] and r["logged"] is True


def test_env_file_and_strip_header(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\nTELEGRAM_CHAT_ID='7'\nSMTP_TO=a@b\n")
    env = {"SMTP_TO": "keep@me"}
    notify.load_env_file(str(p), env)
    assert env == {"SMTP_TO": "keep@me", "TELEGRAM_CHAT_ID": "7"}
    assert notify.strip_header("<!-- event: NEW -->\nhello") == "hello"


def test_no_secrets_committed():
    import os
    from conftest import ROOT
    assert not os.path.exists(os.path.join(ROOT, ".env")) or "TELEGRAM_BOT_TOKEN=\n" in open(os.path.join(ROOT, ".env")).read()
    ex = open(os.path.join(ROOT, ".env.example")).read()
    for line in ex.splitlines():
        if line.startswith(("TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD", "TELEGRAM_CHAT_ID")):
            assert line.split("=", 1)[1] == ""
