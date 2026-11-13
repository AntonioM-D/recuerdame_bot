import asyncio
import re
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

import dateparser

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from ..keyboards import (
    menu_main_keyboard,
    menu_page_keyboard,
    postpone_keyboard,
    reminder_keyboard,
    url_confirmation_keyboard,
)
from ..services.parser import parse_reminder
from ..services.url_metadata import extract_metadata, extract_url, is_standalone_url

router = Router()

# La personalización vive mientras el proceso del bot está activo.
# Cada entrada guarda: ID, mensaje original y texto original.
_custom_requests: dict[tuple[int, int], tuple[int, int, str]] = {}
_url_waiting_date: dict[tuple[int, int], dict] = {}
_url_drafts: dict[str, dict] = {}


def local_dt(value: str, timezone_name: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(ZoneInfo(timezone_name))


def format_dt(value: datetime) -> str:
    return value.strftime("%d/%m/%Y a las %H:%M")


def format_reminder_block(row, timezone_name: str) -> str:
    due = format_dt(local_dt(row["due_at"], timezone_name))
    status = {
        "notified": "🔔 Avisado",
        "paused": "⏸ Pausado",
        "pending": "⏳ Pendiente",
    }.get(row["status"], row["status"])
    lines = [
        f"<b>#{row['id']}</b> · {status} · {due}",
        escape(row["description"]),
    ]
    recurrence = {
        "daily": "Diaria",
        "weekly": "Semanal",
        "monthly": "Mensual",
    }.get(row["recurrence"])
    if recurrence:
        lines.append(f"🔁 {recurrence}")
    tags = stored_tags(row["tags"])
    if tags:
        lines.append(f"🏷 {escape(display_tags(tags))}")
    if row["note"]:
        lines.append(f"🗒 {escape(row['note'])}")
    if row["url"]:
        lines.append(f'<a href="{escape(row["url"], quote=True)}">Abrir enlace</a>')
    return "\n".join(lines)


TAG_RE = re.compile(r"(?<![\w])#([\w-]{1,32})", re.UNICODE)
TAG_LABELS = {
    "nas": "NAS",
    "trabajo": "Trabajo",
    "casa": "Casa",
    "personal": "Personal",
    "compras": "Compras",
    "tecnologia": "Tecnología",
    "tecnología": "Tecnología",
}


def normalize_tag(value: str) -> str:
    return re.sub(r"[^\w-]", "", value.strip().lstrip("#").casefold())[:32]


def extract_tags(text: str) -> list[str]:
    tags = []
    for match in TAG_RE.finditer(text):
        tag = normalize_tag(match.group(1))
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:10]


def stored_tags(value: str | None) -> list[str]:
    return [tag for tag in (normalize_tag(item) for item in (value or "").split(",")) if tag]


def display_tags(tags: list[str]) -> str:
    return " ".join(
        f"#{TAG_LABELS.get(tag, tag[:1].upper() + tag[1:])}"
        for tag in tags
    )


def parse_custom_delay(value: str) -> timedelta | None:
    match = re.fullmatch(
        r"\s*(\d+)\s*(minutos?|mins?|m|horas?|hrs?|h|d[ií]as?|dias?|days?|d)\s*",
        value.strip().lower(),
    )
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    if amount < 1 or amount > 3650:
        return None
    if unit in ("m", "min", "mins", "minuto", "minutos"):
        return timedelta(minutes=amount)
    if unit in ("h", "hr", "hrs", "hora", "horas"):
        return timedelta(hours=amount)
    return timedelta(days=amount)


async def append_to_original(callback: CallbackQuery, text: str) -> None:
    if callback.message:
        original = callback.message.text or ""
        await callback.message.edit_text(
            f"{original}\n\n{text}",
            reply_markup=None,
        )


def is_current_alert(callback: CallbackQuery, row) -> bool:
    return bool(
        row
        and row["status"] == "notified"
        and callback.message
        and (
            row["active_message_id"] is None
            or row["active_message_id"] == callback.message.message_id
        )
    )


async def set_postponed(
    callback: CallbackQuery,
    db,
    settings,
    reminder_id: int,
    due_at: datetime,
    confirmation: str,
) -> None:
    ok = await db.postpone_reminder(
        reminder_id,
        callback.from_user.id,
        due_at,
    )
    await callback.answer(
        confirmation if ok else "Este aviso ya no está activo."
    )
    if ok:
        await append_to_original(
            callback,
            f"⏰ Pospuesto hasta {format_dt(due_at.astimezone(ZoneInfo(settings.timezone)))}.",
        )


@router.message(CommandStart())
@router.message(Command("menu"))
async def open_main_menu(message: Message):
    await message.answer(
        "🤖 <b>Recuérdame Bot</b>\nElige qué quieres hacer:",
        reply_markup=menu_main_keyboard(),
    )


@router.callback_query(F.data.startswith("menu:"))
async def interactive_menu(callback: CallbackQuery, db, settings):
    data = callback.data or ""
    message = callback.message
    if message is None:
        await callback.answer()
        return

    if data == "menu:close":
        await message.edit_text("Menú cerrado. Escribe /menu para abrirlo de nuevo.")
        await callback.answer()
        return

    if data == "menu:home":
        await message.edit_text(
            "🤖 <b>Recuérdame Bot</b>\nElige qué quieres hacer:",
            reply_markup=menu_main_keyboard(),
        )
        await callback.answer()
        return

    if data.startswith("menu:section:"):
        section = data.rsplit(":", 1)[-1]
        titles = {
            "create": ("📝 Crear recordatorio", "Puedes escribirlo en lenguaje natural o enviar un enlace."),
            "queries": ("📋 Consultar", "Elige una consulta. Las tareas se muestran por fecha."),
            "recurrence": ("🔁 Recurrencias", "Elige una acción para ver el comando y un ejemplo."),
            "organize": ("🏷 Organizar tareas", "Gestiona etiquetas, notas o cancelaciones."),
            "history": ("📊 Historial y estadísticas", "Consulta las acciones registradas por el bot."),
            "help": ("ℹ️ Ayuda", "Elige un tema para ver instrucciones y ejemplos."),
        }
        if section not in titles:
            await callback.answer("Sección desconocida.", show_alert=True)
            return
        title, text = titles[section]
        await message.edit_text(
            f"<b>{title}</b>\n{text}",
            reply_markup=menu_page_keyboard(section),
        )
        await callback.answer()
        return

    if data.startswith("menu:detail:"):
        key = data.rsplit(":", 1)[-1]
        details = {
            "create": ("📝 Crear un recordatorio", "Escribe: <code>Recuérdame comprar leche mañana a las 18:30</code>. También puedes usar <code>/recordar descripción y fecha</code>."),
            "url": ("🔗 Recordatorio desde un enlace", "Envía al bot un enlace compatible. Si encuentra una fecha fiable, te ofrecerá crear el recordatorio y guardará el enlace."),
            "pause": ("⏸ Pausar una recurrencia", "Usa <code>/pausar ID</code>. Sustituye ID por el número de la tarea que aparece en <code>/lista</code>."),
            "resume": ("▶️ Reactivar una recurrencia", "Usa <code>/reactivar ID</code> para reanudarla."),
            "frequency": ("🔄 Cambiar frecuencia", "Ejemplo: <code>/frecuencia 12 semanal</code>. Frecuencias: diaria, semanal o mensual."),
            "complete": ("✅ Completar una ocurrencia", "Pulsa <b>Hecho</b> en el aviso. Si es recurrente, completará esa ocurrencia y programará la siguiente."),
            "series_cancel": ("🛑 Cancelar una serie", "Usa <code>/cancelar ID</code> para detener la recurrencia completa. El registro se conserva en el historial."),
            "tags": ("🏷 Etiquetas", "Al crear: añade <code>#libros</code>. Para sustituir etiquetas: <code>/etiqueta ID #libros #pelis</code>. Para borrar las de una tarea: <code>/etiqueta ID borrar</code>."),
            "notes": ("🗒 Notas", "Usa <code>/nota ID texto</code> para añadir o cambiar una nota; <code>/nota ID borrar</code> la elimina."),
            "cancel": ("✖ Cancelar un recordatorio", "Usa <code>/cancelar ID</code> o el botón Cancelar de su notificación. La acción conserva el registro."),
            "commands": ("⌨️ Comandos", "El menú nativo de Telegram muestra los comandos disponibles. También puedes escribir <code>/help</code> para ver la guía completa."),
            "time": ("🕰 Fechas y horas", "Si omites la hora, se usan las 09:00 de Europe/Madrid. Puedes escribir, por ejemplo, <code>el viernes a las 18:30</code> o <code>dentro de 2 semanas</code>."),
        }
        if key not in details:
            await callback.answer("Opción desconocida.", show_alert=True)
            return
        title, text = details[key]
        section = {
            "create": "create", "url": "create",
            "pause": "recurrence", "resume": "recurrence",
            "frequency": "recurrence", "complete": "recurrence", "series_cancel": "recurrence",
            "tags": "organize", "notes": "organize", "cancel": "organize",
            "commands": "help", "time": "help",
        }[key]
        await message.edit_text(
            f"<b>{title}</b>\n{text}",
            reply_markup=menu_page_keyboard(section),
        )
        await callback.answer()
        return

    if data.startswith("menu:run:"):
        action = data.rsplit(":", 1)[-1]
        user_id = callback.from_user.id
        now = datetime.now(ZoneInfo(settings.timezone))
        rows = []
        title = ""
        if action == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            rows = await db.list_reminders(user_id, start, start + timedelta(days=1))
            rows = [row for row in rows if row["status"] != "paused"]
            title = "📅 Recordatorios de hoy"
        elif action == "week":
            rows = await db.list_reminders(user_id, now, now + timedelta(days=7))
            rows = [row for row in rows if row["status"] != "paused"]
            title = "🗓 Próximos siete días"
        elif action == "upcoming":
            all_rows = await db.list_reminders(user_id)
            rows = [
                row for row in all_rows
                if row["status"] in ("pending", "notified")
                and local_dt(row["due_at"], settings.timezone) >= now
            ][:10]
            title = "⏭ Próximos recordatorios"
        elif action == "list":
            rows = await db.list_reminders(user_id)
            rows = sorted(rows, key=lambda row: row["due_at"])
            title = "📋 Tus recordatorios activos"
        elif action == "history":
            history = await db.list_history(user_id, 15)
            if not history:
                result = "Todavía no hay actividad en el historial."
            else:
                labels = {"created": "🆕 Creado", "completed": "✅ Completado", "postponed": "⏰ Pospuesto", "cancelled": "🗑 Cancelado"}
                parts = ["<b>🕘 Historial reciente</b>"]
                for row in history:
                    happened = format_dt(local_dt(row["occurred_at"], settings.timezone))
                    parts.append(
                        f"<b>#{row['reminder_id']} · {labels.get(row['event_type'], row['event_type'])}</b> · {happened}\n"
                        f"{escape(row['description'][:180])}"
                    )
                result = "\n\n".join(parts)
            await message.answer(result, disable_web_page_preview=True)
            await callback.answer()
            return
        elif action == "stats":
            days = 30
            since = datetime.now(timezone.utc) - timedelta(days=days)
            totals = await db.history_statistics(user_id, since)
            if not totals:
                result = f"No hay actividad registrada en los últimos {days} días."
            else:
                result = "\n".join([
                    f"<b>📊 Actividad de los últimos {days} días</b>",
                    f"🆕 Creados: {totals.get('created', 0)}",
                    f"✅ Completados: {totals.get('completed', 0)}",
                    f"⏰ Pospuestos: {totals.get('postponed', 0)}",
                    f"🗑 Cancelados: {totals.get('cancelled', 0)}",
                ])
            await message.answer(result)
            await callback.answer()
            return
        else:
            await callback.answer("Acción desconocida.", show_alert=True)
            return

        if not rows:
            empty = {
                "today": "No tienes recordatorios previstos para hoy.",
                "week": "No tienes recordatorios previstos en los próximos siete días.",
                "upcoming": "No tienes próximos recordatorios pendientes.",
                "list": "No tienes recordatorios activos. ¡Todo al día! 👌",
            }
            await message.answer(empty[action])
        else:
            shown = rows[:10]
            result = [f"<b>{title}</b>"]
            result.extend(format_reminder_block(row, settings.timezone) for row in shown)
            if len(rows) > len(shown):
                result.append("Usa /lista para ver el resto.")
            await message.answer("\n\n".join(result), disable_web_page_preview=True)
        await callback.answer()
        return

    await callback.answer()


@router.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "¡Hola! Soy <b>MiBot Personal</b> 🤖\n\n"
        "Puedo gestionar tus recordatorios y enviarte alertas de tus sistemas.\n\n"
        "<b>Ejemplos:</b>\n"
        "• Recuérdame comprar leche mañana a las 18:30\n"
        "• /recordar renovar certificado dentro de 20 días\n"
        "• /recordar revisar copias cada semana\n"
        "• Envíame el enlace de un lanzamiento para revisar su fecha\n"
        "• Recuérdame revisar las copias mañana #NAS\n\n"
        "<b>Comandos:</b>\n"
        "/cancelar ID — cancelar uno\n"
        "/estadisticas [días] — resumen de actividad\n"
        "/estado — estado del bot\n"
        "/etiqueta ID #NAS #Casa — asignar etiquetas o borrar\n"
        "/frecuencia ID diaria|semanal|mensual — cambiar periodicidad\n"
        "/historial [límite] — últimas acciones\n"
        "/hoy — ver los de hoy\n"
        "/lista [#etiqueta] — ver pendientes o filtrar por etiqueta\n"
        "/nota ID texto — añadir o cambiar una nota\n"
        "/pausar ID — pausar una recurrencia\n"
        "/proximos — ver los próximos recordatorios\n"
        "/reactivar ID — reactivar una recurrencia pausada\n"
        "/recordar — crear recordatorio\n"
        "/semana — ver los próximos siete días\n\n"
        "Si no indicas hora, usaré las 09:00 (Europe/Madrid)."
    )


@router.message(Command("recordar"))
async def create_from_command(message: Message, db, settings):
    raw = (message.text or "").partition(" ")[2]
    await create_reminder(message, raw, db, settings)



@router.message(F.text, lambda message: message.text is not None and is_standalone_url(message.text.strip()))
async def preview_url(message: Message, settings):
    url = extract_url(message.text.strip()) or message.text.strip()
    try:
        metadata = await asyncio.to_thread(extract_metadata, url)
    except ValueError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}")
        return

    title = metadata.get("title") or url
    author = metadata.get("author")
    description = title if not author else f"{title} — {author}"
    draft = {
        "url": url,
        "title": title,
        "author": author,
        "item_type": metadata.get("item_type"),
        "description": description,
    }

    raw_date = metadata.get("release_date")
    due_at = None
    if raw_date:
        tz = ZoneInfo(settings.timezone)
        now = datetime.now(tz)
        parsed = dateparser.parse(
            raw_date,
            languages=["es", "en"],
            settings={
                "TIMEZONE": settings.timezone,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "RELATIVE_BASE": now,
                "PREFER_DATES_FROM": "future",
            },
        )
        if parsed is not None:
            parsed = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
            has_clock = bool(re.search(r"(?:T|\s)\d{1,2}:\d{2}", raw_date))
            if not has_clock:
                parsed = parsed.replace(hour=10, minute=0, second=0, microsecond=0)
            if parsed > now:
                due_at = parsed

    if due_at is None:
        key = (message.from_user.id, message.chat.id)
        _url_waiting_date[key] = draft
        await message.answer(
            f"🔎 He encontrado <b>{escape(title)}</b>, pero no una fecha futura fiable.\n"
            "Envíame la fecha (por ejemplo, <code>27/01/2027</code> o "
            "<code>27/01/2027 10:00</code>). Usa /salir para cancelar."
        )
        return

    await send_url_confirmation(message, draft, due_at, settings)


async def send_url_confirmation(message: Message, draft: dict, due_at: datetime, settings):
    token = secrets.token_hex(5)
    draft.update({"user_id": message.from_user.id, "chat_id": message.chat.id, "due_at": due_at})
    _url_drafts[token] = draft
    lines = ["🤖 <b>He encontrado este lanzamiento</b>", "", f"<b>{escape(draft['title'])}</b>"]
    if draft.get("author"):
        lines.append(f"Autor: {escape(draft['author'])}")
    if draft.get("item_type"):
        lines.append(f"Tipo: {escape(str(draft['item_type']))}")
    lines.extend([
        f"Fecha detectada: {format_dt(due_at)}",
        "¿Quieres que te lo recuerde ese día?",
        f'<a href="{escape(draft["url"], quote=True)}">Abrir enlace</a>',
    ])
    await message.answer(
        "\n".join(lines),
        reply_markup=url_confirmation_keyboard(token),
        disable_web_page_preview=True,
    )


@router.message(
    F.text,
    lambda message: message.from_user is not None
    and (message.from_user.id, message.chat.id) in _url_waiting_date,
)
async def receive_url_date(message: Message, settings):
    key = (message.from_user.id, message.chat.id)
    draft = _url_waiting_date.get(key)
    if draft is None:
        return
    text = (message.text or "").strip()
    if text.lower() in ("/salir", "/cancelar"):
        _url_waiting_date.pop(key, None)
        await message.answer("Creación desde el enlace cancelada.")
        return

    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    parsed = dateparser.parse(
        text,
        languages=["es", "en"],
        settings={
            "TIMEZONE": settings.timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now,
            "PREFER_DATES_FROM": "future",
        },
    )
    if parsed is None:
        await message.answer("No he entendido la fecha. Prueba <code>27/01/2027 10:00</code>.")
        return
    parsed = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
    has_clock = bool(re.search(r"\d{1,2}[:.]\d{2}|\ba\s+las?\s+\d{1,2}\b", text, re.IGNORECASE))
    if not has_clock:
        parsed = parsed.replace(hour=9, minute=0, second=0, microsecond=0)
    if parsed <= now:
        await message.answer("Esa fecha ya ha pasado. Envíame una fecha futura.")
        return

    _url_waiting_date.pop(key, None)
    await send_url_confirmation(message, draft, parsed, settings)


@router.callback_query(F.data.startswith("url:yes:"))
async def callback_url_confirm(callback: CallbackQuery, db):
    token = callback.data.rsplit(":", 1)[1]
    draft = _url_drafts.get(token)
    if (
        draft is None
        or draft["user_id"] != callback.from_user.id
        or callback.message is None
        or draft["chat_id"] != callback.message.chat.id
    ):
        await callback.answer("Esta propuesta ha caducado.", show_alert=True)
        return

    reminder_id = await db.create_reminder(
        callback.from_user.id,
        draft["chat_id"],
        draft["description"],
        draft["due_at"],
        None,
        draft["url"],
    )
    _url_drafts.pop(token, None)
    await callback.answer("Recordatorio creado.")
    await callback.message.edit_text(
        f"✅ <b>Recordatorio #{reminder_id} creado</b>\n\n"
        f"📝 {escape(draft['description'])}\n"
        f"🗓 {format_dt(draft['due_at'])}\n"
        f'<a href="{escape(draft["url"], quote=True)}">Abrir enlace</a>',
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("url:no:"))
async def callback_url_dismiss(callback: CallbackQuery):
    token = callback.data.rsplit(":", 1)[1]
    draft = _url_drafts.get(token)
    if draft is None or draft["user_id"] != callback.from_user.id:
        await callback.answer("Esta propuesta ha caducado.", show_alert=True)
        return
    _url_drafts.pop(token, None)
    await callback.answer("No se creó el recordatorio.")
    if callback.message:
        await callback.message.edit_text("Propuesta descartada.")

@router.message(Command("lista"))
async def list_reminders(message: Message, db, settings):
    parts = (message.text or "").split(maxsplit=1)
    requested_tag = normalize_tag(parts[1]) if len(parts) == 2 else None
    if len(parts) == 2 and not requested_tag:
        await message.answer("Uso: /lista #etiqueta")
        return

    rows = await db.list_reminders(message.from_user.id)
    if requested_tag:
        rows = [row for row in rows if requested_tag in stored_tags(row["tags"])]
    if not rows:
        await message.answer(
            f"No tienes recordatorios activos con #{requested_tag}."
            if requested_tag
            else "No tienes recordatorios activos. ¡Todo al día! 👌"
        )
        return

    now = datetime.now(ZoneInfo(settings.timezone))
    overdue = []
    notified = []
    paused = []
    by_date = {}

    for row in rows:
        due = local_dt(row["due_at"], settings.timezone)
        if row["status"] == "paused":
            paused.append(row)
        elif row["status"] == "notified":
            notified.append(row)
        elif due < now:
            overdue.append(row)
        else:
            by_date.setdefault(due.date(), []).append(row)

    lines = ["<b>📋 Tus recordatorios activos</b>"]
    sections = [
        ("⚠️ Vencidos", overdue),
        ("🔔 Avisados", notified),
    ]
    for heading, section_rows in sections:
        if section_rows:
            lines.append(f"\n<b>{heading}</b>")
            lines.extend(format_reminder_block(row, settings.timezone) for row in section_rows)

    today = now.date()
    for day in sorted(by_date):
        if day == today:
            heading = "📅 Hoy"
        elif (day - today).days == 1:
            heading = "📅 Mañana"
        else:
            heading = f"📅 {day:%d/%m/%Y}"
        lines.append(f"\n<b>{heading}</b>")
        lines.extend(format_reminder_block(row, settings.timezone) for row in by_date[day])

    if paused:
        lines.append("\n<b>⏸ Pausados</b>")
        lines.extend(format_reminder_block(row, settings.timezone) for row in paused)

    await message.answer("\n".join(lines), disable_web_page_preview=True)


@router.message(Command("hoy"))
async def today_reminders(message: Message, db, settings):
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = await db.list_reminders(
        message.from_user.id,
        start,
        start + timedelta(days=1),
    )
    if not rows:
        await message.answer("No tienes recordatorios previstos para hoy. 👌")
        return

    lines = ["<b>📅 Recordatorios de hoy</b>"]
    for row in rows:
        due = local_dt(row["due_at"], settings.timezone)
        lines.append(
            f"\n<b>#{row['id']}</b> · {due:%H:%M}\n"
            f"{escape(row['description'])}"
        )
        tags = stored_tags(row["tags"])
        if tags:
            lines.append(f"\n🏷 {escape(display_tags(tags))}")
        if row["note"]:
            lines.append(f"\n🗒 {escape(row['note'])}")
        if row["url"]:
            lines.append(f'\n🔗 <a href="{escape(row["url"], quote=True)}">Abrir enlace</a>')
    await message.answer("\n".join(lines))


@router.message(Command("semana"))
async def week_reminders(message: Message, db, settings):
    now = datetime.now(ZoneInfo(settings.timezone))
    rows = await db.list_reminders(
        message.from_user.id,
        now,
        now + timedelta(days=7),
    )
    rows = [row for row in rows if row["status"] != "paused"]
    if not rows:
        await message.answer("No tienes recordatorios previstos en los próximos siete días.")
        return

    lines = ["<b>📅 Próximos siete días</b>"]
    for row in rows:
        lines.append("\n" + format_reminder_block(row, settings.timezone))
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@router.message(Command("proximos"))
async def upcoming_reminders(message: Message, db, settings):
    now = datetime.now(ZoneInfo(settings.timezone))
    rows = await db.list_reminders(message.from_user.id)
    upcoming = [
        row for row in rows
        if row["status"] in ("pending", "notified")
        and local_dt(row["due_at"], settings.timezone) >= now
    ][:10]
    if not upcoming:
        await message.answer("No tienes próximos recordatorios pendientes.")
        return

    lines = ["<b>⏭ Tus próximos recordatorios</b>"]
    for row in upcoming:
        lines.append("\n" + format_reminder_block(row, settings.timezone))
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@router.message(Command("cancelar"))
async def cancel_reminder(message: Message, db):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(
            "Uso: /cancelar ID. Puedes consultar los identificadores con /lista."
        )
        return

    ok = await db.cancel_reminder(int(parts[1]), message.from_user.id)
    await message.answer(
        "🗑 Recordatorio cancelado."
        if ok
        else "No encuentro un recordatorio activo con ese ID."
    )


@router.message(Command("pausar"))
async def pause_recurrence(message: Message, db):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Uso: /pausar ID")
        return

    ok = await db.pause_recurrence(int(parts[1]), message.from_user.id)
    await message.answer(
        "⏸ Recurrencia pausada. La fecha actual se conserva."
        if ok
        else "No encuentro una recurrencia activa con ese ID."
    )


@router.message(Command("reactivar"))
async def resume_recurrence(message: Message, db):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Uso: /reactivar ID")
        return

    ok = await db.resume_recurrence(int(parts[1]), message.from_user.id)
    await message.answer(
        "▶️ Recurrencia reactivada."
        if ok
        else "No encuentro una recurrencia pausada con ese ID."
    )


@router.message(Command("frecuencia"))
async def change_recurrence(message: Message, db):
    parts = (message.text or "").split()
    periods = {
        "diaria": "daily",
        "diario": "daily",
        "daily": "daily",
        "semanal": "weekly",
        "weekly": "weekly",
        "mensual": "monthly",
        "monthly": "monthly",
    }
    if len(parts) != 3 or not parts[1].isdigit() or parts[2].lower() not in periods:
        await message.answer("Uso: /frecuencia ID diaria|semanal|mensual")
        return

    recurrence = periods[parts[2].lower()]
    ok = await db.change_recurrence(
        int(parts[1]), message.from_user.id, recurrence
    )
    label = {"daily": "diaria", "weekly": "semanal", "monthly": "mensual"}[recurrence]
    await message.answer(
        f"🔁 Periodicidad cambiada a {label}. Se aplicará tras completar la ocurrencia actual."
        if ok
        else "No encuentro una recurrencia activa con ese ID."
    )


@router.message(Command("nota"))
async def set_reminder_note(message: Message, db):
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].strip():
        await message.answer(
            "Uso: /nota ID texto. Para quitarla: /nota ID borrar."
        )
        return

    note_text = parts[2].strip()
    note = None if note_text.lower() in ("borrar", "quitar", "eliminar") else note_text
    if note is not None and len(note) > 1000:
        await message.answer("La nota no puede superar los 1000 caracteres.")
        return
    ok = await db.set_reminder_note(
        int(parts[1]), message.from_user.id, note
    )
    if not ok:
        await message.answer("No encuentro un recordatorio activo con ese ID.")
    elif note is None:
        await message.answer("🗒 Nota eliminada.")
    else:
        await message.answer("🗒 Nota guardada.")


@router.message(Command("etiqueta"))
async def set_reminder_tags(message: Message, db):
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer("Uso: /etiqueta ID #NAS #Casa")
        return

    clear_tags = parts[2].strip().lower() in ("borrar", "quitar", "eliminar")
    tags = [] if clear_tags else extract_tags(parts[2])
    if not tags and not clear_tags:
        await message.answer("Indica al menos una etiqueta, por ejemplo #NAS.")
        return
    ok = await db.set_reminder_tags(
        int(parts[1]), message.from_user.id, tags
    )
    if not ok:
        await message.answer("No encuentro un recordatorio activo con ese ID.")
    elif clear_tags:
        await message.answer("🏷 Etiquetas eliminadas.")
    else:
        await message.answer(f"🏷 Etiquetas guardadas: {escape(display_tags(tags))}")


@router.message(Command("historial"))
async def reminder_history(message: Message, db, settings):
    parts = (message.text or "").split()
    if len(parts) > 2 or (len(parts) == 2 and not parts[1].isdigit()):
        await message.answer("Uso: /historial [límite de 1 a 20]")
        return
    limit = int(parts[1]) if len(parts) == 2 else 15
    if not 1 <= limit <= 20:
        await message.answer("El límite debe estar entre 1 y 20.")
        return

    rows = await db.list_history(message.from_user.id, limit)
    if not rows:
        await message.answer("Todavía no hay actividad en el historial.")
        return

    labels = {
        "created": "🆕 Creado",
        "completed": "✅ Completado",
        "postponed": "⏰ Pospuesto",
        "cancelled": "🗑 Cancelado",
    }
    lines = ["<b>🕘 Historial reciente</b>"]
    for row in rows:
        happened = format_dt(local_dt(row["occurred_at"], settings.timezone))
        description = escape(row["description"][:180])
        line = (
            f"<b>#{row['reminder_id']} · {labels.get(row['event_type'], row['event_type'])}</b>"
            f" · {happened}\n{description}"
        )
        if row["event_type"] == "postponed" and row["due_at"] and row["new_due_at"]:
            old_due = format_dt(local_dt(row["due_at"], settings.timezone))
            new_due = format_dt(local_dt(row["new_due_at"], settings.timezone))
            line += f"\n{old_due} → {new_due}"
        elif row["due_at"]:
            due = format_dt(local_dt(row["due_at"], settings.timezone))
            line += f"\nFecha del recordatorio: {due}"
        lines.append(line)
    await message.answer("\n\n".join(lines), disable_web_page_preview=True)


@router.message(Command("estadisticas"))
async def reminder_statistics(message: Message, db):
    parts = (message.text or "").split()
    if len(parts) > 2 or (len(parts) == 2 and not parts[1].isdigit()):
        await message.answer("Uso: /estadisticas [días, de 1 a 365]")
        return
    days = int(parts[1]) if len(parts) == 2 else 30
    if not 1 <= days <= 365:
        await message.answer("El periodo debe estar entre 1 y 365 días.")
        return

    since = datetime.now(timezone.utc) - timedelta(days=days)
    totals = await db.history_statistics(message.from_user.id, since)
    if not totals:
        await message.answer(f"No hay actividad registrada en los últimos {days} días.")
        return
    lines = [
        f"<b>📊 Actividad de los últimos {days} días</b>",
        f"🆕 Creados: {totals.get('created', 0)}",
        f"✅ Completados: {totals.get('completed', 0)}",
        f"⏰ Pospuestos: {totals.get('postponed', 0)}",
        f"🗑 Cancelados: {totals.get('cancelled', 0)}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("estado"))
async def status(message: Message, db, settings):
    count = await db.count_active()
    retry_text = (
        "desactivados"
        if settings.reminder_max_retries == 0
        else f"cada {settings.reminder_repeat_minutes} min, máximo {settings.reminder_max_retries} reintentos"
    )
    await message.answer(
        f"<b>🟢 MiBot está funcionando</b>\nRecordatorios activos: {count}"
        f"\nAvisos sin respuesta: {retry_text}."
    )


async def create_reminder(message: Message, raw: str, db, settings):
    note_match = re.search(r"(?:^|\s)nota\s*:\s*(.+)$", raw, re.IGNORECASE)
    note = note_match.group(1).strip() if note_match else None
    reminder_text = raw[:note_match.start()].strip() if note_match else raw
    url = extract_url(reminder_text)
    tag_source = reminder_text.replace(url, " ") if url else reminder_text
    tags = extract_tags(tag_source)
    parser_text = TAG_RE.sub(" ", tag_source)
    try:
        description, due_at, recurrence = parse_reminder(parser_text, settings.timezone)
        reminder_id = await db.create_reminder(
            message.from_user.id,
            message.chat.id,
            description,
            due_at,
            recurrence,
            url,
            note,
            tags,
        )
        recurrence_text = {
            "daily": " · Se repetirá cada día",
            "weekly": " · Se repetirá cada semana",
            "monthly": " · Se repetirá cada mes",
        }.get(recurrence, "")
        await message.answer(
            f"✅ <b>Recordatorio #{reminder_id} creado</b>\n\n"
            f"📝 {escape(description)}\n"
            f"🗓 {format_dt(due_at)}{recurrence_text}"
            + (f"\n🏷 {escape(display_tags(tags))}" if tags else "")
            + (f"\n🗒 {escape(note)}" if note else "")
            + (f'\n🔗 <a href="{escape(url, quote=True)}">Abrir enlace</a>' if url else "")
        )
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")


@router.message(
    F.text,
    lambda message: (
        message.from_user is not None
        and (message.from_user.id, message.chat.id) in _custom_requests
    ),
)
async def receive_custom_delay(message: Message, db, settings):
    key = (message.from_user.id, message.chat.id)
    request = _custom_requests.get(key)
    if request is None:
        return

    reminder_id, original_message_id, original_text = request
    row = await db.get_reminder(reminder_id, message.from_user.id)
    if (
        not row
        or row["status"] != "notified"
        or (
            row["active_message_id"] is not None
            and row["active_message_id"] != original_message_id
        )
    ):
        _custom_requests.pop(key, None)
        await message.answer("Este aviso ya no está activo. Consulta /lista.")
        return
    typed = (message.text or "").strip()

    if typed.lower() == "/salir":
        _custom_requests.pop(key, None)
        row = await db.get_reminder(reminder_id, message.from_user.id)
        markup = (
            reminder_keyboard(reminder_id)
            if row and row["status"] == "notified"
            else None
        )
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=original_message_id,
            text=original_text,
            reply_markup=markup,
        )
        await message.answer("Personalización cancelada.")
        return

    delay = parse_custom_delay(typed)
    if delay is None:
        await message.answer(
            "Escribe un intervalo como «45 minutos», «3 horas» o «2 días». "
            "Usa /salir para volver."
        )
        return

    tz = ZoneInfo(settings.timezone)
    due_at = datetime.now(tz) + delay
    ok = await db.postpone_reminder(reminder_id, message.from_user.id, due_at)
    _custom_requests.pop(key, None)

    if not ok:
        await message.answer("Este aviso ya no está activo.")
        return

    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=original_message_id,
        text=(
            f"{original_text}\n\n"
            f"⏰ Pospuesto hasta {format_dt(due_at)}."
        ),
        reply_markup=None,
    )


@router.message(F.text)
async def create_from_text(message: Message, db, settings):
    text = (message.text or "").strip()
    if text.startswith("/"):
        return
    if not text.lower().startswith(("recuérdame", "recuerdame", "recordar ")):
        await message.answer(
            "No he reconocido ese mensaje. Escribe /help para ver ejemplos."
        )
        return
    await create_reminder(message, text, db, settings)


@router.callback_query(F.data.startswith("done:"))
async def callback_done(callback: CallbackQuery, db, settings):
    reminder_id = int(callback.data.split(":", 1)[1])
    row = await db.get_reminder(reminder_id, callback.from_user.id)
    if not is_current_alert(callback, row):
        await callback.answer("Este aviso ya no está activo.")
        return
    ok = await db.complete_reminder(
        reminder_id,
        callback.from_user.id,
        settings.timezone,
    )
    await callback.answer("Marcado como hecho" if ok else "Este aviso ya no está activo.")
    if not ok or not callback.message:
        return

    row = await db.get_reminder(reminder_id, callback.from_user.id)
    if row and row["recurrence"]:
        next_due = format_dt(local_dt(row["due_at"], settings.timezone))
        note = f"✅ Ocurrencia completada. Próximo aviso: {next_due}."
    else:
        note = "✅ Recordatorio completado."
    await append_to_original(callback, note)


@router.callback_query(F.data.startswith("postpone:"))
async def callback_postpone(callback: CallbackQuery, db):
    reminder_id = int(callback.data.split(":", 1)[1])
    row = await db.get_reminder(reminder_id, callback.from_user.id)
    if not is_current_alert(callback, row):
        await callback.answer("Este aviso ya no está activo.")
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=postpone_keyboard(reminder_id)
        )


@router.callback_query(F.data.startswith("snooze:"))
async def callback_snooze(callback: CallbackQuery, db, settings):
    _, raw_id, choice = callback.data.split(":", 2)
    reminder_id = int(raw_id)
    row = await db.get_reminder(reminder_id, callback.from_user.id)
    if not is_current_alert(callback, row):
        await callback.answer("Este aviso ya no está activo.")
        return
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)

    delays = {
        "10m": timedelta(minutes=10),
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "2h": timedelta(hours=2),
        "tomorrow": timedelta(days=1),
    }
    delay = delays.get(choice)
    if delay is None:
        await callback.answer("Opción no válida.")
        return

    await set_postponed(
        callback,
        db,
        settings,
        reminder_id,
        now + delay,
        "Recordatorio pospuesto.",
    )


@router.callback_query(F.data.startswith("later:"))
async def callback_legacy_later(callback: CallbackQuery, db, settings):
    # Mantiene funcionando mensajes enviados con el teclado antiguo.
    reminder_id = int(callback.data.split(":", 1)[1])
    row = await db.get_reminder(reminder_id, callback.from_user.id)
    if not is_current_alert(callback, row):
        await callback.answer("Este aviso ya no está activo.")
        return
    due_at = datetime.now(ZoneInfo(settings.timezone)) + timedelta(hours=1)
    await set_postponed(
        callback,
        db,
        settings,
        reminder_id,
        due_at,
        "Pospuesto una hora.",
    )


@router.callback_query(F.data.startswith("custom:"))
async def callback_custom(callback: CallbackQuery, db):
    reminder_id = int(callback.data.split(":", 1)[1])
    row = await db.get_reminder(reminder_id, callback.from_user.id)
    if not is_current_alert(callback, row):
        await callback.answer("Este aviso ya no está activo.")
        return

    key = (callback.from_user.id, callback.message.chat.id)
    original_text = callback.message.text or ""
    _custom_requests[key] = (
        reminder_id,
        callback.message.message_id,
        original_text,
    )

    await callback.answer()
    await callback.message.edit_text(
        f"{original_text}\n\n"
        "✏️ Escribe el intervalo, por ejemplo «45 minutos» o «2 horas». "
        "Usa /salir para volver.",
        reply_markup=None,
    )


@router.callback_query(F.data.startswith("back:"))
async def callback_back(callback: CallbackQuery, db):
    reminder_id = int(callback.data.split(":", 1)[1])
    row = await db.get_reminder(reminder_id, callback.from_user.id)
    if not is_current_alert(callback, row):
        await callback.answer("Este aviso ya no está activo.")
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=reminder_keyboard(reminder_id)
        )


@router.callback_query(F.data.startswith("cancel:"))
async def callback_cancel(callback: CallbackQuery, db):
    reminder_id = int(callback.data.split(":", 1)[1])
    row = await db.get_reminder(reminder_id, callback.from_user.id)
    if not is_current_alert(callback, row):
        await callback.answer("Este aviso ya no está activo.")
        return
    ok = await db.cancel_notified_reminder(
        reminder_id,
        callback.from_user.id,
    )
    await callback.answer(
        "Recordatorio cancelado." if ok else "Este aviso ya no está activo."
    )
    if ok:
        await append_to_original(callback, "✖ Recordatorio cancelado.")
