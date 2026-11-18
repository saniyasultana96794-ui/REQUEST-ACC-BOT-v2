import csv
import os
import shutil
import sqlite3
from datetime import datetime, timezone


class Database:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self.connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                welcome_text TEXT NOT NULL DEFAULT '🎉 Welcome {first_name}! Thanks for joining ❤️',
                button_text TEXT NOT NULL DEFAULT '🚀 Start Now',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                started INTEGER NOT NULL DEFAULT 0,
                clicked INTEGER NOT NULL DEFAULT 0,
                source_channel INTEGER,
                blocked INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS join_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0,
                welcome_sent INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, chat_id)
            );
            """)
            # Safe migrations for older database versions.
            self._ensure_column(con, "users", "started", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(con, "users", "clicked", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(con, "users", "source_channel", "INTEGER")
            self._ensure_column(con, "users", "blocked", "INTEGER NOT NULL DEFAULT 0")

    def _ensure_column(self, con, table, column, definition):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def now(self):
        return datetime.now(timezone.utc).isoformat()

    def add_channel(self, chat_id, title):
        with self.connect() as con:
            con.execute("INSERT INTO channels(chat_id,title,created_at) VALUES(?,?,?) "
                        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
                        (chat_id, title, self.now()))

    def get_channel(self, chat_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM channels WHERE chat_id=?", (chat_id,)).fetchone()
            return dict(row) if row else None

    def list_channels(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM channels ORDER BY created_at DESC")]

    def set_channel_field(self, chat_id, field, value):
        if field not in ("welcome_text", "button_text", "enabled"):
            raise ValueError("Invalid channel field")
        with self.connect() as con:
            con.execute(f"UPDATE channels SET {field}=? WHERE chat_id=?", (value, chat_id))

    def upsert_user(self, user_id, first_name, username, source_channel=None, joined_channel=None):
        now = self.now()
        with self.connect() as con:
            con.execute("""
                INSERT INTO users(user_id,first_name,username,started,clicked,source_channel,first_seen,last_seen)
                VALUES(?,?,?,0,0,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name=excluded.first_name,
                    username=excluded.username,
                    source_channel=COALESCE(excluded.source_channel, users.source_channel),
                    last_seen=excluded.last_seen
            """, (user_id, first_name, username, source_channel, now, now))
            if source_channel is not None:
                con.execute("UPDATE users SET started=1, clicked=1, source_channel=? WHERE user_id=?",
                            (source_channel, user_id))
            if joined_channel is not None:
                con.execute("INSERT OR IGNORE INTO join_requests(user_id,chat_id,created_at) VALUES(?,?,?)",
                            (user_id, joined_channel, now))
            else:
                con.execute("UPDATE users SET started=1 WHERE user_id=?", (user_id,))

    def mark_approved(self, user_id, chat_id):
        with self.connect() as con:
            con.execute("UPDATE join_requests SET approved=1 WHERE user_id=? AND chat_id=?", (user_id, chat_id))

    def mark_welcome_sent(self, user_id, chat_id):
        with self.connect() as con:
            con.execute("UPDATE join_requests SET welcome_sent=1 WHERE user_id=? AND chat_id=?", (user_id, chat_id))

    def user_counts(self):
        with self.connect() as con:
            all_users = con.execute("SELECT COUNT(*) FROM users WHERE started=1").fetchone()[0]
            clicked = con.execute("SELECT COUNT(*) FROM users WHERE clicked=1").fetchone()[0]
            not_started = con.execute("SELECT COUNT(DISTINCT user_id) FROM join_requests WHERE user_id NOT IN (SELECT user_id FROM users WHERE started=1)").fetchone()[0]
            return {"all": all_users, "clicked": clicked, "not_started": not_started}

    def stats(self):
        with self.connect() as con:
            return {
                "channels": con.execute("SELECT COUNT(*) FROM channels").fetchone()[0],
                "join_requests": con.execute("SELECT COUNT(*) FROM join_requests").fetchone()[0],
                "approved": con.execute("SELECT COUNT(*) FROM join_requests WHERE approved=1").fetchone()[0],
                "users": con.execute("SELECT COUNT(*) FROM users WHERE started=1").fetchone()[0],
                "clicked": con.execute("SELECT COUNT(*) FROM users WHERE clicked=1").fetchone()[0],
                "welcomes": con.execute("SELECT COUNT(*) FROM join_requests WHERE welcome_sent=1").fetchone()[0],
            }

    def broadcast_targets(self, audience):
        clause = "started=1 AND blocked=0"
        if audience == "clicked":
            clause += " AND clicked=1"
        with self.connect() as con:
            return [r[0] for r in con.execute(f"SELECT user_id FROM users WHERE {clause}")]

    def mark_blocked(self, user_id):
        with self.connect() as con:
            con.execute("UPDATE users SET blocked=1 WHERE user_id=?", (user_id,))

    def list_clicked_users(self, limit=20):
        with self.connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT user_id,first_name,username FROM users WHERE clicked=1 ORDER BY last_seen DESC LIMIT ?", (limit,)
            )]

    def export_csv(self, clicked_only=False):
        path = "button_clicked_users.csv" if clicked_only else "users_export.csv"
        query = "SELECT user_id,first_name,username,started,clicked,source_channel,blocked,first_seen,last_seen FROM users"
        if clicked_only:
            query += " WHERE clicked=1"
        query += " ORDER BY first_seen"
        with self.connect() as con:
            rows = con.execute(query).fetchall()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["user_id","first_name","username","started","clicked_from_welcome","source_channel","blocked","first_seen","last_seen"])
            writer.writerows([tuple(r) for r in rows])
        return path

    def backup(self, directory):
        os.makedirs(directory, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(directory, f"autojoin_backup_{stamp}.db")
        with self.connect() as con:
            target = sqlite3.connect(dest)
            con.backup(target)
            target.close()
        return dest

    def restore(self, source, backup_dir):
        # Validate SQLite before replacing the live DB, and keep a safety copy.
        test = sqlite3.connect(source)
        try:
            result = test.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in test.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if result != "ok" or not {"users", "channels", "join_requests"}.issubset(tables):
                raise ValueError("Backup file is invalid or incomplete.")
        finally:
            test.close()
        self.backup(backup_dir)
        # Close connections before replace; WAL is checkpointed first.
        con = self.connect()
        try:
            con.execute("PRAGMA wal_checkpoint(FULL)")
        finally:
            con.close()
        # Remove stale WAL sidecars before replacing the main database file.
        for suffix in ("-wal", "-shm"):
            sidecar = self.path + suffix
            if os.path.exists(sidecar):
                os.remove(sidecar)
        shutil.copy2(source, self.path)
        # Reopen and ensure schema is valid.
        with self.connect() as con:
            con.execute("SELECT COUNT(*) FROM users").fetchone()
