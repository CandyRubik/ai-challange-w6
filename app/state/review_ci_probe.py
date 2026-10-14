import sqlite3

def normalize_review_label(label: str) -> str:
    return label.strip()
