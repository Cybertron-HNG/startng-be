"""Who actually sends the email. Everything else talks to EmailProvider, so moving off Brevo means one new class here.

    EMAIL_PROVIDER=log     emails go to the server log (local work, tests)
    EMAIL_PROVIDER=brevo   sent with Brevo's API (BREVO_API_KEY)
    EMAIL_PROVIDER=smtp    sent with an SMTP server (SMTP_* settings)
"""
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Protocol

import requests

from ...config import settings

log = logging.getLogger(__name__)


@dataclass
class Message:
    to: str
    subject: str
    html: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


class SendError(Exception):
    """The provider didn't take the email. retry=False: trying again won't help (a bad address, a bad key)."""

    def __init__(self, message: str, retry: bool = True):
        super().__init__(message)
        self.retry = retry


class EmailProvider(Protocol):
    def send(self, message: Message) -> str:
        """Send it; return the provider's message id. Raise SendError when it isn't taken."""
        ...


class LogProvider:
    """Writes each email to the log instead of sending it. Keeps the last ones for tests."""

    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> str:
        self.sent.append(message)
        log.info("Email (not sent, EMAIL_PROVIDER=log) to %s: %s\n%s", message.to, message.subject, message.text)
        return f"log-{len(self.sent)}"


class BrevoProvider:
    URL = "https://api.brevo.com/v3/smtp/email"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def send(self, message: Message) -> str:
        name, address = parseaddr(settings.email_from)
        body: dict = {
            "sender": {"name": name or "start.ng", "email": address},
            "to": [{"email": message.to}],
            "subject": message.subject,
            "htmlContent": message.html,
            "textContent": message.text,
            "headers": message.headers,
        }
        if settings.email_reply_to:
            body["replyTo"] = {"email": settings.email_reply_to}
        if message.tags:
            body["tags"] = message.tags
        try:
            r = requests.post(self.URL, json=body, timeout=20,
                              headers={"api-key": self.api_key, "accept": "application/json"})
        except requests.RequestException as e:
            raise SendError(f"Couldn't reach Brevo: {e}")
        if r.status_code in (200, 201, 202):
            return str(r.json().get("messageId", ""))
        detail = r.text[:300]
        # 400: the email itself is wrong; 401/403: the key is wrong or the sender isn't verified. Retrying won't help.
        # 429 (too many) and 5xx (Brevo is down) are worth another try.
        raise SendError(f"Brevo said {r.status_code}: {detail}", retry=r.status_code == 429 or r.status_code >= 500)


class SmtpProvider:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        starttls: bool,
        ssl_enabled: bool,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.starttls = starttls
        self.ssl_enabled = ssl_enabled

    def send(self, message: Message) -> str:
        sender_name, sender_address = parseaddr(settings.email_from)
        if not sender_address:
            raise SendError("EMAIL_FROM must contain a valid email address", retry=False)
        email = EmailMessage()
        email["From"] = f"{sender_name} <{sender_address}>" if sender_name else sender_address
        email["To"] = message.to
        email["Subject"] = message.subject
        if settings.email_reply_to:
            email["Reply-To"] = settings.email_reply_to
        for name, value in message.headers.items():
            email[name] = value
        email.set_content(message.text)
        email.add_alternative(message.html, subtype="html")
        context = ssl.create_default_context()
        try:
            if self.ssl_enabled:
                with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=20) as client:
                    self._login_and_send(client, email, sender_address)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=20) as client:
                    client.ehlo()
                    if self.starttls:
                        client.starttls(context=context)
                        client.ehlo()
                    self._login_and_send(client, email, sender_address)
        except (OSError, smtplib.SMTPException) as error:
            raise SendError(f"SMTP delivery failed: {error}")
        return email.get("Message-ID", "")

    def _login_and_send(self, client: smtplib.SMTP, email: EmailMessage, sender: str) -> None:
        if self.username:
            client.login(self.username, self.password)
        client.send_message(email, from_addr=sender)


_log_provider = LogProvider()


def get_provider() -> EmailProvider:
    if settings.email_provider == "brevo":
        if not settings.brevo_api_key:
            raise SendError("EMAIL_PROVIDER is brevo but BREVO_API_KEY isn't set", retry=False)
        return BrevoProvider(settings.brevo_api_key)
    if settings.email_provider == "smtp":
        if not settings.smtp_host:
            raise SendError("EMAIL_PROVIDER is smtp but SMTP_HOST isn't set", retry=False)
        if settings.smtp_ssl and settings.smtp_starttls:
            raise SendError("SMTP_SSL and SMTP_STARTTLS cannot both be enabled", retry=False)
        return SmtpProvider(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_username,
            settings.smtp_password,
            settings.smtp_starttls,
            settings.smtp_ssl,
        )
    return _log_provider
