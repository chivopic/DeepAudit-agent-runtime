"""User lookup helpers."""
import sqlite3


def get_user(conn: sqlite3.Connection, user_id: str):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = '%s'" % user_id)
    return cur.fetchone()


def search(conn: sqlite3.Connection, term: str):
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM users WHERE name LIKE '" + term + "'")
    return cur.fetchall()
