# Plan de implementación: framework de ciclos para agentes IA

## 1. Objetivo

Agregar a WebAgent un flujo guiado para tareas de desarrollo:

1. El usuario describe una tarea.
2. Un agente analiza el repositorio y devuelve un plan en Markdown.
3. WebAgent muestra ese `.md` para que el usuario lo revise.
4. El usuario puede aprobarlo o pedir cambios.
5. Al aprobarlo, WebAgent ejecuta ciclos autónomos hasta terminar:
   - lanza al agente con el plan y el estado actual;
   - aclara que no debe ejecutar los chequeos locales;
   - ejecuta los chequeos mediante `autorun`;
   - si fallan, devuelve los errores al agente para el siguiente ciclo;
   - si pasan, guarda los cambios en Git y continúa con el siguiente ciclo.
6. El flujo termina cuando el agente declara que no quedan tareas y los chequeos pasan, o cuando se alcanza un límite/error que requiera intervención.

El framework debe ser reutilizable por Copilot, Codex y cualquier agente compatible con la configuración existente. La ejecución de verificaciones pertenece a WebAgent, no al agente, para evitar consumir tokens en comandos repetibles y para que todos los agentes usen el mismo criterio.

## 2. Alcance y decisiones

### Incluido

- Crear, editar, visualizar y aprobar un plan Markdown.
- Persistir el plan, su estado, el ciclo actual y el historial de resultados.
- Ejecutar ciclos secuenciales sobre una carpeta Git.
- Reutilizar `AgentConsolePanel`, `AgentConfigStore`, `build_autorun_plan`, `validate_autorun_command` y `GitVersioning`.
- Detectar automáticamente los checks por lenguaje cuando no exista un comando configurado.
- Configurar un límite de ciclos y permitir pausar, cancelar y reanudar.
- Mostrar en vivo la salida del agente y de cada check.
- Commitear solamente después de un ciclo sin errores.

### Fuera de alcance inicial

- Ejecutar agentes en paralelo sobre el mismo working tree.
- Hacer `push`, merge o cambiar ramas automáticamente.
- Permitir que el agente elija o ejecute comandos de shell arbitrarios.
- Ocultar errores, corregirlos silenciosamente o marcar una tarea como terminada sin verificación.

La rama de trabajo será la rama activa del repositorio, respetando la separación actual `master`/`main` cuando la configuración del proyecto la utilice. El framework no debe cambiar de rama si hay cambios sin commitear que puedan mezclarse.

## 3. Modelo de estados

Cada ejecución debe tener un identificador y avanzar por una máquina de estados persistible:

```text
DRAFT
  -> PLAN_READY
  -> WAITING_APPROVAL
  -> REVISION_REQUESTED -> PLAN_READY
  -> RUNNING_AGENT
  -> RUNNING_CHECKS
  -> CHECKS_FAILED -> RUNNING_AGENT
  -> CHECKS_PASSED -> COMMITTING
  -> COMMITTED -> RUNNING_AGENT
  -> COMPLETED
```

Estados terminales adicionales:

- `PAUSED`: pausa solicitada por el usuario; conserva el contexto para reanudar.
- `CANCELLED`: cancelación explícita; no inicia procesos nuevos.
- `BLOCKED`: falta de agente, repositorio, identidad Git, configuración o permiso.
- `FAILED`: error irrecuperable del proceso de agente, autorun o persistencia.

Reglas:

- `RUNNING_AGENT` no puede iniciar si existe otro proceso activo para la misma carpeta.
- `RUNNING_CHECKS` ejecuta la lista completa en orden; el primer fallo detiene la lista.
- `COMMITTING` solo es válido con todos los checks exitosos y cambios detectables.
- `COMPLETED` requiere una respuesta final del agente que indique que la tarea está terminada y una última ejecución exitosa de checks.
- Un commit creado por el framework no debe iniciar otro ciclo por sí mismo.

## 4. Flujo de usuario y UI

Agregar una entrada “Framework de tarea” desde la consola de agentes o el menú de agentes IA. La vista debe incluir:

- editor de la solicitud original;
- carpeta/repositorio seleccionado;
- agente ejecutor;
- límite máximo de ciclos, con un valor seguro por defecto;
- botón **Generar plan**;
- visor/editor del Markdown generado;
- botones **Pedir cambios**, **Aprobar y ejecutar**, **Pausar** y **Cancelar**;
- estado actual, número de ciclo, commit producido y último resultado de checks;
- pestañas de salida para el plan, el agente, los checks y el historial.

Al pedir cambios, el usuario debe ingresar una observación. Esa observación vuelve al agente planificador y genera una nueva versión, sin comenzar la implementación. La aprobación debe ser explícita y quedar registrada con fecha, perfil, agente y hash del estado inicial.

El archivo de plan debe guardarse dentro del directorio de datos de WebAgent o en una ubicación de sesión no versionada por defecto. Solo debe escribirse en el repositorio si el usuario lo solicita; en ese caso usar un nombre configurable, por defecto `framework_plan.md`, y tratarlo como un cambio normal del ciclo.

## 5. Generación y contrato del plan Markdown

El agente planificador debe inspeccionar el repositorio y devolver únicamente el contenido del plan, sin modificar archivos. El documento debe contener:

```markdown
# Objetivo
# Contexto y supuestos
# Archivos a modificar
# Pasos de implementación
# Criterios de aceptación verificables
# Checks esperados
# Riesgos y decisiones pendientes
```

WebAgent debe validar que la respuesta no esté vacía, conservar el texto original y generar una versión numerada o con timestamp. No debe interpretar texto libre como comandos. Los checks que el agente proponga son información para revisión; la ejecución real sale de la configuración segura de autorun y de la detección local.

El prompt de planificación debe exigir:

- inspección del repositorio antes de proponer cambios;
- pasos pequeños y verificables;
- no editar archivos ni crear commits durante la planificación;
- señalar incertidumbres en vez de inventar detalles;
- criterios claros para declarar la tarea terminada.

## 6. Prompt de cada ciclo de implementación

Cada ciclo debe recibir la solicitud original, la versión aprobada del plan, el número de ciclo, el estado Git relevante y el contexto del ciclo anterior. El prompt debe incluir explícitamente:

> Implementá el siguiente paso pendiente del plan. No ejecutes chequeos, tests, builds, linters ni comandos de verificación: WebAgent los ejecutará automáticamente al terminar tu ciclo. No hagas el commit; WebAgent lo hará solo si todos los checks pasan. Inspeccioná primero los cambios existentes y no borres trabajo válido.

En el primer ciclo se omite el contexto de errores. En ciclos posteriores se agrega solo:

- comando que falló;
- código de salida;
- salida relevante, limitada a un tamaño seguro;
- archivos modificados desde el commit anterior;
- instrucción de corregir la causa y no repetir el mismo intento sin cambios.

No se debe reenviar automáticamente el log completo, credenciales, tokens, cookies ni archivos no relacionados. Si el agente informa que la tarea está terminada, WebAgent aún debe ejecutar los checks antes de aceptarla.

## 7. Autorun y detección de checks

Reutilizar `core.automation.build_autorun_plan(folder)` como detector inicial y `validate_autorun_command` como barrera de seguridad. La prioridad debe ser:

1. comando explícito configurado para el repositorio;
2. checks detectados por manifiestos y archivos presentes;
3. bloqueo claro si no existe ningún check confiable y el repositorio no permite determinar cómo validarse.

La detección debe poder ampliarse por lenguaje sin cambiar el orquestador. Como mínimo contemplar los patrones ya soportados:

- Python: `python -m compileall -q .` y `pytest -q` cuando corresponda;
- Node: `npm test` si existe el script;
- Rust: `cargo test --quiet`;
- Go: `go test ./...`;
- .NET: `dotnet test --no-restore`;
- Git: `git diff --check`.

Los checks se ejecutan en secuencia, con `cwd` igual al repositorio, entorno del agente y timeout configurable. Toda salida debe transmitirse a la consola y persistirse. Un comando desconocido, inválido o no permitido es un error de configuración, no un resultado exitoso.

## 8. Commit y continuación

Después de que todos los checks terminen con código cero:

1. refrescar el estado y el diff;
2. si no hay cambios, no crear un commit vacío;
3. validar que el repositorio tenga identidad Git;
4. crear un commit generado por WebAgent, por ejemplo:
   `chore(framework): complete cycle <n>`;
5. guardar hash, mensaje, archivos y timestamp;
6. iniciar el siguiente ciclo con el plan y el nuevo estado.

El agente no debe hacer commits. Si el commit falla, el estado pasa a `BLOCKED` o `FAILED` según la causa y se muestra el error sin iniciar otro ciclo. Nunca hacer `git add -A` fuera del repositorio seleccionado ni incluir credenciales o archivos temporales; la estrategia de staging debe respetar la política Git existente y excluir archivos sensibles.

## 9. Terminación, límites y errores

El agente debe devolver una señal estructurada al final de cada ciclo, preferentemente en un bloque JSON delimitado, con:

```json
{
  "status": "continue|complete|blocked",
  "summary": "resumen breve",
  "next_steps": ["..."],
  "criteria_met": ["..."]
}
```

Si la señal falta o es inválida, tratar el ciclo como `continue` mientras existan cambios y no se haya superado el límite. No confiar únicamente en frases como “terminado”.

Detener y pedir intervención cuando:

- se alcance el máximo de ciclos;
- el mismo check falle repetidamente sin cambios sustanciales;
- el agente solicite una decisión humana;
- falte autenticación, herramienta o identidad Git;
- haya conflictos, cambios externos o un working tree inesperado;
- se cancele el proceso o cierre la aplicación.

El usuario debe poder reanudar una ejecución persistida sin perder el plan, los commits ni los errores anteriores.

## 10. Persistencia y recuperación

Extender el almacenamiento de configuración existente sin guardar secretos. Se recomienda un archivo por repositorio/sesión en `IA_DATA_DIR`, con:

```json
{
  "run_id": "...",
  "folder": "...",
  "agent_id": "copilot",
  "status": "RUNNING_CHECKS",
  "plan_version": 1,
  "cycle": 2,
  "max_cycles": 10,
  "approved_at": "...",
  "base_head": "...",
  "cycles": [
    {
      "number": 1,
      "agent_exit_code": 0,
      "checks": [],
      "commit": null,
      "status": "..."
    }
  ]
}
```

Escribir de forma atómica y tolerar cierres inesperados. Al iniciar WebAgent, detectar ejecuciones incompletas y ofrecer reanudarlas, cancelarlas o inspeccionarlas. Los logs completos continúan en el sistema de historial existente; el estado solo debe guardar referencias y resúmenes.

## 11. Cambios técnicos propuestos

1. **Orquestador nuevo**, preferentemente `core/agent_framework.py`, sin dependencias de widgets. Será responsable de estados, ciclos, persistencia, límites y transiciones.
2. **Adaptador de ejecución** en `agents/agent_console.py` para lanzar el agente usando el mecanismo actual, capturar salida y notificar finalización.
3. **Runner de checks** reutilizando `core/automation.py`, con resultado estructurado, timeout, cancelación y salida acotada.
4. **API de Git** en `core/file_ops.py` para obtener HEAD/diff, validar identidad y crear commits seguros.
5. **Panel UI** en `agents/agent_console.py` o un diálogo dedicado, manteniendo el patrón de procesos no modales y referencias vivas ya usado por la aplicación.
6. **Persistencia** en `AgentConfigStore` o un almacén específico de ejecuciones; migrar configuraciones antiguas sin romper `autorun` existente.
7. **Historial**: agregar metadatos de framework a los logs de `agents/agent_runs.py`, sin incrustar logs gigantes en la vista resumida.

No cambiar el comportamiento del autorun manual: las ejecuciones normales de un agente deben seguir pudiendo usar checks y commit automático según su configuración. El modo framework debe ser una política explícita y más estricta.

## 12. Seguridad y confiabilidad

- Mantener la lista de binarios permitidos y el bloqueo de operadores de shell.
- Ejecutar siempre con `cwd` validado y sin interpolar texto del usuario en comandos.
- No enviar secretos al agente ni persistirlos en el plan o los logs.
- Aplicar límites de tiempo, ciclos, tamaño de salida y tamaño de prompt.
- No ejecutar checks en paralelo sobre un mismo working tree.
- Detectar cambios hechos fuera de la ejecución mediante el hash inicial y el estado Git.
- Liberar procesos al cancelar y evitar que callbacks de un ciclo viejo alteren una ejecución nueva.
- Informar explícitamente errores de configuración, proceso, check y commit.

## 13. Criterios de aceptación

- Se puede generar un plan Markdown sin modificar el repositorio.
- El usuario puede editar/rechazar el plan, pedir cambios y aprobar una versión concreta.
- La aprobación inicia automáticamente el primer ciclo.
- El agente recibe la instrucción de no ejecutar chequeos ni hacer commits.
- Si un check falla, el ciclo queda visible como fallido y el error llega al agente en el ciclo siguiente.
- Si todos los checks pasan, se crea exactamente un commit no vacío y se inicia el siguiente ciclo.
- La tarea solo se marca completa con señal de finalización y checks exitosos.
- Pausar, cancelar, cerrar y reanudar no corrompe el estado ni deja procesos huérfanos.
- Los límites de seguridad bloquean comandos inválidos y no exponen secretos.
- Las ejecuciones normales existentes y el autorun actual conservan su comportamiento.

## 14. Orden recomendado de implementación

1. Definir modelos de estado, resultados y persistencia atómica.
2. Extraer o completar un runner de autorun con callbacks/resultados estructurados.
3. Completar helpers Git para HEAD, staging seguro y commit.
4. Implementar el orquestador sin UI y probar sus transiciones.
5. Integrarlo con `AgentConsolePanel` y el historial de logs.
6. Agregar generación/edición/aprobación del plan.
7. Agregar pausa, cancelación, reanudación y límites.
8. Ejecutar pruebas manuales con un repositorio Python, uno Node/Rust y un repositorio con check fallido.
9. Actualizar README y la ayuda de configuración de agentes con el nuevo flujo.
