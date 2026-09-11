"""Link preview fetcher."""
from urllib.parse import urlparse

import requests

from .policy_safe import host_allowed


def preview(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not host_allowed(parsed.hostname or ""):
        raise PermissionError("destination not permitted")
    return requests.get(url, timeout=5, allow_redirects=False).text[:2000]
