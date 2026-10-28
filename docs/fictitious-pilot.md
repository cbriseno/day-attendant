# Piloto manual con datos ficticios

El piloto no autoriza uso laboral ni instalación de jobs. Usa `scripts/demo.py`,
que crea estado temporal y lo elimina al terminar. Para explorar manualmente,
elige otra carpeta temporal explícita con `--state`; no reutilices datos reales.

Ante un conflicto externo:

1. Detén las escrituras y conserva el error y la clave de operación.
2. Ejecuta `doctor` y `recover` sobre esa carpeta. Registra los diagnósticos; no
   edites SQLite ni fuerces un cambio en Taskwarrior para hacer desaparecer el error.
3. Conserva la carpeta ficticia para review con el arquitecto. El diagnóstico no
   significa que esa carpeta esté lista para continuar.
4. Si quieres seguir explorando, crea otra carpeta temporal vacía y utiliza
   nuevas claves de solicitudes. Revisa que sea la carpeta ficticia nueva antes
   de ejecutar cualquier mutación. No elimines la carpeta anterior para ocultar
   el conflicto ni apliques este procedimiento a datos laborales.

Un aviso `expired_unknown` significa que venció un claim y no se conoce su
resultado. `notify` lo reconcilia como incierto sin reintentar por su cuenta.
`--retry-uncertain` es una decisión explícita después de revisar el caso; puede
duplicar un popup que el sistema local hubiera aceptado antes de la caída.

Antes de datos reales hacen falta una resolución supervisada de conflictos y
backup/restauración coherentes, además de retención y validación de la plataforma.
