# Diseño: push y pull seguros del proyecto

## Objetivo

Crear dos comandos sencillos para sincronizar el repositorio sin perder ni omitir datos deportivos. El push debe incluir siempre código y datos persistentes —estadísticas, marcadores, deep stats, evidencias, modelos y precálculos— y debe excluir cachés, logs, capturas y archivos temporales. El pull debe negarse a continuar cuando existan cambios locales versionables.

## Interfaz

- `scripts/push_project.ps1 -Message "mensaje del commit"`
- `scripts/pull_project.ps1`

Ambos scripts se ejecutan desde cualquier directorio y localizan la raíz del repositorio a partir de su propia ubicación. Operan sobre la rama actual, que debe ser `main`, y el remoto `origin`.

## Arquitectura

Los dos lanzadores PowerShell delegan la lógica a `scripts/project_sync.py`. El módulo Python concentra las reglas para que puedan probarse sin realizar pushes o pulls reales:

- clasificación de rutas persistentes y prohibidas;
- lectura de estado Git en formato estable;
- validación de archivos preparados;
- checkpoint e integridad de SQLite;
- comprobación de divergencia entre `main` y `origin/main`;
- ejecución controlada de Git y de la suite de tests.

Los lanzadores solo resuelven el intérprete Python, transmiten parámetros y conservan el código de salida.

## Datos que deben incluirse

El push prepara todos los cambios versionables del repositorio. Las reglas de `.gitignore` excluirán los artefactos desechables, mientras que el programa comprobará expresamente que las rutas persistentes no estén ignoradas:

- `data/worldcup.sqlite`;
- `data/models/**` salvo temporales;
- `data/fixtures/**`;
- `data/evidence/reviewed-json/**`;
- `data/precomputed/**`;
- código, tests, scripts, documentación y configuración versionable.

Antes de preparar archivos, el programa ejecuta `PRAGMA wal_checkpoint(TRUNCATE)` para incorporar a `worldcup.sqlite` cualquier escritura pendiente del WAL. Después ejecuta `PRAGMA integrity_check`; cualquier resultado distinto de `ok` aborta.

## Datos que nunca deben incluirse

Se reforzará `.gitignore` para cubrir:

- `data/cache/`;
- `output/`;
- `.codex-remote-attachments/`;
- logs (`*.log`, `*.out.log`, `*.err.log`);
- archivos SQLite auxiliares (`*.sqlite-wal`, `*.sqlite-shm`, journals y copias temporales);
- cachés de Python, Streamlit, IDE y herramientas locales.

Después de `git add -A`, el programa inspecciona el índice. Si alguna ruta prohibida estuviera preparada —por ejemplo porque ya fuese tracked—, aborta antes del commit y muestra las rutas exactas. También aborta si queda algún archivo no ignorado sin preparar, evitando que un nuevo JSON o precálculo se quede fuera accidentalmente.

## Flujo de push

1. Confirmar que existe el repositorio, la rama es `main` y el remoto `origin` está configurado.
2. Hacer checkpoint y validar la SQLite.
3. Verificar que todas las rutas persistentes obligatorias existen y no están ignoradas.
4. Ejecutar la suite completa de tests.
5. Ejecutar `git fetch origin` y abortar si `origin/main` contiene commits que el `main` local no tiene.
6. Ejecutar `git add -A`.
7. Bloquear rutas prohibidas en el índice y comprobar que no quedan cambios versionables fuera.
8. Si no hay cambios, informar de que no es necesario crear commit; aun así comprobar si existen commits locales pendientes de push.
9. Si hay cambios, exigir un mensaje no vacío y crear el commit.
10. Ejecutar `git push origin main` y mostrar el commit publicado.

El programa no hace force-push, merge automático ni rebase.

## Flujo de pull

1. Confirmar repositorio, rama `main` y remoto `origin`.
2. Examinar cambios tracked y archivos untracked no ignorados.
3. Si existe cualquier cambio versionable local, abortar e indicar que primero debe ejecutarse `push_project.ps1`.
4. Los archivos ignorados —cachés, logs y temporales— no bloquean el pull.
5. Ejecutar `git fetch origin` y luego `git merge --ff-only origin/main`.
6. Validar la integridad de SQLite después de actualizar.

No se usa stash automático y no se resuelven conflictos de forma silenciosa.

## Errores y seguridad

Cada operación externa comprueba su código de salida. Un fallo detiene el flujo inmediatamente. Los mensajes explican qué comprobación falló y no sugieren comandos destructivos. El programa nunca borra archivos, nunca ejecuta `reset --hard` y nunca altera la historia publicada.

Para evitar una instantánea inconsistente, el mensaje inicial pedirá cerrar la aplicación o cualquier proceso que esté escribiendo en SQLite. Si el checkpoint encuentra la base bloqueada, el push aborta.

## Pruebas

Las pruebas unitarias crearán repositorios Git temporales y verificarán:

- inclusión de SQLite, modelos, fixtures, evidencias y precálculos;
- exclusión de cachés, logs, output y adjuntos de Codex;
- aborto del pull con cambios locales importantes;
- tolerancia del pull a archivos ignorados;
- bloqueo de una ruta prohibida ya preparada;
- detección de archivos versionables omitidos;
- validación de mensaje de commit;
- checkpoint e integridad de una SQLite en modo WAL;
- rechazo de actualizaciones no fast-forward o de un remoto adelantado antes del push.

Las pruebas de integración ejecutarán los scripts en repositorios locales con un remoto bare, sin usar red ni modificar el repositorio real.
