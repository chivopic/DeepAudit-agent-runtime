"""Reporting queries."""
import sqlite3

from .escape_util_safe import bind


def report_by_name(conn: sqlite3.Connection, name: str):
    cur = conn.cursor()
    cur.execute("SELECT * FROM reports WHERE owner = ?", bind(name))
    return cur.fetchall()
