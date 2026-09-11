"""Reporting queries."""
import sqlite3

from .sanitize_util import clean


def report_by_name(conn: sqlite3.Connection, name: str):
    cur = conn.cursor()
    cur.execute("SELECT * FROM reports WHERE owner = '" + clean(name) + "'")
    return cur.fetchall()
