"""Credential helpers."""
import hashlib
import os

API_KEY = os.environ["BILLING_API_KEY"]


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
