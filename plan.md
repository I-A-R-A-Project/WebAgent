# Plan para implementar un framework de agentes en WebAgent

## 1. Objetivo

Incorporar una capa de orquestación de agentes que permita convertir una tarea
del usuario en un flujo observable, reanudable y seguro. El framework debe
coordinar proveedores distintos, herramientas locales y operaciones Git sin
acoplar la lógica de agentes a la interfaz PyQt6.

El primer caso de uso recomendado es:

> recibir una tarea desde la bandeja de tareas, asociarla a una Colección,
> analizar el repositorio, proponer un plan, ejecutar cambios en una rama de
> trabajo, ejecutar verificaciones y dejar un resultado revisable.

La implementación debe conservar el comportamiento actual: Copilot y Codex
siguen pudiendo ejecutarse como CLI, Gemini y Groq siguen funcionando como
proveedores de consulta, y el usuario mantiene control sobre autenticación,
cambios Git, perfiles y aprobación de acciones.

## 2. Estado actual del repositorio

### Componentes existentes relevantes

- `window.py` conecta la interfaz, las pestañas, las Colecciones, la bandeja
  de tareas y la consola inferior de agentes.
- `agent_console.py` ejecuta comandos mediante `QProcess`, transmite salida en
  vivo, acepta stdin, guarda logs, detecta autenticación de Copilot y ejecuta
  autorun.
- `ai_manager.py` contiene `AGENT_DEFS`, plantillas de comandos, configuración
  por carpeta, opciones CLI, perfiles, ramas y el diálogo de configuración.
- `task_manager.py` persiste tareas JSON por perfil y clasifica Colecciones
  opcionalmente mediante Gemini.
- `scripts/gemini_agent.py` y `scripts/groq_agent.py` son adaptadores simples
  de API que imprimen una respuesta y terminan.
- `file_ops.py` centraliza operaciones Git, detección de repositorio, ramas,
  estado limpio, commits y contexto de ejecución.
- `agent_runs.py` y `agent_runs.html` muestran los logs históricos guardados
  en `%APPDATA%\IARA\WebAgent\agent_logs`.
- Los perfiles aíslan almacenamiento del navegador y `COPILOT_HOME`; las API
  keys y configuraciones se mantienen por carpeta/perfil.

### Limitaciones que el framework debe resolver

1. La unidad de ejecución actual es un comando individual; no hay estados,
   dependencias, reintentos ni checkpoints de un flujo.
2. `AgentConsolePanel` y el diálogo de agentes contienen lógica parcialmente
   duplicada para ejecutar procesos, autenticación, autorun y salida.
3. Los adaptadores de Gemini/Groq no comparten una interfaz de modelo,
   streaming, conteo de tokens ni errores normalizados.
4. Las tareas se guardan como JSON con estados básicos (`pending`,
   `completed`, `cancelled`), sin ejecuciones, eventos, artefactos ni
   reanudación.
5. Las herramientas disponibles están implícitas en comandos y prompts; no
   existe una política central para permisos, rutas, red, Git o aprobación.
6. El loop de Qt no debe bloquearse con llamadas síncronas de modelos o
   procesos largos.

## 3. Evaluación de frameworks

| Criterio | AutoGen | LangGraph | CrewAI |
|---|---|---|---|
| Flujos explícitos y estados | Bueno, orientado a conversaciones entre agentes | Excelente, grafo de estados explícito | Bueno, basado en crews y tasks |
| Checkpoints y reanudación | Requiere diseñar bastante infraestructura | Capacidad central del enfoque | Más limitado y dependiente de la integración |
| Human-in-the-loop | Posible, pero hay que modelarlo | Natural mediante interrupciones y estados | Posible, pero menos preciso para workflows complejos |
| Herramientas y control de permisos | Flexible, requiere convenciones propias | Flexible, fácil de encapsular en nodos | Sencillo para herramientas de agentes |
| Adaptación a PyQt/QProcess | Requiere puente async/sync | Requiere puente async/sync | Requiere puente async/sync |
| Proveedores heterogéneos y CLI existentes | Posible | Posible mediante nodos/adaptadores | Posible, con mayor abstracción orientada a roles |
| Depuración y trazabilidad | Conversaciones pueden ser difíciles de inspeccionar | Cada transición puede quedar como evento/checkpoint | Buena lectura conceptual, menor control fino |
| Complejidad inicial | Media/alta | Media | Baja/media |
| Encaje con WebAgent | Parcial: favorece conversación multiagente | Alto: WebAgent necesita workflows controlados | Medio: útil para equipos de agentes, no imprescindible |

### Recomendación

Elegir **LangGraph como motor de orquestación**, con una primera versión
deliberadamente pequeña y con adaptadores propios para los agentes actuales.

La razón principal no es disponer de más agentes conversando, sino poder
representar de forma explícita:

- análisis, planificación, ejecución, verificación y revisión;
- pausas para autenticación, aprobación o conflictos Git;
- reintentos y rotación de perfiles cuando hay cuota agotada;
- persistencia y reanudación de una ejecución después de cerrar WebAgent;
- eventos y artefactos que la interfaz pueda mostrar sin interpretar texto
  libre del modelo.

**AutoGen** sería una alternativa si el objetivo prioritario fuera una
conversación dinámica entre varios agentes especializados. **CrewAI** sería
adecuado para un MVP muy orientado a roles (“investigador”, “programador”,
“revisor”), pero ofrece menos control que LangGraph sobre el estado durable y
las pausas operativas que ya necesita WebAgent. No se recomienda incorporar
los tres: aumentaría dependencias, superficie de configuración y dificultad
de depuración.

## 4. Arquitectura propuesta

Crear un paquete `agent_framework/` independiente de PyQt:

```text
agent_framework/
├── __init__.py
├── models.py          # Run, RunState, AgentMessage, ToolCall, Artifact
├── graph.py           # construcción del StateGraph y nodos
├── runtime.py         # ejecución async y puente de cancelación
├── providers.py       # interfaz común y adaptadores de modelos/CLI
├── tools.py           # herramientas permitidas y validación de argumentos
├── checkpoints.py     # persistencia de estado y reanudación
├── events.py          # eventos tipados para la UI y logs
├── policies.py        # permisos, límites y aprobación humana
└── errors.py          # errores normalizados y clasificación de reintentos
```

### Estado mínimo del grafo

El estado serializable debe contener, como mínimo:

```text
run_id
task_id
profile_id
collection_id
workspace
branch
goal
plan
messages
pending_approval
tool_results
changed_files
verification_results
retry_count
status
error
created_at / updated_at
```

No se deben guardar API keys, cookies, tokens ni el contenido completo de
sesiones del navegador dentro del checkpoint.

### Flujo inicial

1. `intake`: valida la tarea, perfil, Colección y carpeta de trabajo.
2. `inspect_repo`: obtiene rama, estado Git, archivos relevantes y límites de
   contexto sin modificar el repositorio.
3. `plan`: un modelo produce un plan estructurado y enumera herramientas
   requeridas.
4. `approve_plan`: pausa y solicita aprobación si la política lo exige.
5. `execute`: aplica cambios usando herramientas controladas o delega a
   Copilot/Codex CLI en la rama autorizada.
6. `verify`: ejecuta verificaciones permitidas por configuración.
7. `review`: resume diff, commits, fallos y archivos modificados.
8. `complete` o `failed`: persiste el resultado y habilita reanudación,
   reintento o cancelación.

Los nodos deben ser deterministas respecto del estado y devolver eventos
tipados; la UI no debe depender de analizar strings de la salida del modelo
para saber en qué etapa está una ejecución.

## 5. Adaptación de los agentes actuales

Definir una interfaz conceptual común:

```python
class AgentProvider(Protocol):
    provider_id: str

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        ...

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        ...
```

Implementaciones iniciales:

- `CopilotCliProvider`: usa `QProcess` detrás de un adaptador async o un
  worker dedicado; conserva `COPILOT_HOME` por perfil, device flow y rotación.
- `CodexCliProvider`: conserva la limpieza de commits y las restricciones de
  rama existentes.
- `GeminiProvider`: extrae el cliente HTTP de `task_manager.py` y
  `scripts/gemini_agent.py` a una implementación reutilizable.
- `GroqProvider`: extrae el cliente HTTP de `scripts/groq_agent.py` y
  normaliza respuestas compatibles con OpenAI.

La selección de proveedor debe vivir en configuración por carpeta/Colección,
no en prompts codificados en `window.py`. La migración debe mantener los
comandos personalizados existentes de `AgentConfigStore`.

## 6. Herramientas y seguridad

Las herramientas deben ser funciones tipadas, con validación antes de
ejecutar:

- inspección: listar archivos, leer archivo con límite de tamaño, buscar
  símbolos, consultar `git status`, `git diff` y `git log`;
- edición: aplicar parche dentro de `workspace`, sin aceptar rutas fuera de
  la carpeta autorizada;
- verificación: comandos declarados en configuración y con timeout;
- Git: crear/cambiar ramas, preparar diff y crear commits solo con una
  aprobación explícita, salvo una política configurada;
- website tools: exponer crawler, analyzer y scraper existentes con límites
  de dominio, páginas, workers y robots.txt;
- terminal: no exponer una shell libre al grafo; reutilizar una allowlist
  equivalente a `validate_autorun_command` y ampliarla solo de forma
  explícita.

Medidas obligatorias:

- mantener separación de `COPILOT_HOME`, API keys y perfiles;
- eliminar secretos de logs, mensajes, checkpoints y errores;
- limitar tiempo, tamaño de salida, archivos leídos y llamadas por ejecución;
- bloquear path traversal, enlaces simbólicos fuera del workspace y comandos
  encadenados;
- registrar quién aprobó cada acción y con qué perfil;
- cancelar procesos hijos al cancelar una ejecución;
- no ejecutar acciones de escritura mientras el working tree tenga cambios
  ajenos sin una confirmación clara.

## 7. Integración con PyQt6

No ejecutar el grafo en el hilo principal. Introducir un `AgentRunController`
que:

1. recibe `RunRequest` desde `window.py` o la bandeja de tareas;
2. lanza la ejecución en `QThread`, `QThreadPool` o un worker asyncio
   dedicado;
3. emite señales Qt para `run_started`, `state_changed`, `event`,
   `approval_required`, `run_finished` y `run_failed`;
4. ofrece `cancel(run_id)`, `approve(run_id, decision)` y `resume(run_id)`;
5. conserva `agent_console.py` como vista de salida, no como orquestador.

La consola debe mostrar eventos estructurados y stdout/stderr de los CLI,
manteniendo pestañas, stdin, logs y el historial. El diálogo de
`AIAgentsDialog` debería quedar para configuración y autenticación; la
ejecución duplicada debe migrarse gradualmente al controlador común.

## 8. Persistencia y compatibilidad

Mantener los JSON existentes y añadir un almacén de ejecuciones, inicialmente
en `%APPDATA%\IARA\WebAgent\agent_runs\`:

```text
agent_runs/<run_id>.json       # metadata, estado y checkpoint
agent_runs/<run_id>.events     # eventos append-only
agent_runs/<run_id>/artifacts/ # planes, reportes y diffs
```

Evolución de `task_manager.py`:

- agregar `run_id`, `status`, `started`, `finished`, `error` y `result`;
- conservar tareas antiguas sin `run_id` mediante migración tolerante;
- diferenciar estado de la tarea (`pending/completed/cancelled`) del estado de
  la ejecución (`queued/running/waiting_approval/failed/completed`);
- guardar `collection_id` y `profile_id` en el momento de iniciar una corrida,
  para que cambiar la selección actual no altere una ejecución existente.

En una fase posterior, si el volumen o la concurrencia lo justifican, migrar
el almacén de ejecuciones a SQLite. No introducir una base de datos solo por
adoptar LangGraph.

## 9. Fases de implementación

### Fase 0 — Contratos y reducción de riesgo

- fijar versión compatible de Python y LangGraph;
- documentar límites de ejecución, permisos y política de aprobación;
- crear `models.py`, `events.py`, `errors.py` y pruebas de serialización;
- identificar y no romper configuraciones existentes de `codex_config.json`;
- decidir una única ruta de ejecución nueva y marcar el diálogo antiguo como
  compatibilidad durante la transición.

**Salida:** contratos estables, sin cambio visible en el flujo actual.

### Fase 1 — Runtime y ejecución de un proveedor

- implementar `AgentRunController` y el puente Qt/async;
- implementar `checkpoints.py` con escritura atómica y recuperación;
- implementar un grafo lineal `intake -> inspect -> plan -> review`;
- integrar primero Gemini o Groq como proveedor sin escritura;
- mostrar eventos y estados en `AgentConsolePanel`.

**Salida:** una tarea de consulta puede ejecutarse, cancelarse, reanudarse y
verse en el historial sin modificar archivos.

### Fase 2 — Herramientas seguras y workspace

- extraer operaciones reutilizables de `file_ops.py`;
- implementar lectura, búsqueda, parche y Git como herramientas tipadas;
- añadir límites, timeouts, allowlist y aprobación para escritura;
- producir artefactos de plan, diff y reporte de verificaciones.

**Salida:** un flujo puede proponer y aplicar un cambio controlado en una rama
de trabajo.

### Fase 3 — Integración de Copilot/Codex

- encapsular `QProcess`, autenticación device flow, stdin y salida en
  `CopilotCliProvider`/`CodexCliProvider`;
- mover rotación de cuota a una política del runtime;
- conservar `COPILOT_HOME` por perfil y no mezclar credenciales;
- soportar comandos personalizados existentes como modo legacy;
- migrar autorun al nodo `verify` con la misma validación de seguridad.

**Salida:** los flujos de código usan los agentes actuales sin perder
funcionalidad ni aislamiento.

### Fase 4 — Bandeja de tareas, Colecciones y revisión

- iniciar una corrida desde `window.py` y `new_tab_page.py`;
- asociar automáticamente `task_id`, `collection_id`, `profile_id` y
  workspace;
- añadir UI de aprobaciones, cancelación, reintento y reanudación;
- enriquecer `agent_runs.html` con estados, eventos, artefactos y diff;
- permitir abrir la corrida asociada desde una tarea histórica.

**Salida:** flujo completo desde tarea hasta revisión de cambios.

### Fase 5 — Flujos multiagente opcionales

- incorporar roles solo si un caso real lo justifica: planificador,
  implementador, verificador y revisor;
- limitar la comunicación a mensajes y artefactos estructurados;
- evitar conversaciones abiertas sin presupuesto, límite de iteraciones o
  condición de finalización;
- medir costo, latencia, tasa de reintentos y calidad antes de activar por
  defecto.

**Salida:** colaboración multiagente controlada, no una dependencia
innecesaria para las tareas simples.

## 10. Validación

Agregar pruebas unitarias y de integración para:

- serialización, migración y recuperación de checkpoints;
- transiciones válidas e inválidas del grafo;
- cancelación, timeout, reintento y reanudación;
- aislamiento de perfiles y ausencia de secretos en logs/checkpoints;
- rechazo de path traversal, comandos encadenados y workspaces inválidos;
- working tree sucio, ramas inexistentes, conflictos y rollback Git;
- errores HTTP de Gemini/Groq y salida inesperada de CLI;
- rotación de Copilot sin duplicar ejecuciones;
- señales Qt emitidas en el orden correcto sin bloquear la interfaz;
- compatibilidad con tareas y configuraciones creadas por versiones anteriores.

Casos manuales de aceptación:

1. Cerrar y reabrir WebAgent durante una pausa de aprobación y continuar la
   misma corrida.
2. Ejecutar una tarea en dos perfiles y comprobar que no comparten
   `COPILOT_HOME`, tokens ni logs sensibles.
3. Rechazar un plan y comprobar que no se modifica el repositorio.
4. Forzar un error de verificación y comprobar que se conserva el diff y se
   ofrece reintento.
5. Confirmar que el uso legacy de Copilot/Codex sigue funcionando mientras se
   migra cada proveedor.

## 11. Dependencias y decisión de adopción

La dependencia nueva mínima sería LangGraph y su soporte de checkpoint
compatible con el runtime elegido. Los clientes HTTP y adaptadores CLI deben
reutilizar la biblioteca estándar y el código existente antes de agregar
SDKs adicionales. Las dependencias opcionales de observabilidad deben
incorporarse solo después de definir qué eventos se almacenan localmente.

La adopción se considera exitosa cuando el flujo lineal de la Fase 1 ofrece
estado durable, cancelación, reanudación y aprobación, y cuando Copilot/Codex
pueden integrarse sin que la UI conozca detalles del framework. Si LangGraph
no puede funcionar de forma estable con el modelo de concurrencia elegido,
la alternativa de respaldo es conservar los contratos propios y reemplazar
solo `graph.py` por una implementación explícita sin framework; no acoplar
el resto del sistema a AutoGen o CrewAI.

## 12. Resultado esperado

WebAgent debe terminar con tres capas claras:

1. **Interfaz:** PyQt6, consola, bandeja de tareas, historial y aprobaciones.
2. **Orquestación:** LangGraph, estado durable, políticas, eventos y
   reanudación.
3. **Capacidades:** proveedores Gemini/Groq/Copilot/Codex, herramientas de
   archivos/Git/website tools y almacenamiento de perfiles.

Esta separación permite sumar proveedores o flujos sin duplicar lógica de
autenticación, procesos, Git y seguridad, y deja a LangGraph en el lugar que
más valor aporta al proyecto: coordinar estados y decisiones auditables.
