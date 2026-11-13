import asyncio
import logging
from datetime import datetime, timezone
from html import escape

from ..keyboards import reminder_keyboard

log = logging.getLogger(__name__)


def reminder_text(row, retry_number: int | None = None, max_retries: int = 0) -> str:
    if retry_number is None:
        text = f"⏰ <b>Recordatorio #{row['id']}</b>\n\n{escape(row['description'])}"
    else:
        text = (
            f"🔔 <b>Recordatorio #{row['id']} — aviso de seguimiento "
            f"{retry_number}/{max_retries}</b>\n\n{escape(row['description'])}"
        )
    if row["tags"]:
        tags = " ".join(
            f"#{escape(tag.strip())}"
            for tag in row["tags"].split(",")
            if tag.strip()
        )
        if tags:
            text += f"\n\n🏷 {tags}"
    if row["note"]:
        text += f"\n\n🗒 <b>Nota:</b> {escape(row['note'])}"
    if row["url"]:
        text += f'\n\n🔗 <a href="{escape(row["url"], quote=True)}">Abrir enlace</a>'
    return text


async def send_reminder(bot, row, retry_number: int | None = None, max_retries: int = 0):
    return await bot.send_message(
        chat_id=row["chat_id"],
        text=reminder_text(row, retry_number, max_retries),
        reply_markup=reminder_keyboard(row["id"]),
    )


async def scheduler_loop(bot, db, settings):
    while True:
        try:
            now = datetime.now(timezone.utc)
            due = await db.due_reminders(now)
            for row in due:
                try:
                    sent_message = await send_reminder(bot, row)
                    await db.mark_notified(row["id"], sent_message.message_id)
                except Exception:
                    log.exception("No se pudo enviar el recordatorio #%s", row["id"])

            retries = await db.due_retry_reminders(
                datetime.now(timezone.utc),
                settings.reminder_repeat_minutes,
                settings.reminder_max_retries,
            )
            for row in retries:
                attempt = row["retry_count"] + 1
                try:
                    sent_message = await send_reminder(
                        bot, row, attempt, settings.reminder_max_retries
                    )
                    await db.mark_retry_sent(
                        row["id"],
                        datetime.now(timezone.utc),
                        sent_message.message_id,
                        settings.reminder_max_retries,
                    )
                    old_message_id = row["active_message_id"]
                    if old_message_id:
                        try:
                            await bot.edit_message_reply_markup(
                                chat_id=row["chat_id"],
                                message_id=old_message_id,
                                reply_markup=None,
                            )
                        except Exception:
                            log.debug(
                                "No se pudo retirar el teclado del aviso anterior #%s",
                                row["id"],
                                exc_info=True,
                            )
                except Exception:
                    log.exception(
                        "No se pudo reenviar el recordatorio #%s",
                        row["id"],
                    )

        except Exception:
            log.exception("Error en el ciclo de recordatorios")

        await asyncio.sleep(10)
