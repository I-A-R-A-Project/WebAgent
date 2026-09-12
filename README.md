# WebAgent

WebAgent es un navegador de escritorio para trabajar con agentes de
inteligencia artificial desde un entorno controlado. Su objetivo no es ser
solo un navegador web: cada perfil representa una cuenta separada y mantiene
sus propias sesiones, archivos, cookies, credenciales de agentes y ramas Git.

## Para qué sirve

- Navegar sitios web y chats de IA con pestañas.
- Mantener cuentas independientes de Copilot, Codex, GitHub, Claude y Gemini.
- Rotar entre cuentas cuando una agota sus tokens o cuota disponible.
- Asociar Colecciones con carpetas de trabajo y URLs necesarias.
- Descargar, extraer, editar y versionar archivos desde el navegador.
- Ejecutar Codex y Copilot CLI sobre un repositorio sin abrir una consola
  separada.
- Ejecutar refactors estructurales con ast-grep sin consumir tokens del agente.
- Revisar el historial y los diffs Git antes de continuar una tarea.
- Trabajar con Colecciones que reúnen páginas y carpetas de varios perfiles.

## Flujo recomendado

1. Crear un perfil por cuenta de servicios IA.
2. Autenticar Copilot, Codex, GitHub, Claude y Gemini en el perfil que
   corresponda.
3. Crear una Colección para cada proyecto o espacio de trabajo.
4. Asignar la carpeta de trabajo y guardar las URLs necesarias.
5. Asignar un perfil a cada URL que necesite una cuenta específica; las URLs
   sin asignación usan `Default`.
6. Ejecutar agentes y revisar el diff antes de aceptar cambios.

## Perfiles y seguridad

Cada perfil tiene cookies, cache y sesión aisladas. Los archivos y descargas
usan una única carpeta común configurable desde **Configuración → Agentes IA**.
Las sesiones y cuentas autenticadas siguen siendo independientes. Un perfil puede contener la
cuenta correspondiente de Copilot, Codex, GitHub, Claude y Gemini. El perfil
`Default` se usa cuando una Colección no requiere una cuenta específica y no
se puede eliminar.

Cuando un agente agota tokens, cuota o rate limit, WebAgent puede rotar al
siguiente perfil disponible y reintentar la tarea conservando su contexto.
Las credenciales, sesiones e historiales de conversación siguen separados
entre perfiles.

Cuando Git está activo, WebAgent usa `master` para cambios crudos y `main`
para el trabajo de agentes y cambios revisados.

WebAgent no cambia de rama si hay cambios sin commitear que puedan mezclarse.
Esto evita que una descarga o agente sobrescriba accidentalmente archivos de
otro perfil.

## Colecciones y agentes IA

Las Colecciones son espacios de trabajo del navegador. Cada una puede guardar:

- Carpeta de trabajo y archivos del proyecto.
- URLs, chats, repositorios y páginas necesarias.
- Perfil específico por URL para las páginas que requieren una cuenta
  determinada; una misma Colección puede contener URLs de perfiles distintos.
- Configuración Git y ramas fijas del proyecto (`master` cruda y `main` final).

Las pestañas normales se abren con `Default`; solo los flujos explícitos de
autenticación de un agente abren una pestaña con otro perfil.

La configuración **Agentes IA** se abre desde **Configuración → Agentes IA...**.
El diálogo permite seleccionar y administrar perfiles, cambiar la carpeta
común de archivos y configurar los agentes. La ejecución y la salida viven en
la consola inferior plegable; las Colecciones ya no abren la configuración de
agentes.
La ventana es no modal, por lo que se puede seguir usando el navegador
mientras un agente trabaja.

### Cuentas compatibles

Cada perfil puede tener una cuenta independiente de:

- GitHub.
- Copilot.
- Codex.
- Claude.
- Gemini.

El inicio de sesión se realiza dentro de las pestañas del perfil
correspondiente. WebAgent no copia cookies ni tokens entre perfiles.

### Codex

Codex analiza la rama `master` de cambios automáticos y puede reorganizarla en
commits descriptivos sobre la rama final `main`. Se puede configurar el
repositorio remoto, pero no las ramas por perfil.

Instalación:

```bash
npm install -g @openai/codex
codex login
```

### Copilot

Copilot CLI corre dentro de WebAgent mediante una terminal integrada.

- Cada perfil usa su propio `COPILOT_HOME` e historial de Copilot.
- El login usa device flow y abre GitHub en una pestaña del perfil correcto.
- WebAgent detecta el código de dispositivo y lo completa automáticamente.
- La pestaña de autenticación se cierra al detectar `Signed in successfully`.
- Si falta autenticación, el login comienza automáticamente.
- Si se detecta un límite de cuota, puede rotar al siguiente perfil y reintentar.
- El prompt recibe estado Git, diff sin commitear y diff del commit reciente.

La separación de credenciales depende del almacenamiento usado por Copilot CLI.
WebAgent no guarda tokens propios ni copia credenciales entre perfiles.

### Gemini y Groq

Gemini y Groq aparecen en **Agentes IA** como proveedores opcionales. Gemini
usa la API de Google AI Studio y Groq usa su API compatible con OpenAI; ambos
ejecutan prompts sin modificar archivos por sí mismos. Las API keys se guardan
por perfil en la configuración local de agentes.

- Gemini: `GEMINI_API_KEY` — https://aistudio.google.com/u/3/docs
- Groq: `GROQ_API_KEY` — https://console.groq.com/docs/overview

### Refactors estructurales

El script `scripts/refactor_ast_grep.py` permite buscar y reemplazar por
estructura sintáctica, en lugar de depender de regex:

```bash
python scripts/refactor_ast_grep.py . --pattern "console.log($$$ARGS)" --lang ts
python scripts/refactor_ast_grep.py . --pattern "var $A = $B" --rewrite "let $A = $B" --lang js --dry-run
```

Instalación opcional:

```bash
npm install --global @ast-grep/cli
```

El servidor `ast-grep-mcp` también puede configurarse en un cliente MCP para
que un agente visualice AST y pruebe reglas estructurales. WebAgent no lo
inicia automáticamente.

### Grabar una ventana

En Windows se puede grabar una ventana visible con FFmpeg usando el script
incluido. El script usa `ffmpeg.exe` de la raíz del proyecto si está
disponible, o un ejecutable encontrado en `PATH`:

```bash
python scripts/record_window.py
```

Por defecto busca la ventana cuyo título es `WebAgent`, guarda el vídeo en
`recordings/window_YYYYMMDD_HHMMSS.mp4` y continúa hasta pulsar `Ctrl+C`.
También permite seleccionar otra ventana y limitar la duración:

```bash
python scripts/record_window.py --title "Calculadora" --duration 30 --output captura.mp4
python scripts/record_window.py --list-windows
```

## Instalación y ejecución

Requiere Python, PyQt6 y PyQt6-WebEngine:

```bash
pip install -r requirements.txt
python main.py
```

Los imports son locales; ejecutar `python main.py` desde la carpeta `WebAgent`.

## Archivos y módulos principales

```text
WebAgent/
├── main.py                 # Ventana, pestañas, perfiles y navegación
├── profiles.py             # Perfiles aislados y carpeta común de archivos
├── collections_manager.py  # Colecciones y marcadores multi-perfil
├── downloads.py            # Descargas, reemplazo y extracción
├── ai_manager.py           # Terminal integrada y gestión de agentes
├── new_tab_page.py         # Búsqueda Google y bandeja de tareas
├── task_manager.py         # Persistencia y clasificación de tareas
├── scripts/website_tools/   # Crawling y extracción web controlada
├── web_engine.py           # Popups OAuth y vistas locales
├── file_ops.py             # Git, ramas y operaciones de archivos
└── requirements.txt        # Dependencias Python
```

Cada pestaña nueva incluye búsqueda de Google y una bandeja local de tareas.
Las tareas de la bandeja se guardan por perfil. Cada tarea se relaciona con una
Colección mediante Gemini, usando el índice, los Tags, los README y la jerarquía;
Copilot queda como respaldo si Gemini no está disponible. Sólo se crea una
Colección nueva cuando ninguno de los dos agentes identifica una Colección
existente.

### Website Tools

La primera fase incluye un crawler HTTP acotado y reutilizable:

```bash
python scripts/website_crawl.py https://example.com --output crawl.json --max-depth 2 --max-pages 100
```

Por defecto limita profundidad y cantidad de páginas, sigue solo enlaces
HTTP/HTTPS del dominio permitido y exporta resultados compactos en JSON.
Las solicitudes pueden ejecutarse con workers concurrentes mediante
`--workers`; el valor se limita al número de núcleos disponibles y a 16.
Se puede restringir explícitamente el alcance con patrones URL y selectores
HTML:

```bash
python scripts/website_crawl.py https://example.com/ \
  --output crawl.json --allow-url "https://example.com/*" \
  --allow-selector "#main-content" --block-selector ".ads,footer"
```

`--allow-url` y `--block-url` aceptan patrones glob y pueden repetirse.
`--allow-selector` limita enlaces, títulos, metadatos y encabezados a los
elementos seleccionados; `--block-selector` excluye esos elementos y sus
descendientes. Los selectores soportados son `tag`, `#id`, `.clase` y
`[atributo=valor]`. El crawler respeta `robots.txt` por defecto; usar
`--ignore-robots` únicamente en entornos controlados.

El informe técnico se genera en una segunda etapa, sin volver a descargar el
sitio:

```bash
python scripts/website_analyze.py crawl.json --output report.json
```

Detecta errores HTTP, errores de solicitud, títulos ausentes o duplicados,
meta descripciones ausentes, páginas sin encabezados y enlaces hacia páginas
con error.

El Downloader MVP descarga las páginas de un crawl en una carpeta controlada y
genera `website-manifest.json` con estado, tamaño y SHA-256:

```bash
python scripts/website_download.py crawl.json --output sitio-descargado --max-pages 25
```

El Scraper MVP acepta una regla JSON con selector de elementos y campos de
texto o atributos, y puede exportar JSON, JSONL, CSV o SQLite.
Las reglas pueden incluir `next_selector` y `max_pages` para seguir una
paginación limitada. Para comparar ejecuciones:

```bash
python scripts/website_compare.py crawl-before.json crawl-after.json --output comparison.json
```

La implementación se probó con sitios de práctica públicos como Books to
Scrape, Quotes to Scrape y Scrapethissite, usando límites pequeños y
respetando `robots.txt`.

También podés abrir **Vista → Website Tools...** desde la interfaz. El diálogo
precompleta la URL de la pestaña actual, permite elegir el tipo de operación,
limitar profundidad y páginas, muestra el informe dentro de WebAgent y
permite guardar el crawl en JSON.

### Capturar responses en vivo con CDP

WebAgent expone el endpoint local de Chrome DevTools Protocol en el puerto
`9222`. Para guardar las respuestas de red de la pestaña activa, abrí
**Configuración → Capturar respuestas de la pestaña (HAR)...**, elegí un
archivo `.har` y dejá la captura activa mientras navegás o interactuás con la
página (por ejemplo, para registrar las respuestas de una timeline de X/Twitter).
Cada response se escribe en el HAR automáticamente, incluyendo headers, URL y
body cuando Chromium lo permite. Usá la misma opción para detener la captura.

La dependencia `websocket-client` es necesaria para la conexión CDP:

```bash
pip install -r requirements.txt
```

### Archivar tweets de una response

El script `scripts/twitter_archive.py` convierte una response JSON de la
timeline de X/Twitter en datos pequeños y una página local con búsqueda,
filtro por autor, métricas, medios y enlaces originales:

```bash
python scripts/twitter_archive.py test/responses_content/response-000155.json
```

También puede recibir una carpeta: procesa todos sus archivos `.json` válidos y
combina los tweets sin duplicarlos, aunque no usen el prefijo `response-`:

```bash
python scripts/twitter_archive.py test/responses_content --output twitter_archive
```

Abrí `twitter_archive/index.html` en el navegador. Las imágenes se muestran
desde sus URLs originales de X; si una URL deja de estar disponible, el texto
y los metadatos siguen guardados en `tweets.json`.

## Datos persistentes

WebAgent guarda datos en `%APPDATA%\IARA\WebAgent\`:

```text
profiles.json                 # Perfiles y preferencias
files_dir.txt                # Carpeta común de archivos y descargas
collections.json              # Colecciones y marcadores
session.json                  # Pestañas restaurables
copilot_profiles/<id>/        # Estado de Copilot por perfil
profiles/<id>/storage/        # Cookies y almacenamiento Chromium
profiles/<id>/cache/          # Cache Chromium
task_agent_context/           # Datos y copias de README para "Agregar tarea"
```

No incluir estas carpetas en commits públicos si contienen sesiones,
credenciales, cookies o datos personales.

Al usar **Agregar tarea**, WebAgent actualiza `task_agent_context/` con un
`collections.json` y copias de los README de cada Colección. Copilot se
ejecuta desde ese directorio para clasificar la tarea con acceso explícito a
la información de las Colecciones, y luego se mueve a la carpeta de la
Colección elegida para realizar el trabajo.
