"""Small security helpers; Django handles password hashing, sessions and CSRF."""

import os
import secrets
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured


def secret_key(base_dir, debug):
    """Use a private local key for development; require an explicit key for hosting."""
    configured = os.environ.get("SECRET_KEY", "")
    if configured:
        if len(configured) < 50 or len(set(configured)) < 5:
            raise ImproperlyConfigured(
                "SECRET_KEY must be a random value at least 50 characters long."
            )
        return configured
    if not debug:
        raise ImproperlyConfigured("Set SECRET_KEY before running with DEBUG=0.")

    path = Path(base_dir) / ".local-secret"
    try:
        # Exclusive creation avoids overwriting a key another process just created.
        with open(
            path, "x", opener=lambda name, flags: os.open(name, flags, 0o600)
        ) as handle:
            handle.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    return path.read_text().strip()


def client_ip(request):
    """Do not trust client-supplied forwarding headers for login throttling."""
    return request.META.get("REMOTE_ADDR")
