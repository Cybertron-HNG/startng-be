import smtplib
from email.utils import parseaddr

import pytest

from app.config import settings
from app.features.email import provider as mail
from app.features.email.provider import Message, SendError, SmtpProvider


class FakeSmtp:
    """Stands in for smtplib.SMTP and SMTP_SSL: remembers what the provider did."""
    log: list = []
    fail = None

    def __init__(self, host, port, context=None, timeout=None):
        self.kind = "ssl" if context is not None and type(self).__name__ == "FakeSmtpSsl" else "plain"
        FakeSmtp.log.append(("connect", type(self).__name__, host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        FakeSmtp.log.append(("ehlo",))

    def starttls(self, context=None):
        FakeSmtp.log.append(("starttls", context is not None))

    def login(self, user, password):
        if FakeSmtp.fail == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        FakeSmtp.log.append(("login", user, password))

    def send_message(self, msg, from_addr=None):
        if FakeSmtp.fail == "refused":
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        if FakeSmtp.fail == "busy":
            raise smtplib.SMTPResponseException(451, b"try later")
        if FakeSmtp.fail == "network":
            raise ConnectionResetError("reset")
        FakeSmtp.log.append(("send", from_addr, msg))


class FakeSmtpSsl(FakeSmtp):
    pass


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeSmtp.log, FakeSmtp.fail = [], None
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSmtp)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSmtpSsl)
    monkeypatch.setattr(settings, "email_from", "start.ng <hello@start.ng>")
    monkeypatch.setattr(settings, "email_reply_to", "hello@start.ng")


MESSAGE = Message(to="ada@example.com", subject="Hi", html="<p>Hello</p>", text="Hello", headers={"List-Unsubscribe": "<https://x>"}, tags=["t"])


def sent():
    return next(e for e in FakeSmtp.log if e[0] == "send")


def test_port_587_uses_starttls_then_logs_in():
    msg_id = SmtpProvider("smtp.example.com", 587, "hello@start.ng", "secret", True, False).send(MESSAGE)
    assert [e[0] for e in FakeSmtp.log] == ["connect", "ehlo", "starttls", "ehlo", "login", "send"]
    assert FakeSmtp.log[0][1:4] == ("FakeSmtp", "smtp.example.com", 587)
    _, sender, email = sent()
    assert sender == "hello@start.ng" and email["To"] == "ada@example.com" and email["Subject"] == "Hi"
    assert parseaddr(email["From"]) == ("start.ng", "hello@start.ng") and email["Reply-To"] == "hello@start.ng"
    assert email["List-Unsubscribe"] == "<https://x>"
    assert email["Date"] and email["Message-ID"] == msg_id and msg_id.endswith("@start.ng>")  # both set, the id from our own domain
    assert email.get_body(("plain",)).get_content().strip() == "Hello" and "<p>Hello</p>" in email.get_body(("html",)).get_content()


def test_port_465_uses_implicit_ssl_without_starttls():
    SmtpProvider("smtp.example.com", 465, "hello@start.ng", "secret", False, True).send(MESSAGE)
    assert FakeSmtp.log[0][1] == "FakeSmtpSsl" and FakeSmtp.log[0][3] == 465
    assert [e[0] for e in FakeSmtp.log] == ["connect", "login", "send"]


def test_no_login_without_a_username():
    SmtpProvider("relay.example.com", 25, "", "", False, False).send(MESSAGE)
    assert [e[0] for e in FakeSmtp.log] == ["connect", "ehlo", "send"]


@pytest.mark.parametrize("fail, retry", [("auth", False), ("refused", False), ("busy", True), ("network", True)])
def test_failures_say_whether_to_try_again(fail, retry):
    FakeSmtp.fail = fail
    with pytest.raises(SendError) as err:
        SmtpProvider("smtp.example.com", 587, "u", "p", True, False).send(MESSAGE)
    assert err.value.retry is retry and "secret" not in str(err.value)


def test_a_bad_from_address_is_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "email_from", "")
    with pytest.raises(SendError) as err:
        SmtpProvider("h", 587, "u", "p", True, False).send(MESSAGE)
    assert err.value.retry is False


def test_settings_pick_the_provider(monkeypatch):
    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "")
    with pytest.raises(SendError, match="SMTP_HOST"):
        mail.get_provider()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_starttls", True)
    monkeypatch.setattr(settings, "smtp_ssl", True)
    with pytest.raises(SendError, match="cannot both"):
        mail.get_provider()
    monkeypatch.setattr(settings, "smtp_starttls", False)
    monkeypatch.setattr(settings, "smtp_port", 465)
    provider = mail.get_provider()
    assert isinstance(provider, SmtpProvider) and provider.ssl_enabled and provider.port == 465
