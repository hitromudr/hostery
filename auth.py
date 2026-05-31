import hmac
import logging
import secrets
from functools import wraps

from flask import Response, request

logger = logging.getLogger(__name__)


def parse_auth_env(value):
    """Return (mode, creds). mode in {'off','fixed','generated'}."""
    if value is not None and value.strip().lower() == "off":
        return "off", None
    if value:
        user, _, pw = value.partition(":")
        return "fixed", (user or "admin", pw)
    # Unset → generate a random password so we are never open by default.
    pw = secrets.token_urlsafe(12)
    return "generated", ("admin", pw)


def check_credentials(creds, user, pw):
    if not creds:
        return False
    return hmac.compare_digest(creds[0], user or "") and hmac.compare_digest(creds[1], pw or "")


def init_auth(app, env_value):
    mode, creds = parse_auth_env(env_value)
    app.config["AUTH_MODE"] = mode
    if mode == "generated":
        logger.warning("hostery basic auth ENABLED (generated). user=%s password=%s "
                       "(set HOSTERY_AUTH=user:pass to fix, or HOSTERY_AUTH=off to disable)",
                       creds[0], creds[1])
    elif mode == "fixed":
        logger.info("hostery basic auth enabled (HOSTERY_AUTH)")
    else:
        logger.warning("hostery basic auth DISABLED (HOSTERY_AUTH=off)")

    if mode == "off":
        return

    @app.before_request
    def _require_auth():
        a = request.authorization
        if not a or not check_credentials(creds, a.username, a.password):
            return Response("Auth required", 401,
                            {"WWW-Authenticate": 'Basic realm="hostery"'})
