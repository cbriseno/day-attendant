# Validación autónoma en Linux

El recorrido CLI inicial de 18 pasos y las 30 pruebas de integración no demostraban
comportamiento durante varios días ni el transporte real de avisos. Esta fase añade
una suite de sistema para descubrir esos fallos sin delegar la validación al usuario.
El arquitecto fijó el contrato en la ronda 07 de la conversación guardada localmente.

Resultado registrado: **30 pruebas de integración en 32.111 s y 14 de sistema en
40.382 s, sin fallos, errores ni omisiones**. Los logs están en
[integración](test-results-hardening-linux.txt) y [sistema](test-results-system-linux.txt).
La [evidencia por escenario](e2e-evidence/summary.json) contiene solicitudes y
respuestas D-Bus, comandos, exportaciones y journal; todos los datos son ficticios.

## Fronteras ejercitadas

| Nivel | Ejecución | Alcance |
|---|---|---|
| Integración | Python, SQLite y Taskwarrior 3.5.0 real | 30 pruebas previas de recuperación, foco, DST y concurrencia |
| Sistema acelerado | CLI pública en procesos nuevos, estado persistente, reloj del harness | Siete días lógicos, fin de semana sin pulso, reinicios, retroceso del reloj |
| Transporte Linux | `notify-send` real, D-Bus privado, receptor Gio instrumentado | Payload, aceptación, rechazo, ausencia de servidor, timeout |
| Plataforma desatendida | Pendiente | Scheduler instalado, suspensión física, sesión gráfica, tiempo real |

El entry point de pruebas sólo inyecta un reloj y barreras de fallo en `Assistant`.
No reemplaza métodos de negocio, Taskwarrior ni el notificador. Otro escenario usa
directamente `asistente.py` con reloj real. El reloj de Taskwarrior y el del sistema
operativo permanecen reales: esto valida las decisiones temporales del asistente,
no una semana física ni el temporizador nativo de Taskwarrior.

El bus de pruebas no carga directorios de activación de servicios. Detener el
receptor no puede arrancar el daemon de notificaciones del escritorio. Todos los
datos son ficticios y viven en carpetas temporales; ningún job se instala.

## Defectos reproducidos y corregidos

Se ejecutaron los nuevos escenarios contra el código anterior `9e826f1` en una copia
temporal. Fallaron por dos causas observables:

- Ocho tareas con tres motivos cada una produjeron **24 avisos** donde el contrato
  exigía un máximo de tres. Ahora se agrupan por entidad, se comparte la supresión
  entre motivos y todos los procesos respetan tres intentos por cinco minutos.
- Un intento detenido después de la aceptación podía volver de `uncertain` a
  `accepted` al finalizar tarde. Ahora el aviso sólo cambia mediante una actualización
  condicionada al identificador vigente. El resultado tardío queda en `late_outcome`
  del intento sin consumir supresión ni modificar un propietario nuevo.

La primera ejecución del harness tuvo tres errores en su lector de trazas: buscaba
el cuerpo en el registro de aceptación en vez de asociarlo a la solicitud. Se
corrigió el lector y se repitió la suite; esos errores no se ocultaron como éxitos.

## Escenarios y oráculos

Las expectativas están escritas en los tests sin llamar al evaluador de candidatos.
Se contrastan la exportación JSON nativa de Taskwarrior, las filas de SQLite y el
registro del servidor D-Bus. Se conservan comandos, códigos de salida y fallos
inyectados en el directorio de evidencia.

| Escenario | Resultado exigido |
|---|---|
| Viernes a viernes, siete días lógicos | Evento del viernes una vez, tarea del lunes tras snooze, sin evento vencido ni backlog |
| Retroceso de una hora y reinicios | No rompe supresión; completar la tarea elimina avisos posteriores |
| Ocho tareas, tres notifiers concurrentes | Tres mensajes distintos; otros tres tras cinco minutos, sin duplicar motivos |
| Claim finaliza después de vencer | Conserva uncertain y accepted_at vacío; registra resultado tardío |
| Nuevo propietario antes de finalizar el anterior | El intento viejo no altera el claim nuevo |
| Claim vence antes del transporte | No se invoca notify-send |
| Rechazo, servidor ausente, timeout | Fallos visibles, sin aceptación ficticia ni reintento automático de uncertain |
| Cliente notify-send ausente | Diagnóstico persistido, cero solicitudes D-Bus |
| Caídas en cuatro límites y nueve capturas concurrentes | Siete UUID únicos para siete solicitudes, cero intents pendientes |
| Edición externa durante recuperación | Bloquea nuevas escrituras y conserva la edición |
| Foco A→B→C→B→A y marco completado | Un foco coherente; exige omisión explícita del marco inválido |
| Migración v1→v2 con datos | Conserva idempotencia, tareas y supresión |
| Migración de un claim sending heredado | Doctor muestra expiración; notify lo vuelve incierto sin reenviar |
| Aparece due tras aceptar stale | Supresión intencional hasta completar el intervalo de la entidad |
| CLI sin reloj inyectado y schedule dry-run | Entrega de transporte real y ninguna instalación solicitada |

## Reproducir

```sh
ASISTENTE_TASK_BIN="$PWD/.runtime/taskwarrior/bin/task" \
  python3 -B -m unittest discover -s tests -p test_assistant.py -v
E2E_REPORT_DIR="$PWD/.runtime/e2e-report" python3 -B scripts/test_system.py
```

Requiere Python 3.11+, Taskwarrior 3.5.0, `notify-send`, `dbus-run-session` y `gi`
con Gio/GLib. Estas últimas dependencias son del laboratorio, no del producto.
En un sandbox que prohíba sockets Unix, el bus privado necesita ejecutar fuera de
esa restricción. La suite se omite si se intenta descubrir directamente sin su
runner: no debe conectarse accidentalmente al escritorio.

## Lo que sigue sin estar demostrado

No certifica una semana real, cron/systemd instalados, suspensión física, presentación
visual o lectura humana de popups, macOS, extracción de capturas por agentes ni
sincronización entre computadoras. No autoriza datos laborales ni ejecución
desatendida. Siguen pendientes resolución supervisada de conflictos, backup y
restauración coherentes, retención y jornada/horas de silencio. La validación de esos
componentes corresponde al desarrollo, no a esperar a que el usuario descubra fallos.

## Cierre del review Round 08

El arquitecto leyó el source y las trazas, sin ejecutar los tests. No encontró un P0
nuevo y aprobó provisionalmente L1/L2/L3 con dos condiciones concretas. Ambas
regresiones se añadieron y pasaron en la ejecución completa final de 14 escenarios:
claim sending heredado de v1 y supresión de due tras aceptar stale. Su dictamen
indica que cumplir esas dos condiciones cierra esta fase sin otro review extenso.

La supresión por entidad también retrasa una escalada stale→due. Es una decisión
conservadora explícita, no una garantía de aviso inmediato de toda urgencia. Los
fallos consumen presupuesto y pueden retrasar un evento nuevo. Antes del uso laboral
se debe decidir si alguna escalada rompe esa supresión; no se añadió ese motor.
