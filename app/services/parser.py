import re

from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from zoneinfo import ZoneInfo

import dateparser
from dateparser.search import search_dates


RECURRENCE_PATTERNS = [
    (r"\bcada\s+d[ií]a\b", "daily"),
    (r"\bcada\s+semana\b", "weekly"),
    (r"\bcada\s+mes\b", "monthly"),
]


NUMBER_WORDS = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "quince": 15,
    "veinte": 20,
    "treinta": 30,
}


RELATIVE_PATTERN = re.compile(
    r"\b(?:dentro\s+de|en)\s+"
    r"(\d+|un|uno|una|dos|tres|cuatro|cinco|seis|siete|"
    r"ocho|nueve|diez|once|doce|quince|veinte|treinta)"
    r"\s+"
    r"(minutos?|mins?|horas?|d[ií]as?|semanas?|mes(?:es)?)\b",
    re.IGNORECASE,
)


def remove_phrase(text: str, phrase: str) -> str:
    """Elimina una expresión encontrada, conservando el resto del texto."""
    return re.sub(re.escape(phrase), " ", text, count=1, flags=re.IGNORECASE)


def clean_description(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.-;:")


def relative_datetime(match, now: datetime) -> datetime:
    amount_text = match.group(1).lower()
    unit = match.group(2).lower()

    amount = (
        int(amount_text)
        if amount_text.isdigit()
        else NUMBER_WORDS[amount_text]
    )

    if unit.startswith(("min",)):
        return now + timedelta(minutes=amount)

    if unit.startswith("hora"):
        return now + timedelta(hours=amount)

    if unit.startswith("d"):
        return now + timedelta(days=amount)

    if unit.startswith("semana"):
        return now + timedelta(weeks=amount)

    if unit.startswith("mes"):
        return now + relativedelta(months=amount)

    raise ValueError("No he podido interpretar el intervalo indicado.")


def parse_reminder(raw_text: str, timezone_name: str):
    text = raw_text.strip()

    text = re.sub(
        r"^/?recordar\b\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"^recu[eé]rdame\b\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    if not text:
        raise ValueError("Escribe qué quieres que recuerde y cuándo.")

    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)

    recurrence = None

    for pattern, value in RECURRENCE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            recurrence = value
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
            break

    # 1. Primero procesamos intervalos relativos exactos.
    # Ejemplos: dentro de un minuto, en 2 horas, dentro de 3 días.

    relative_match = RELATIVE_PATTERN.search(text)

    if relative_match:
        parsed = relative_datetime(relative_match, now)
        description = clean_description(
            text[:relative_match.start()] + " " + text[relative_match.end():]
        )

        if not description:
            raise ValueError(
                "No he encontrado qué necesitas recordar. "
                "Escribe primero la descripción."
            )

        return description, parsed, recurrence

    # 2. Si no hay intervalo exacto, utilizamos dateparser
    # para interpretar fechas naturales en español.

    matches = search_dates(
        text,
        languages=["es"],
        settings={
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now,
            "PREFER_DATES_FROM": "future",
            "PARSERS": [
                "relative-time",
                "absolute-time",
                "custom-formats",
                "timestamp",
            ],
        },
    )

    # 3. Si es una recurrencia sin fecha inicial,
    # programamos su primera ejecución.

    if not matches and recurrence:
        if recurrence == "daily":
            parsed = (now + timedelta(days=1)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )
        elif recurrence == "weekly":
            parsed = (now + timedelta(weeks=1)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )
        else:
            parsed = (now + relativedelta(months=1)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )

        description = clean_description(text)

        if not description:
            raise ValueError("Escribe qué quieres que recuerde.")

        return description, parsed, recurrence

    if not matches:
        raise ValueError(
            "No he entendido la fecha. Prueba, por ejemplo: "
            "comprar leche mañana a las 18:30."
        )

    # 4. Elegimos la expresión temporal más completa.

    matched_text, parsed = max(matches, key=lambda item: len(item[0]))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)

    # Si no se ha especificado hora, usamos las 09:00.

    has_clock = bool(
        re.search(
            r"\b\d{1,2}[:.]\d{2}\b"
            r"|\ba\s+las?\s+\d{1,2}\b"
            r"|\bmediod[ií]a\b"
            r"|\bmedianoche\b",
            matched_text,
            re.IGNORECASE,
        )
    )

    if not has_clock:
        parsed = parsed.replace(
            hour=9,
            minute=0,
            second=0,
            microsecond=0,
        )

    description = clean_description(
        remove_phrase(text, matched_text)
    )

    if not description:
        raise ValueError(
            "No he encontrado el texto del recordatorio. "
            "Escribe primero qué necesitas recordar."
        )

    if parsed <= now:
        raise ValueError(
            "La fecha indicada ya ha pasado. "
            "Comprueba el día y la hora."
        )

    return description, parsed, recurrence
