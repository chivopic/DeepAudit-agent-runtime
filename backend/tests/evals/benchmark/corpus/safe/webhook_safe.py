"""Outbound webhook delivery, restricted to vetted hosts."""
from urllib.parse import urlparse

import requests

ALLOWED_HOSTS = {"hooks.example.com", "api.example.com"}


def fetch_preview(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("destination is not on the webhook allowlist")
    resp = requests.get(url, timeout=5, allow_redirects=False)
    return resp.text[:2000]
