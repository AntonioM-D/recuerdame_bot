# Historial de cambios

## v1.1.0

- Mejoras en el reconocimiento de fechas
- Interpretación de fechas numéricas (DD/MM/YYYY y DD-MM-YYYY).
- Reconocimiento de fechas escritas en español.
- Mejoras en expresiones como «el próximo lunes» o «lunes que viene».
- Soporte para horas concretas.
- Conservación del reconocimiento de intervalos relativos y recurrencias.
- Gestión de recordatorios
- Mejora de la cancelación múltiple de recordatorios.
- Posibilidad de cancelar varios identificadores en una misma operación.
/cancelar admite ahora varios identificadores separados por comas.
/cancelar 22, 21, 20

✅ Recordatorio #22 cancelado
✅ Recordatorio #21 cancelado
❌ Recordatorio #20 no encontrado

- Actualizados el aviso de arranque y la versión que muestra el bot a `1.1.0`.

## v1.0

- Primera versión documentada para publicar el proyecto.
- Recordatorios en español con recurrencias, etiquetas, notas y enlaces.
- Menú interactivo y comandos de consulta, historial y estadísticas.
- Avisos persistentes configurables y API HTTP local para alertas.
- Aviso de arranque con versión y hora.
