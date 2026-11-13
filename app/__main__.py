import asyncio
import logging
from zoneinfo import ZoneInfo
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.types import BotCommand, MenuButtonCommands

from .api import create_app
from .config import load_settings
from .db import Database
from .handlers import router
from .services.scheduler import scheduler_loop


BOT_VERSION = "1.0"


BOT_COMMANDS = [
    BotCommand(command="menu", description="Abrir el menú interactivo"),
    BotCommand(command="cancelar", description="Cancelar un recordatorio"),
    BotCommand(command="estadisticas", description="Ver actividad por periodo"),
    BotCommand(command="estado", description="Ver el estado del bot"),
    BotCommand(command="etiqueta", description="Asignar o quitar etiquetas"),
    BotCommand(command="frecuencia", description="Cambiar una recurrencia"),
    BotCommand(command="help", description="Ver ayuda y ejemplos"),
    BotCommand(command="historial", description="Consultar acciones recientes"),
    BotCommand(command="hoy", description="Ver los recordatorios de hoy"),
    BotCommand(command="lista", description="Ver pendientes o filtrar por etiqueta"),
    BotCommand(command="nota", description="Añadir o cambiar una nota"),
    BotCommand(command="pausar", description="Pausar una recurrencia"),
    BotCommand(command="proximos", description="Ver los próximos recordatorios"),
    BotCommand(command="reactivar", description="Reactivar una recurrencia"),
    BotCommand(command="recordar", description="Crear un recordatorio"),
    BotCommand(command="salir", description="Salir de una acción en curso"),
    BotCommand(command="semana", description="Ver los próximos siete días"),
]


async def notify_startup(bot, settings):
    logger = logging.getLogger(__name__)
    try:
        await bot.get_me()
        started_at = datetime.now(ZoneInfo(settings.timezone)).strftime("%d/%m/%Y %H:%M")
    except Exception:
        logger.exception("No se pudo preparar el aviso de arranque")
        return

    chat_ids = settings.allowed_user_ids
    if not chat_ids:
        logger.warning("No se envió el aviso de arranque: no hay usuarios autorizados configurados")
        return

    text = (
        "🟢 <b>Recuérdame Bot activo</b>\n"
        f"⚙️ Versión: v{BOT_VERSION}\n"
        f"🕒 Inicio: {started_at} ({settings.timezone})"
    )
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logger.exception("No se pudo enviar el aviso de arranque a un usuario autorizado")


async def main():
    settings = load_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(settings.db_path)
    await db.initialize()

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    try:
        await bot.set_my_commands(BOT_COMMANDS)
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception:
        logging.getLogger(__name__).exception(
            'No se pudo registrar el menú nativo de comandos'
        )
    dp = Dispatcher()
    dp.include_router(router)

    async def auth_middleware(handler, event, data):
        user = getattr(event, "from_user", None)

        if user is not None and user.id not in settings.allowed_user_ids:
            if hasattr(event, "answer"):
                await event.answer(
                    "⛔ No estás autorizado para utilizar este bot."
                )
            return

        data["db"] = db
        data["settings"] = settings

        return await handler(event, data)

    dp.message.middleware(auth_middleware)
    dp.callback_query.middleware(auth_middleware)

    runner = web.AppRunner(create_app(bot, settings))
    await runner.setup()

    site = web.TCPSite(
        runner,
        settings.api_host,
        settings.api_port,
    )
    await site.start()

    logging.getLogger(__name__).info(
        "API de alertas escuchando en %s:%s",
        settings.api_host,
        settings.api_port,
    )

    scheduler = asyncio.create_task(
        scheduler_loop(bot, db, settings)
    )
    await notify_startup(bot, settings)

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )

    except TelegramConflictError:
        logging.getLogger(__name__).exception(
            "Ya existe otra instancia usando este token de Telegram"
        )
        raise

    finally:
        scheduler.cancel()

        try:
            await scheduler
        except asyncio.CancelledError:
            pass

        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
