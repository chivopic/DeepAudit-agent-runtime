"""Serve user-uploaded files, confined to the upload root."""
import os

BASE = "/srv/uploads"


def read_upload(name: str) -> bytes:
    path = os.path.realpath(os.path.join(BASE, name))
    root = os.path.realpath(BASE)
    if path != root and not path.startswith(root + os.sep):
        raise PermissionError("path escapes the upload root")
    with open(path, "rb") as fh:
        return fh.read()
