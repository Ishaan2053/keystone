"""Optional Slack incoming-webhook transport (Slack notifications).

One shared maintainer channel is selected when the incoming webhook is created;
Keystone holds no Slack OAuth or bot token. ``SLACK_WEBHOOK_URL`` is an optional
secret: a blank value disables Slack, and an invalid value disables Slack with a
fixed warning, never a value-bearing error.

Delivery is best-effort. ``SlackSender.send`` makes exactly one HTTP attempt with
no retries and no redirects, so a Slack failure can never fail triage or roll
back GitHub actions.

The five-second value is a *network-operation* timeout (connect/read/write/pool
each get it), not an exactly-five-second wall-clock guarantee for the whole call.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("api.slack")

# The only accepted destination: Slack's incoming-webhook service.
WEBHOOK_HOST = "hooks.slack.com"
# Applied to every network operation, not to the total wall-clock duration.
TIMEOUT_SECONDS = 5.0
# Fixed copy: an invalid configured URL must never echo the value or its token.
INVALID_URL_WARNING = (
    "SLACK_WEBHOOK_URL is not a valid Slack incoming webhook; Slack notifications are disabled"
)
# Fixed copy: a failed delivery must never echo the URL, status body or exception.
DELIVERY_FAILURE_WARNING = "Slack notification delivery failed"

_quiet_lock = threading.Lock()
_quiet_users = 0
_quiet_previous: dict[str, int] = {}


def validate_webhook_url(value: str | None) -> str | None:
    """Return the validated incoming-webhook URL, or ``None`` when Slack is disabled.

    A missing or blank value disables Slack silently. A nonblank value that is
    not an ``https://hooks.slack.com/services/<a>/<b>/<c>`` URL logs one fixed
    warning and disables Slack instead of failing startup. Nothing about the
    value is ever echoed or raised, so a mistyped secret cannot reach a log or
    an exception message.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if _is_webhook_url(text):
        return text
    logger.warning(INVALID_URL_WARNING)
    return None


def _is_webhook_url(text: str) -> bool:
    """Whether ``text`` is an exact Slack incoming-webhook URL."""
    # Reject any percent escape outright: an encoded separator such as %2F would
    # change how many path segments a receiver sees.
    if "%" in text:
        return False
    try:
        parsed = urlparse(text)
        if parsed.scheme != "https" or parsed.hostname != WEBHOOK_HOST:
            return False
        if parsed.username or parsed.password:
            return False
        # An explicit or malformed port is never part of a webhook URL.
        if parsed.port is not None:
            return False
    except ValueError:
        return False
    if parsed.params or parsed.query or parsed.fragment:
        return False
    # Exactly /services/<nonempty>/<nonempty>/<nonempty>, nothing more.
    segments = parsed.path.split("/")
    if len(segments) != 5 or segments[0] or segments[1] != "services":
        return False
    return all(segments[2:])


@contextlib.contextmanager
def _quiet_http_clients():
    """Silence ``httpx``/``httpcore`` request logging for one owned call.

    ``httpx`` logs the full request line at INFO and ``httpcore`` logs connection
    detail at DEBUG; either can contain the secret webhook path. Their levels are
    suppressed until the last overlapping call finishes. The lock protects only
    bookkeeping, never network work; an earlier completion cannot restore DEBUG
    under a still-active send, nor can overlapping calls overwrite saved levels.
    """
    global _quiet_users
    with _quiet_lock:
        names = {"httpx", "httpcore"}
        for name in list(logging.root.manager.loggerDict):
            if name.startswith(("httpx.", "httpcore.")):
                names.add(name)
        for name in sorted(names):
            if name not in _quiet_previous:
                _quiet_previous[name] = logging.getLogger(name).level
                logging.getLogger(name).setLevel(logging.WARNING)
        _quiet_users += 1
    try:
        yield
    finally:
        with _quiet_lock:
            _quiet_users -= 1
            if not _quiet_users:
                for name, level in _quiet_previous.items():
                    logging.getLogger(name).setLevel(level)
                _quiet_previous.clear()


class SlackSender:
    """Send one Slack incoming-webhook payload in a single bounded attempt.

    The injected client owns the transport and connection lifecycle (the app
    builds one shared client); tests inject ``httpx.MockTransport`` so no real
    webhook is ever contacted. ``send`` returns ``True`` only for the documented
    successful response, and never raises.
    """

    def __init__(self, webhook_url: str, client: httpx.Client):
        self.webhook_url = webhook_url
        self.client = client

    def send(self, payload: dict) -> bool:
        """POST ``payload`` once; return whether Slack acknowledged it.

        Success requires HTTP 200 and a body whose stripped form is ``ok``.
        Every other outcome — redirect, 429, 5xx, unexpected body, transport or
        client error — logs a fixed category (never the URL, body or exception)
        and returns ``False``.
        """
        try:
            with _quiet_http_clients():
                response = self.client.post(
                    self.webhook_url,
                    json=payload,
                    timeout=TIMEOUT_SECONDS,
                    follow_redirects=False,
                )
                if response.status_code != 200:
                    logger.warning("%s (status %d)", DELIVERY_FAILURE_WARNING, response.status_code)
                    return False
                body = response.text.strip()
        except httpx.HTTPError:
            logger.warning("%s (transport)", DELIVERY_FAILURE_WARNING)
            return False
        except Exception:
            # A best-effort notifier must never let any client failure escape.
            logger.warning("%s (client)", DELIVERY_FAILURE_WARNING)
            return False
        if body != "ok":
            logger.warning("%s (unexpected body)", DELIVERY_FAILURE_WARNING)
            return False
        return True
