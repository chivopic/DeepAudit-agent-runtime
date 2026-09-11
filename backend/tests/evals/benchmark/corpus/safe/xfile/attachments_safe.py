"""Attachment download endpoint."""
from .validators_safe import resolve_within

BASE = "/srv/attachments"


def read_attachment(name: str) -> bytes:
    with open(resolve_within(BASE, name), "rb") as fh:
        return fh.read()
