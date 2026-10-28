# Asistente del día

Primera versión local para que Claude Code y Codex administren tareas de trabajo por CLI. Taskwarrior 3.5.0 conserva las tareas; SQLite guarda procedencia, foco, eventos e intentos de aviso. No necesita MCP ni paquetes Python externos para ejecutarse.

**Estado:** piloto manual con datos ficticios en Linux aprobado tras review del arquitecto. Mac aún no validada. No hay jobs instalados ni integración con correo/Teams. La entrada es texto o imágenes que el agente interpreta; este programa recibe datos estructurados.

## Usar en esta PC

Requiere Python 3.11+ y Taskwarrior 3.5.0. En esta PC quedó preparado un entorno de desarrollo en `.runtime/taskwarrior`, excluido de Git. Para otra instalación configura `ASISTENTE_TASK_BIN` con la ruta al ejecutable.

```sh
cd /home/bri/projects/asistente-dia
python3 -B asistente.py doctor
python3 -B asistente.py --json task list
```

El estado por defecto es `${XDG_STATE_HOME:-~/.local/state}/asistente-dia`, separado de `~/.task` y de los repos. Se puede usar `--state /otra/carpeta/privada`; el programa rechaza guardar estado dentro de este proyecto. Los tests sólo usan carpetas temporales.

## Flujo para agentes

```sh
# Una clave identifica el pedido, no cada intento de ejecutarlo.
python3 -B asistente.py --json task add "[FICTICIO] Revisar PR" \
  --priority high --source pr --reference "PR de ejemplo" --key captura-ejemplo-1

python3 -B asistente.py --json brief

# Usa UUID y revisión recibidos; no copies IDs numéricos de Taskwarrior.
python3 -B asistente.py --json focus push UUID --expect REVISION --key foco-ejemplo-1

python3 -B asistente.py --json task snooze UUID "2030-01-01T15:00:00-07:00" \
  --zone America/Edmonton --key posponer-ejemplo-1

python3 -B asistente.py --json focus resume --expect REVISION --key retomar-ejemplo-1
python3 -B asistente.py --json task done UUID --key cerrar-ejemplo-1
```

Repetir clave y contenido devuelve el resultado previo sin duplicar. Cambiar el contenido con la misma clave da un conflicto. `focus push/resume` requiere `--expect` para detectar decisiones basadas en una revisión antigua. Una mutación nueva incrementa la revisión. Si una hora local se repite por DST, se exige `--fold 0` o `--fold 1`; una hora inexistente se rechaza.

`task update` permite `--title`, `--priority`, `--due`, `--clear-due` y `--progress`. Sólo `--progress` registra avance humano. Cambios de fecha, foco o posposición no equivalen a progreso.

`event add TITULO --start ISO --end ISO --zone ZONA --key CLAVE` y `event list/cancel` administran la agenda manual. Un `--estimate-minutes` en la captura permite advertir choques entre la siguiente tarea y los eventos registrados.

## Avisos

```sh
python3 -B asistente.py pulse          # consulta pura; no envía ni consume avisos
python3 -B asistente.py notify         # prepara, revalida e intenta avisar
python3 -B asistente.py --json doctor  # muestra fallos y resultados inciertos
python3 -B asistente.py schedule --dry-run
```

`brief`, `task list` y `pulse` no consumen intervalos. `notify` registra intentos en una cola durable. Un fallo tiene hasta tres intentos con backoff; un resultado incierto no se reintenta automáticamente. Sólo tras revisar el caso se usa `notify --retry-uncertain`. Un código exitoso del notificador significa aceptación local, no lectura humana. Una caída después del popup y antes del commit puede producir duplicación si se autoriza reintentar.

Los avisos cubren vencimientos, fin de snooze, estancamiento, mensajes capturados pendientes y eventos próximos/en curso. Se agrupan los motivos de cada tarea en un aviso y se respeta su intervalo aunque cambie el motivo. El orden es evento, vencimiento, fin de snooze, chat y estancamiento; dentro de cada motivo, fecha y UUID. El presupuesto compartido por todos los procesos es de tres intentos en cinco minutos (`notification_budget`); los fallos también lo consumen. No se acumulan avisos por pulsos omitidos. Tras una ausencia, los vencimientos siguen vigentes y los eventos terminados se omiten. Un retroceso del reloj conserva la supresión hasta alcanzar los tiempos guardados.

Los defaults visibles en `config list` son políticas iniciales ajustables, no horarios asumidos del usuario. `config set NOMBRE VALOR --key CLAVE` permite cambiarlos. No hay avisos periódicos hasta instalar explícitamente un job, algo que esta versión sólo muestra en `dry-run`. Se deben acordar jornada y horas de silencio antes de hacerlo.

## Recuperación

```sh
python3 -B asistente.py --json recover
```

Las intenciones se guardan antes de escribir en Taskwarrior. Tras una caída, se verifica el efecto antes de repetir. Los cambios externos incompatibles bloquean la recuperación y las escrituras siguientes; `doctor/recover` muestran la operación. No se sobrescriben ni se compensan automáticamente. En esta primera versión, resolver un conflicto externo requiere revisar el estado con el arquitecto; no hay un comando que lo fuerce.

Una tarea completada/pospuesta/bloqueada que figure en la pila no se restaura silenciosamente. `focus resume` informa el marco inválido. Tras revisar el motivo, `--skip-invalid` retira como máximo ese marco sin activar una tarea incorrecta.

## Demo ficticia

`python3 -B scripts/demo.py` recorre captura, prioridad, snooze, foco anidado, cierre, reanudación, agenda y pulso mediante la CLI, en estado temporal que se elimina al terminar. Añade `--notify` sólo si quieres intentar un popup local de ejemplo. No instala jobs.

## Pruebas

```sh
ASISTENTE_TASK_BIN="$PWD/.runtime/taskwarrior/bin/task" \
  python3 -B -m unittest discover -s tests -p test_assistant.py -v

# Sistema: CLI en procesos nuevos y notify-send real en D-Bus privado.
# Requiere dbus-run-session, notify-send y Python gi (Gio/GLib).
python3 -B scripts/test_system.py
```

Las pruebas de integración ejecutan Taskwarrior real. Incluyen muerte de procesos entre pasos, rollback de SQLite, conflicto externo sin sobrescritura, concurrencia, DST, snooze vencido, supresión, reintentos y avisos inciertos. Para revisar la prueba inicial independiente del producto, consulta [el laboratorio de viabilidad](pruebas/viabilidad-taskwarrior/README.md).

La suite de sistema recorre siete días lógicos acelerados, ausencias, reinicios, fallos reales de transporte y claims tardíos. Su reloj sólo existe en el harness. El receptor aislado registra los mensajes enviados por `notify-send`; no usa el bus de tu escritorio. Consulta [alcance y evidencia](docs/e2e-linux.md) antes de interpretar esto como validación de operación desatendida.

## Límites actuales

La coordinación cubre escritores que usan esta CLI en una misma máquina. No es una transacción distribuida ni sincronización Mac–Linux. No escribir directamente con `task` sobre el estado administrado salvo durante una reconciliación revisada. La copia coherente de seguridad y un proceso de retención/restauración de producción aún no tienen un comando en el producto.

Cada intento posee un identificador persistente. Un resultado tardío se conserva como evidencia y sólo finaliza el aviso si mantiene la propiedad del claim. El notificador tiene timeout de 10 segundos y un lease de dos minutos. El esquema SQLite migra de v1 a v2 conservando tareas, claves y supresión; detén los procesos de la versión anterior antes de actualizar.

El núcleo no extrae imágenes ni interpreta frases. Eso corresponde a los agentes con estas [instrucciones](AGENTS.md). Los datos observados son una instantánea: el asistente no sabe si llegaron mensajes nuevos ni si contestaste fuera de él.

No se probó en Mac ni Windows. Los adaptadores están separados para permitir esa validación; su presencia no demuestra compatibilidad. El build corporativo de Taskwarrior sigue pendiente. El manifiesto Linux de la prueba no es una lista de dependencias aprobadas para la empresa.

`--lang es|en` controla la salida humana. Las claves y valores de máquina del contrato JSON permanecen en inglés, independientemente del idioma.
