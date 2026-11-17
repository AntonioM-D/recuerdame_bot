# Historial de cambios

## v1.1.1

- Reorganizado el menú nativo de Telegram con los 18 comandos en un orden más intuitivo.
- Unificada la sección de comandos de `/help` con el orden del menú nativo.
- Diferenciadas las consultas: `/proximos` muestra los próximos recordatorios ordenados por fecha; `/lista [#etiqueta]` muestra los recordatorios activos y permite filtrar por etiqueta.
- Ajustes de presentación y pequeñas correcciones.
- Sin cambios en el motor de recordatorios, las recurrencias ni el esquema de la base de datos.

## v1.1.0

- Mejoras en el reconocimiento de fechas.
- Interpretación de fechas numéricas (DD/MM/YYYY y DD-MM-YYYY).
- Reconocimiento de fechas escritas en español.
- Mejoras en expresiones como «el próximo lunes» o «lunes que viene».
- Soporte para horas concretas.
- Conservación del reconocimiento de intervalos relativos y recurrencias.
- Mejora de la cancelación múltiple de recordatorios.
- Posibilidad de cancelar varios identificadores en una misma operación.
- Actualizados el aviso de arranque y la versión que muestra el bot a `1.1.0`.

## v1.0

- Primera versión documentada para publicar el proyecto.
- Recordatorios en español con recurrencias, etiquetas, notas y enlaces.
- Menú interactivo y comandos de consulta, historial y estadísticas.
- Avisos persistentes configurables y API HTTP local para alertas.
- Aviso de arranque con versión y hora.
