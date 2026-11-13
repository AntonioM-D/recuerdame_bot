import hmac
import json
from html import escape
import logging
from aiohttp import web

log = logging.getLogger(__name__)


def create_app(bot, settings):
    app = web.Application(client_max_size=16 * 1024)

    async def health(_request):
        return web.json_response({"status": "ok", "service": "mibot"})

    async def notify(request):
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {settings.alert_api_token}"
        if not hmac.compare_digest(auth, expected):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid_json"}, status=400)
        title = str(payload.get("title", "")).strip()[:120]
        message = str(payload.get("message", "")).strip()[:4000]
        severity = str(payload.get("severity", "info")).lower()
        if not title or not message:
            return web.json_response({"error": "title_and_message_required"}, status=400)
        if severity not in {"info", "warning", "critical"}:
            return web.json_response({"error": "invalid_severity"}, status=400)
        icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}[severity]
        text = f"{icon} <b>{escape(title)}</b>\n\n{escape(message)}"
        sent = 0
        for user_id in settings.allowed_user_ids:
            try:
                await bot.send_message(user_id, text)
                sent += 1
            except Exception:
                log.exception("No se pudo entregar una alerta a Telegram ID %s", user_id)
        return web.json_response({"accepted": True, "sent": sent}, status=202)

    app.router.add_get("/health", health)
    app.router.add_post("/notify", notify)
    return app
