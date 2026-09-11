"""Attachment download endpoint."""
import os

from .validators import is_safe_path

BASE = "/srv/attachments"


def read_attachment(name: str) -> bytes:
    if not is_safe_path(name):
        raise PermissionError("unsafe attachment name")
    with open(os.path.join(BASE, name), "rb") as fh:
        return fh.read()
