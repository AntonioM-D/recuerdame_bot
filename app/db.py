from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from pathlib import Path

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    description TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    recurrence TEXT,
    created_at TEXT NOT NULL,
    notified_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    active_message_id INTEGER,
    url TEXT,
    note TEXT,
    tags TEXT
);

CREATE INDEX IF NOT EXISTS idx_reminders_due
ON reminders(status, due_at);

CREATE INDEX IF NOT EXISTS idx_reminders_user
ON reminders(user_id, status);

CREATE TABLE IF NOT EXISTS reminder_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    description TEXT NOT NULL,
    due_at TEXT,
    new_due_at TEXT,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reminder_events_user_time
ON reminder_events(user_id, occurred_at DESC, id DESC);
"""


async def _record_event(
    db,
    reminder_id: int,
    user_id: int,
    event_type: str,
    description: str,
    due_at: str | None,
    new_due_at: str | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO reminder_events
        (reminder_id, user_id, event_type, description, due_at, new_due_at, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            reminder_id,
            user_id,
            event_type,
            description,
            due_at,
            new_due_at,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


class Database:

    def __init__(self, path: str):
        self.path = path

    @asynccontextmanager
    async def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=5000")
            yield db

    async def initialize(self):
        async with self.connect() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(SCHEMA)

            cur = await db.execute("PRAGMA table_info(reminders)")
            columns = {row["name"] for row in await cur.fetchall()}

            if "recurrence_at" not in columns:
                await db.execute(
                    "ALTER TABLE reminders ADD COLUMN recurrence_at TEXT"
                )

            if "url" not in columns:
                await db.execute(
                    "ALTER TABLE reminders ADD COLUMN url TEXT"
                )

            if "note" not in columns:
                await db.execute(
                    "ALTER TABLE reminders ADD COLUMN note TEXT"
                )

            if "tags" not in columns:
                await db.execute(
                    "ALTER TABLE reminders ADD COLUMN tags TEXT"
                )

            if "retry_count" not in columns:
                await db.execute(
                    "ALTER TABLE reminders ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
                )
                # Give already-sent alerts a full interval before their first repeat.
                await db.execute(
                    "UPDATE reminders SET notified_at=? "
                    "WHERE status='notified' AND notified_at IS NOT NULL",
                    (datetime.now(timezone.utc).isoformat(),),
                )

            if "active_message_id" not in columns:
                await db.execute(
                    "ALTER TABLE reminders ADD COLUMN active_message_id INTEGER"
                )

            await db.execute(
                """
                UPDATE reminders
                SET recurrence_at=due_at
                WHERE recurrence IS NOT NULL AND recurrence_at IS NULL
                """
            )
            await db.commit()

    async def create_reminder(
        self,
        user_id: int,
        chat_id: int,
        description: str,
        due_at: datetime,
        recurrence: str | None = None,
        url: str | None = None,
        note: str | None = None,
        tags: list[str] | None = None,
    ) -> int:
        if note is not None and len(note) > 1000:
            raise ValueError("La nota no puede superar los 1000 caracteres.")
        if tags is not None and len(tags) > 10:
            raise ValueError("Puedes asignar un máximo de 10 etiquetas.")

        async with self.connect() as db:
            cur = await db.execute(
                """
                INSERT INTO reminders
                (user_id, chat_id, description, due_at, status,
                 recurrence, recurrence_at, created_at, url, note, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    chat_id,
                    description,
                    due_at.astimezone(timezone.utc).isoformat(),
                    "pending",
                    recurrence,
                    (
                        due_at.astimezone(timezone.utc).isoformat()
                        if recurrence is not None
                        else None
                    ),
                    datetime.now(timezone.utc).isoformat(),
                    url,
                    note,
                    ",".join(tags or []),
                ),
            )
            await _record_event(
                db,
                cur.lastrowid,
                user_id,
                "created",
                description,
                due_at.astimezone(timezone.utc).isoformat(),
            )
            await db.commit()
            return cur.lastrowid

    async def list_reminders(
        self,
        user_id: int,
        today_start: datetime | None = None,
        tomorrow_start: datetime | None = None,
    ):
        async with self.connect() as db:
            if today_start and tomorrow_start:
                cur = await db.execute(
                    """
                    SELECT * FROM reminders
                    WHERE user_id=?
                    AND status IN ('pending', 'notified', 'paused')
                    AND due_at>=? AND due_at<?
                    ORDER BY due_at
                    """,
                    (
                        user_id,
                        today_start.astimezone(timezone.utc).isoformat(),
                        tomorrow_start.astimezone(timezone.utc).isoformat(),
                    ),
                )
            else:
                cur = await db.execute(
                    """
                    SELECT * FROM reminders
                    WHERE user_id=?
                    AND status IN ('pending', 'notified', 'paused')
                    ORDER BY due_at LIMIT 50
                    """,
                    (user_id,),
                )
            return await cur.fetchall()

    async def get_reminder(self, reminder_id: int, user_id: int):
        async with self.connect() as db:
            cur = await db.execute(
                """
                SELECT * FROM reminders
                WHERE id=? AND user_id=?
                AND status IN ('pending', 'notified')
                """,
                (reminder_id, user_id),
            )
            return await cur.fetchone()

    async def cancel_reminder(self, reminder_id: int, user_id: int) -> bool:
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """
                SELECT description, due_at FROM reminders
                WHERE id=? AND user_id=?
                AND status IN ('pending', 'notified', 'paused')
                """,
                (reminder_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                await db.rollback()
                return False
            cur = await db.execute(
                """
                UPDATE reminders SET status='cancelled'
                WHERE id=? AND user_id=?
                AND status IN ('pending', 'notified', 'paused')
                """,
                (reminder_id, user_id),
            )
            if cur.rowcount:
                await _record_event(
                    db, reminder_id, user_id, "cancelled",
                    row["description"], row["due_at"]
                )
            await db.commit()
            return cur.rowcount > 0

    async def set_reminder_note(
        self, reminder_id: int, user_id: int, note: str | None
    ) -> bool:
        if note is not None and len(note) > 1000:
            raise ValueError("La nota no puede superar los 1000 caracteres.")

        async with self.connect() as db:
            cur = await db.execute(
                """
                UPDATE reminders SET note=?
                WHERE id=? AND user_id=?
                AND status IN ('pending', 'notified', 'paused')
                """,
                (note, reminder_id, user_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def set_reminder_tags(
        self, reminder_id: int, user_id: int, tags: list[str]
    ) -> bool:
        if len(tags) > 10:
            raise ValueError("Puedes asignar un máximo de 10 etiquetas.")

        async with self.connect() as db:
            cur = await db.execute(
                """
                UPDATE reminders SET tags=?
                WHERE id=? AND user_id=?
                AND status IN ('pending', 'notified', 'paused')
                """,
                (",".join(tags), reminder_id, user_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def pause_recurrence(self, reminder_id: int, user_id: int) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                """
                UPDATE reminders SET status='paused'
                WHERE id=? AND user_id=? AND recurrence IS NOT NULL
                AND status IN ('pending', 'notified')
                """,
                (reminder_id, user_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def resume_recurrence(self, reminder_id: int, user_id: int) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                """
                UPDATE reminders SET status='pending', notified_at=NULL,
                    retry_count=0, active_message_id=NULL
                WHERE id=? AND user_id=? AND recurrence IS NOT NULL
                AND status='paused'
                """,
                (reminder_id, user_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def change_recurrence(
        self, reminder_id: int, user_id: int, recurrence: str
    ) -> bool:
        if recurrence not in ('daily', 'weekly', 'monthly'):
            raise ValueError('Periodicidad no válida')

        async with self.connect() as db:
            cur = await db.execute(
                """
                UPDATE reminders SET recurrence=?
                WHERE id=? AND user_id=? AND recurrence IS NOT NULL
                AND status IN ('pending', 'notified', 'paused')
                """,
                (recurrence, reminder_id, user_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def cancel_notified_reminder(
        self,
        reminder_id: int,
        user_id: int,
    ) -> bool:
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """
                SELECT description, due_at FROM reminders
                WHERE id=? AND user_id=? AND status='notified'
                """,
                (reminder_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                await db.rollback()
                return False
            cur = await db.execute(
                """
                UPDATE reminders SET status='cancelled'
                WHERE id=? AND user_id=? AND status='notified'
                """,
                (reminder_id, user_id),
            )
            if cur.rowcount:
                await _record_event(
                    db, reminder_id, user_id, "cancelled",
                    row["description"], row["due_at"]
                )
            await db.commit()
            return cur.rowcount > 0

    async def complete_reminder(
        self,
        reminder_id: int,
        user_id: int,
        timezone_name: str = "Europe/Madrid",
    ) -> bool:
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """
                SELECT * FROM reminders
                WHERE id=? AND user_id=? AND status='notified'
                """,
                (reminder_id, user_id),
            )
            row = await cur.fetchone()

            if row is None:
                await db.rollback()
                return False

            recurrence = row["recurrence"]
            if recurrence in ("daily", "weekly", "monthly"):
                tz = ZoneInfo(timezone_name)
                occurrence = datetime.fromisoformat(
                    row["recurrence_at"] or row["due_at"]
                ).astimezone(tz)
                now = datetime.now(tz)

                while occurrence <= now:
                    if recurrence == "daily":
                        occurrence += timedelta(days=1)
                    elif recurrence == "weekly":
                        occurrence += timedelta(weeks=1)
                    else:
                        occurrence += relativedelta(months=1)

                next_due = occurrence.astimezone(timezone.utc).isoformat()
                cur = await db.execute(
                    """
                    UPDATE reminders
                    SET due_at=?, recurrence_at=?,
                        status='pending', notified_at=NULL, retry_count=0,
                        active_message_id=NULL
                    WHERE id=? AND user_id=? AND status='notified'
                    """,
                    (next_due, next_due, reminder_id, user_id),
                )
            else:
                cur = await db.execute(
                    """
                    UPDATE reminders SET status='completed'
                    WHERE id=? AND user_id=? AND status='notified'
                    """,
                    (reminder_id, user_id),
                )

            if cur.rowcount:
                await _record_event(
                    db, reminder_id, user_id, "completed",
                    row["description"], row["due_at"]
                )
            await db.commit()
            return cur.rowcount > 0


    async def postpone_reminder(
        self,
        reminder_id: int,
        user_id: int,
        due_at: datetime,
    ) -> bool:
        async with self.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """
                SELECT description, due_at FROM reminders
                WHERE id=? AND user_id=? AND status='notified'
                """,
                (reminder_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                await db.rollback()
                return False
            new_due_at = due_at.astimezone(timezone.utc).isoformat()
            cur = await db.execute(
                """
                UPDATE reminders
                SET due_at=?, status='pending', notified_at=NULL,
                    retry_count=0, active_message_id=NULL
                WHERE id=? AND user_id=? AND status='notified'
                """,
                (new_due_at, reminder_id, user_id),
            )
            if cur.rowcount:
                await _record_event(
                    db, reminder_id, user_id, "postponed",
                    row["description"], row["due_at"], new_due_at
                )
            await db.commit()
            return cur.rowcount > 0


    async def due_reminders(self, now: datetime):
        async with self.connect() as db:
            cur = await db.execute(
                """
                SELECT * FROM reminders
                WHERE status='pending' AND due_at<=?
                ORDER BY due_at LIMIT 100
                """,
                (now.astimezone(timezone.utc).isoformat(),),
            )
            return await cur.fetchall()

    async def mark_notified(self, reminder_id: int, message_id: int):
        async with self.connect() as db:
            await db.execute(
                """
                UPDATE reminders
                SET status='notified', notified_at=?, retry_count=0,
                    active_message_id=?
                WHERE id=? AND status='pending'
                """,
                (datetime.now(timezone.utc).isoformat(), message_id, reminder_id),
            )
            await db.commit()

    async def due_retry_reminders(
        self, now: datetime, interval_minutes: int, max_retries: int
    ):
        if max_retries <= 0:
            return []
        cutoff = (now - timedelta(minutes=interval_minutes)).astimezone(
            timezone.utc
        ).isoformat()
        async with self.connect() as db:
            cur = await db.execute(
                """
                SELECT * FROM reminders
                WHERE status='notified' AND retry_count < ?
                AND notified_at IS NOT NULL AND notified_at <= ?
                ORDER BY notified_at LIMIT 100
                """,
                (max_retries, cutoff),
            )
            return await cur.fetchall()

    async def mark_retry_sent(
        self,
        reminder_id: int,
        sent_at: datetime,
        message_id: int,
        max_retries: int,
    ) -> bool:
        async with self.connect() as db:
            cur = await db.execute(
                """
                UPDATE reminders
                SET retry_count=retry_count+1, notified_at=?, active_message_id=?
                WHERE id=? AND status='notified' AND retry_count < ?
                """,
                (
                    sent_at.astimezone(timezone.utc).isoformat(),
                    message_id,
                    reminder_id,
                    max_retries,
                ),
            )
            await db.commit()
            return cur.rowcount > 0

    async def list_history(self, user_id: int, limit: int = 15):
        async with self.connect() as db:
            cur = await db.execute(
                """
                SELECT * FROM reminder_events
                WHERE user_id=?
                ORDER BY occurred_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            return await cur.fetchall()

    async def history_statistics(self, user_id: int, since: datetime):
        async with self.connect() as db:
            cur = await db.execute(
                """
                SELECT event_type, COUNT(*) AS total
                FROM reminder_events
                WHERE user_id=? AND occurred_at>=?
                GROUP BY event_type
                """,
                (user_id, since.astimezone(timezone.utc).isoformat()),
            )
            return {row["event_type"]: row["total"] for row in await cur.fetchall()}

    async def count_active(self):
        async with self.connect() as db:
            cur = await db.execute(
                """
                SELECT COUNT(*) AS n FROM reminders
                WHERE status IN ('pending', 'notified', 'paused')
                """
            )
            row = await cur.fetchone()
            return row["n"]
