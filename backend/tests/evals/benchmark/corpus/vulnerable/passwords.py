"""Credential helpers."""
import hashlib

API_KEY = "sk-live-9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"


def hash_password(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest()
