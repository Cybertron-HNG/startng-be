"""Who actually sends the email. Everything else talks to EmailProvider, so moving off Brevo means one new class here.

    EMAIL_PROVIDER=log     emails go to the server log (local work, tests)
    EMAIL_PROVIDER=brevo   sent with Brevo's API (BREVO_API_KEY)
"""
import logging
from dataclasses import dataclass, field
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


_log_provider = LogProvider()


def get_provider() -> EmailProvider:
    if settings.email_provider == "brevo":
        if not settings.brevo_api_key:
            raise SendError("EMAIL_PROVIDER is brevo but BREVO_API_KEY isn't set", retry=False)
        return BrevoProvider(settings.brevo_api_key)
    return _log_provider
