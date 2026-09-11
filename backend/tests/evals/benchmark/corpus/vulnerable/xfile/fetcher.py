"""Link preview fetcher."""
from urllib.parse import urlparse

import requests

from .policy import host_allowed


def preview(url: str) -> str:
    if not host_allowed(urlparse(url).hostname or ""):
        raise PermissionError("destination not permitted")
    return requests.get(url, timeout=5).text[:2000]
