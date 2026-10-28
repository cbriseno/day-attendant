# Prueba acotada de Taskwarrior

Experimento autorizado desde Lavish, con datos ficticios y sin instalar trabajos programados. No es la implementación del asistente.

## Resultado observado en Linux

Taskwarrior 3.5.0, paquete conda-forge `hef4216c_1`, instalado en un prefijo temporal. El sistema no tenía Taskwarrior, CMake ni Rust; Debian ofrecía 2.6.2. Se usó el paquete 3.5.0 para probar su comportamiento, no para simular una compilación corporativa.

**13 pruebas aprobadas, 0 fallos y 0 errores**, en 16.969 segundos. Incluyen concurrencia, identidad, dependencias, foco anidado, recuperación de procesos terminados y restauración con los escritores detenidos. Los resultados y el entorno exacto están junto a este archivo.

El primer intento tuvo un fallo de expectativa sobre `status:waiting`, conservado en `resultados-linux-inicial.json`. Se diagnosticó con el ejecutable y el código oficial fijado en `v3.5.0`, commit `3419d5ba1e6a780fbbb0a8d68d8fd31e675c1bf8`:

- La exportación guarda `status:pending` aunque `+WAITING` sea verdadero.
- Un atributo `wait` pasado puede permanecer guardado cuando `+WAITING` ya es falso.
- La prueba corregida verifica el estado derivado, la fecha y la reaparición; no basta con renombrar la expectativa.

## Qué demuestra el laboratorio

| Caso | Comprobación independiente del resultado |
|---|---|
| CLI/JSON e identidad | UUID conservado tras editar y completar una tarea |
| Snooze/tiempo | `+WAITING`, retirada de wait y dos instantes UTC que corresponden a la hora repetida en Edmonton; exportación estable bajo dos zonas |
| Dependencias | Dependiente aparece bloqueada y deja de estarlo al completar requisito |
| Idempotencia | Repetir creación no duplica ni borra ediciones posteriores |
| Concurrencia | 12 procesos crean tareas distintas mientras se consulta; 6 crean el mismo UUID sin duplicarlo |
| Ediciones primitivas | Dos procesos modifican campos distintos de una tarea sin perder esos cambios en esa ejecución |
| Caída al crear | Proceso termina con código 73 en tres puntos; recuperación repetida conserva una sola tarea |
| Caída en foco/reanudación | Cinco puntos por operación: intención, parada, arranque, marco y finalización; foco y pila quedan consistentes tras recuperar |
| Pila | A → B → C → B; completar A mientras estaba pausada no la reactiva |
| Backup | Copia bajo bloqueo, sin escrituras en curso; restauración mantiene UUID y permite retomar foco |

El cierre abrupto se produce realmente con `os._exit(73)`. La muerte del proceso libera el bloqueo de `flock`. No se prometen garantías frente a un corte eléctrico o fallo del disco.

Taskwarrior es la autoridad de tareas. SQLite contiene sólo intenciones de operaciones y marcos de foco por UUID. Las intenciones conservan el pedido original para recuperación; no son otra tabla mutable de títulos/estados.

## Notificaciones y planificador de este entorno

`notify-send` aceptó un aviso ficticio con código 0. Eso prueba aceptación del comando local, no que el usuario lo haya visto. `systemctl --user is-system-running` devolvió `offline`; existe `crontab`. No se instaló ningún timer ni se probó ejecución programada. La selección del adaptador debe basarse en capacidades reales, no sólo en detectar `systemctl` en PATH.

## Límites: la adopción sigue pendiente

- No se aprobó ni compiló Taskwarrior en la Mac. El `Cargo.lock` upstream contiene 348 entradas, incluidas dependencias específicas de plataforma/build; no significa que todas se compilen en macOS. Revisar toolchain, crates transitivos y submódulos `libshared` y `corrosion` contra los repos aprobados.
- El laboratorio serializa las operaciones compuestas mediante una única puerta de escritura. No cubre cambios externos de Taskwarrior durante una operación parcialmente recuperada, escrituras desde otro programa o sincronización entre equipos.
- No se implementaron una cola durable de notificaciones, su supresión/reintentos, la captura de imágenes ni eventos/choques. No se evalúan esas garantías con estas pruebas.
- El foco usa `start/stop` y una pila auxiliar. La política de varias tareas activas, cierres fuera de orden complejos y conflictos de recuperación requiere definición adicional.
- Se probaron instantes UTC explícitos y cambio de zona de visualización. No se validó parsing libre de fechas ambiguas ni todos los casos de DST.
- El backup probado detiene a los escritores coordinados. No demuestra una copia en caliente consistente con escritores ajenos al coordinador.
- No se midió comportamiento sostenido bajo carga ni estabilidad entre versiones. Es evidencia de viabilidad local, no certificación de producción.

**Dictamen:** Taskwarrior continúa siendo viable como candidato. El experimento muestra que un conjunto pequeño de operaciones cruzadas puede recuperarse sin duplicar las tareas. La aprobación/build en Mac y los límites de recuperación siguen siendo condiciones antes de adoptar el backend.

## Reproducir

Requiere Python con biblioteca estándar y Taskwarrior 3.5.0 en Linux/macOS. El laboratorio usa `fcntl`, apropiado para las dos plataformas de uso real; no demuestra portabilidad a Windows.

```sh
python3 -B probe.py --task-bin /ruta/al/binario/task --report resultados-locales.json
```

Cada caso crea y elimina su estado ficticio en una carpeta temporal. No utiliza `~/.task` ni la configuración habitual de Taskwarrior. El script no configura ningún scheduler, servidor de sincronización ni cuenta externa.

Para reproducir el binario Linux con micromamba, usar el archivo `entorno-linux-explicit.txt` con el comando `micromamba create --no-rc -p /prefijo/aislado -f entorno-linux-explicit.txt`. El manifiesto contiene las URLs y hashes de paquetes resueltos; no usarlo como lista de paquetes aprobados en Mac.

## Preparación de la comprobación en Mac

1. Confirmar Python disponible y aprobación de Taskwarrior 3.5.0, CMake, compilador C++17, Rust >=1.88, Cargo y sus dependencias/submódulos. Seguir las [instrucciones oficiales fijadas](https://github.com/GothenburgBitFactory/taskwarrior/blob/v3.5.0/INSTALL).
2. Compilar con un prefijo de usuario y sin `sudo`. La opción upstream `CMAKE_INSTALL_PREFIX` permite evitar `/usr/local`.
3. Registrar commit, versiones, procedencia de dependencias y salida de compilación. No sustituir un repos corporativo por una descarga externa para hacer pasar la prueba.
4. Ejecutar este laboratorio con ese binario y datos ficticios. Probar por separado el permiso de notificaciones y el planificador de usuario.
5. Comparar los resultados y los requisitos de mantenimiento antes de elegir backend. Un bloqueo corporativo recurrente o un adaptador que duplique estados es un motivo para reevaluar la alternativa SQLite propia.

No se necesita acceso a datos laborales para ninguna de estas comprobaciones.
