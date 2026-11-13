from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def reminder_keyboard(reminder_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Hecho",
                    callback_data=f"done:{reminder_id}",
                ),
                InlineKeyboardButton(
                    text="⏰ Posponer",
                    callback_data=f"postpone:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✖ Cancelar",
                    callback_data=f"cancel:{reminder_id}",
                ),
            ],
        ]
    )


def url_confirmation_keyboard(token: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Crear recordatorio",
                    callback_data=f"url:yes:{token}",
                ),
                InlineKeyboardButton(
                    text="Ahora no",
                    callback_data=f"url:no:{token}",
                ),
            ]
        ]
    )


def postpone_keyboard(reminder_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="10 min",
                    callback_data=f"snooze:{reminder_id}:10m",
                ),
                InlineKeyboardButton(
                    text="30 min",
                    callback_data=f"snooze:{reminder_id}:30m",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="1 hora",
                    callback_data=f"snooze:{reminder_id}:1h",
                ),
                InlineKeyboardButton(
                    text="2 horas",
                    callback_data=f"snooze:{reminder_id}:2h",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Mañana",
                    callback_data=f"snooze:{reminder_id}:tomorrow",
                ),
                InlineKeyboardButton(
                    text="Personalizar",
                    callback_data=f"custom:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩ Volver",
                    callback_data=f"back:{reminder_id}",
                ),
            ],
        ]
    )


# Menú interactivo de Recuérdame Bot. Los botones de consulta ejecutan la consulta;
# las opciones que requieren argumentos muestran ejemplos antes de continuar.
def menu_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 Crear", callback_data="menu:section:create"),
                InlineKeyboardButton(text="📋 Consultar", callback_data="menu:section:queries"),
            ],
            [
                InlineKeyboardButton(text="🔁 Recurrencias", callback_data="menu:section:recurrence"),
                InlineKeyboardButton(text="🏷 Organizar", callback_data="menu:section:organize"),
            ],
            [
                InlineKeyboardButton(text="📊 Historial", callback_data="menu:section:history"),
                InlineKeyboardButton(text="ℹ️ Ayuda", callback_data="menu:section:help"),
            ],
            [InlineKeyboardButton(text="✖ Cerrar", callback_data="menu:close")],
        ]
    )


def menu_page_keyboard(section: str):
    pages = {
        "create": [
            [("✍️ Crear recordatorio", "menu:detail:create"), ("🔗 Usar un enlace", "menu:detail:url")],
        ],
        "queries": [
            [("📅 Hoy", "menu:run:today"), ("🗓 Semana", "menu:run:week")],
            [("⏭ Próximos", "menu:run:upcoming"), ("📋 Lista", "menu:run:list")],
        ],
        "recurrence": [
            [("⏸ Pausar", "menu:detail:pause"), ("▶️ Reactivar", "menu:detail:resume")],
            [("🔄 Cambiar frecuencia", "menu:detail:frequency"), ("✅ Completar", "menu:detail:complete")],
            [("🛑 Cancelar serie", "menu:detail:series_cancel")],
        ],
        "organize": [
            [("🏷 Etiquetas", "menu:detail:tags"), ("🗒 Notas", "menu:detail:notes")],
            [("✖ Cancelar tarea", "menu:detail:cancel")],
        ],
        "history": [
            [("🕘 Ver historial", "menu:run:history"), ("📈 Estadísticas", "menu:run:stats")],
        ],
        "help": [
            [("💡 Cómo crear", "menu:detail:create"), ("⌨️ Comandos", "menu:detail:commands")],
            [("🕰 Fecha y hora", "menu:detail:time")],
        ],
    }
    rows = [
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in pages.get(section, [])
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Menú principal", callback_data="menu:home")])
    rows.append([InlineKeyboardButton(text="✖ Cerrar", callback_data="menu:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
