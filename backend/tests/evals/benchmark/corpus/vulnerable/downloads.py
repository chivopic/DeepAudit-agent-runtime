"""Serve user-uploaded files."""
import os

BASE = "/srv/uploads"


def read_upload(name: str) -> bytes:
    path = os.path.join(BASE, name)
    with open(path, "rb") as fh:
        return fh.read()
