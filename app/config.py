from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_user_ids: set[int]
    alert_api_token: str
    timezone: str
    db_path: str
    log_level: str
    api_host: str
    api_port: int
    reminder_repeat_minutes: int
    reminder_max_retries: int


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    api_token = os.getenv("ALERT_API_TOKEN", "").strip()
    raw_ids = os.getenv("ALLOWED_USER_IDS", "").strip()
    ids = {int(item.strip()) for item in raw_ids.split(",") if item.strip()}
    if not token or "REEMPLAZAR" in token:
        raise RuntimeError("Falta configurar BOT_TOKEN en el archivo .env")
    if not ids:
        raise RuntimeError("Configura al menos un ID en ALLOWED_USER_IDS")
    if not api_token or api_token.startswith("CAMBIA_"):
        raise RuntimeError("Configura ALERT_API_TOKEN con un secreto aleatorio")
    repeat_minutes = int(os.getenv("REMINDER_REPEAT_MINUTES", "30"))
    max_retries = int(os.getenv("REMINDER_MAX_RETRIES", "3"))
    if not 5 <= repeat_minutes <= 1440:
        raise RuntimeError("REMINDER_REPEAT_MINUTES debe estar entre 5 y 1440")
    if not 0 <= max_retries <= 10:
        raise RuntimeError("REMINDER_MAX_RETRIES debe estar entre 0 y 10")
    return Settings(
        bot_token=token,
        allowed_user_ids=ids,
        alert_api_token=api_token,
        timezone=os.getenv("TIMEZONE", "Europe/Madrid"),
        db_path=os.getenv("DB_PATH", "/app/data/mibot.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        api_host=os.getenv("API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("API_PORT", "8080")),
        reminder_repeat_minutes=repeat_minutes,
        reminder_max_retries=max_retries,
    )
