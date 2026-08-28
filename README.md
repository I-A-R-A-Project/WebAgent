# IA Browser

IA Browser es un navegador de escritorio para trabajar con agentes de
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
de agente y cuentas autenticadas independientes. Un perfil puede contener la
cuenta correspondiente de Copilot, Codex, GitHub, Claude y Gemini. El perfil
`Default` se usa cuando una Colección no requiere una cuenta específica y no
se puede eliminar.

Cuando un agente agota tokens, cuota o rate limit, IA Browser puede rotar al
siguiente perfil disponible y reintentar la tarea conservando su contexto.
Las credenciales, sesiones e historiales de conversación siguen separados
entre perfiles.

Cuando Git está activo, IA Browser separa el trabajo automáticamente:

- `profile/<id>-raw`: descargas y cambios automáticos.
- `profile/<id>`: trabajo de agentes y cambios revisados.

IA Browser no cambia de rama si hay cambios sin commitear que puedan mezclarse.
Esto evita que una descarga o agente sobrescriba accidentalmente archivos de
otro perfil.

## Colecciones y agentes IA

Las Colecciones son espacios de trabajo del navegador. Cada una puede guardar:

- Carpeta de trabajo y archivos del proyecto.
- URLs, chats, repositorios y páginas necesarias.
- Perfil específico por URL para las páginas que requieren una cuenta
  determinada; una misma Colección puede contener URLs de perfiles distintos.
- Configuración Git y ramas fijas del proyecto (`master` cruda y `main` final).

Si una URL no tiene perfil asignado, se abre con `Default`. La asignación por
URL permite abrir cada página con las cookies y la cuenta correctas sin
cambiar otras URLs o Colecciones.

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
correspondiente. IA Browser no copia cookies ni tokens entre perfiles.

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

Copilot CLI corre dentro de IA Browser mediante una terminal integrada.

- Cada perfil usa su propio `COPILOT_HOME` e historial de Copilot.
- El login usa device flow y abre GitHub en una pestaña del perfil correcto.
- IA Browser detecta el código de dispositivo y lo completa automáticamente.
- La pestaña de autenticación se cierra al detectar `Signed in successfully`.
- Si falta autenticación, el login comienza automáticamente.
- Si se detecta un límite de cuota, puede rotar al siguiente perfil y reintentar.
- El prompt recibe estado Git, diff sin commitear y diff del commit reciente.

La separación de credenciales depende del almacenamiento usado por Copilot CLI.
IA Browser no guarda tokens propios ni copia credenciales entre perfiles.

### AnyAPI

AnyAPI ofrece acceso unificado a modelos de OpenAI, Anthropic, Google, Meta y
otros mediante `https://api.anyapi.ai/v1`. El agente AnyAPI aparece en
**Agentes IA** como proveedor opcional: requiere una API key y consulta
`chat/completions`; no modifica archivos por sí mismo.

Documentación: https://docs.anyapi.ai/

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
que un agente visualice AST y pruebe reglas estructurales. IA Browser no lo
inicia automáticamente.

## Instalación y ejecución

Requiere Python, PyQt6 y PyQt6-WebEngine:

```bash
pip install -r requirements.txt
python main.py
```

Los imports son locales; ejecutar `python main.py` desde la carpeta `IA`.

## Archivos y módulos principales

```text
IA/
├── main.py                 # Ventana, pestañas, perfiles y navegación
├── profiles.py             # Perfiles aislados y carpetas de trabajo
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
Las tareas se guardan por perfil; si existe `ANYAPI_API_KEY`, AnyAPI intenta
asociarlas con una Colección y cada tarea ofrece un enlace explícito para
abrirla en Copilot. Las tareas sin coincidencia preguntan antes de crear una
Colección nueva.

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
limitar profundidad y páginas, muestra el informe dentro de IA Browser y
permite guardar el crawl en JSON.

## Datos persistentes

IA Browser guarda datos en `~/.ia_browser/`:

```text
profiles.json                 # Perfiles, carpetas y preferencias
collections.json              # Colecciones y marcadores
session.json                  # Pestañas restaurables
copilot_profiles/<id>/        # Estado de Copilot por perfil
profiles/<id>/storage/        # Cookies y almacenamiento Chromium
profiles/<id>/cache/          # Cache Chromium
```

No incluir estas carpetas en commits públicos si contienen sesiones,
credenciales, cookies o datos personales.
