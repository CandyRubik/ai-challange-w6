"""Normalize and persist a task title."""
import sqlite3


def normalize_task_title(title: str) -> str:
    normalized = title.strip()
    with sqlite3.connect("task-titles.sqlite3") as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS normalized_titles (title TEXT NOT NULL)")
        connection.execute("INSERT INTO normalized_titles(title) VALUES (?)", (normalized,))
    return normalized
