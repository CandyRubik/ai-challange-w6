"""Preserve former scope assignments while removing runtime personalization."""

import sqlite3


def remove_retired_rag_chat(connection: sqlite3.Connection) -> None:
    """The retired chat's data was explicitly approved for deletion."""
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    for table in ("rag_chat_turns", "rag_chat_state", "rag_chat_sessions"):
        connection.execute(f"DROP TABLE IF EXISTS {table}")


def archive_profile_scope(connection: sqlite3.Connection, table: str) -> None:
    if table not in {"chat_sessions", "long_term_memory"}:
        raise ValueError("Unsupported migration table")
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if "profile_id" not in columns:
        return
    connection.execute("""CREATE TABLE IF NOT EXISTS legacy_profile_scopes (
        record_type TEXT NOT NULL, record_id TEXT NOT NULL, profile_id TEXT,
        PRIMARY KEY (record_type, record_id)
    )""")
    connection.execute(
        f"INSERT OR IGNORE INTO legacy_profile_scopes SELECT ?, id, profile_id FROM {table}", (table,),
    )
    index = "idx_chat_sessions_profile" if table == "chat_sessions" else "idx_long_term_memory_profile"
    connection.execute(f"DROP INDEX IF EXISTS {index}")
    # SQLite keeps all other columns, row IDs and references to the primary key.
    connection.execute(f"ALTER TABLE {table} DROP COLUMN profile_id")
