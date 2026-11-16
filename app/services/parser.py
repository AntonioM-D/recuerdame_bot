import re
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from zoneinfo import ZoneInfo


RECURRENCE_PATTERNS = [
    (r"\bcada\s+d[ií]a\b", "daily"),
    (r"\bcada\s+semana\b", "weekly"),
    (r"\bcada\s+mes\b", "monthly"),
]

NUMBER_WORDS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3,
    "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "quince": 15, "veinte": 20, "treinta": 30,
}

RELATIVE_PATTERN = re.compile(
    r"\b(?:dentro\s+de|en)\s+"
    r"(\d+|un|uno|una|dos|tres|cuatro|cinco|seis|siete|"
    r"ocho|nueve|diez|once|doce|quince|veinte|treinta)\s+"
    r"(minutos?|mins?|horas?|d[ií]as?|semanas?|mes(?:es)?)\b",
    re.IGNORECASE,
)

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
WEEKDAYS = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5,
    "domingo": 6,
}

TIME_PATTERN = re.compile(
    r"\s*(?:a\s+las?\s+)?(\d{1,2})(?::|\.)(\d{2})\s*h?\b"
    r"|\s*(?:a\s+las?\s+)(\d{1,2})\s*h?\b"
    r"|\s*(mediod[ií]a|medianoche)\b",
    re.IGNORECASE,
)


def clean_description(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.-;:")


def _extract_time(text: str):
    match = TIME_PATTERN.search(text)
    if not match:
        return None, None
    if match.group(1) is not None:
        hour, minute = int(match.group(1)), int(match.group(2))
    elif match.group(3) is not None:
        hour, minute = int(match.group(3)), 0
    elif match.group(4).lower().startswith("medio"):
        hour, minute = 12, 0
    else:
        hour, minute = 0, 0
    if hour > 23 or minute > 59:
        raise ValueError("La hora indicada no es válida.")
    return (hour, minute), match.span()


def _make_datetime(year, month, day, now, time_value):
    hour, minute = time_value if time_value else (9, 0)
    try:
        return datetime(year, month, day, hour, minute, tzinfo=now.tzinfo)
    except ValueError as exc:
        raise ValueError("La fecha indicada no es válida.") from exc


def relative_datetime(match, now: datetime) -> datetime:
    amount_text = match.group(1).lower()
    unit = match.group(2).lower()
    amount = int(amount_text) if amount_text.isdigit() else NUMBER_WORDS[amount_text]
    if unit.startswith("min"):
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


def _explicit_date(text: str, now: datetime):
    """Parsea fechas inequívocas antes de recurrir a dateparser."""
    time_value, time_span = _extract_time(text)

    # DD/MM/YYYY o DD-MM-YYYY, opcionalmente precedido por 'el'.
    numeric = re.search(
        r"\b(?:el\s+)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text,
        re.IGNORECASE,
    )
    if numeric:
        day, month, year = map(int, numeric.groups())
        parsed = _make_datetime(year, month, day, now, time_value)
        spans = [numeric.span()]
        if time_span:
            spans.append(time_span)
        return parsed, spans

    # 'el 16 de noviembre de 2026'.
    written = re.search(
        r"\b(?:el\s+)?(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})\b",
        text, re.IGNORECASE,
    )
    if written and written.group(2).lower() in MONTHS:
        day = int(written.group(1))
        month = MONTHS[written.group(2).lower()]
        year = int(written.group(3))
        parsed = _make_datetime(year, month, day, now, time_value)
        spans = [written.span()]
        if time_span:
            spans.append(time_span)
        return parsed, spans

    # 'el próximo lunes', 'próximo lunes', 'el lunes que viene'.
    weekday = re.search(
        r"\b(?:(?:el\s+)?pr[oó]xim[oa]\s+|el\s+)?"
        r"(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)"
        r"(?:\s+que\s+viene)?\b",
        text, re.IGNORECASE,
    )
    if weekday:
        name = weekday.group(1).lower()
        target = WEEKDAYS[name]
        days_ahead = (target - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        date = now.date() + timedelta(days=days_ahead)
        parsed = _make_datetime(date.year, date.month, date.day, now, time_value)
        spans = [weekday.span()]
        if time_span:
            spans.append(time_span)
        return parsed, spans

    return None


def _remove_spans(text: str, spans) -> str:
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " + text[end:]
    return clean_description(text)


def parse_reminder(raw_text: str, timezone_name: str):
    text = raw_text.strip()
    text = re.sub(r"^/?recordar\b\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^recu[eé]rdame\b\s*", "", text, flags=re.IGNORECASE)
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

    relative_match = RELATIVE_PATTERN.search(text)
    if relative_match:
        parsed = relative_datetime(relative_match, now)
        description = clean_description(text[:relative_match.start()] + " " + text[relative_match.end():])
        if not description:
            raise ValueError("No he encontrado qué necesitas recordar. Escribe primero la descripción.")
        return description, parsed, recurrence

    explicit = _explicit_date(text, now)
    if explicit:
        parsed, spans = explicit
        description = _remove_spans(text, spans)
    else:
        try:
            from dateparser.search import search_dates
            matches = search_dates(
                text,
                languages=["es"],
                settings={
                    "TIMEZONE": timezone_name,
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "RELATIVE_BASE": now,
                    "PREFER_DATES_FROM": "future",
                },
            )
        except ImportError:
            if recurrence:
                matches = None
            else:
                raise RuntimeError("Falta la dependencia dateparser para interpretar fechas naturales.")
        if not matches and recurrence:
            if recurrence == "daily":
                parsed = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            elif recurrence == "weekly":
                parsed = (now + timedelta(weeks=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            else:
                parsed = (now + relativedelta(months=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            description = clean_description(text)
            if not description:
                raise ValueError("Escribe qué quieres que recuerde.")
            return description, parsed, recurrence
        if not matches:
            raise ValueError("No he entendido la fecha. Prueba, por ejemplo: comprar leche mañana a las 18:30.")

        matched_text, parsed = max(matches, key=lambda item: len(item[0]))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        else:
            parsed = parsed.astimezone(tz)
        has_clock = bool(re.search(r"\b\d{1,2}[:.]\d{2}\b|\ba\s+las?\s+\d{1,2}\b|mediod[ií]a|medianoche", matched_text, re.IGNORECASE))
        if not has_clock:
            parsed = parsed.replace(hour=9, minute=0, second=0, microsecond=0)
        description = clean_description(text.replace(matched_text, " ", 1))

    if not description:
        raise ValueError("No he encontrado el texto del recordatorio. Escribe primero qué necesitas recordar.")
    if parsed <= now:
        raise ValueError("La fecha indicada ya ha pasado. Comprueba el día y la hora.")
    return description, parsed, recurrence
