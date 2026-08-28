# Plan de Website Tools

## Estado actual

- Rama de trabajo: `master`.
- Fase 1 completada: crawler HTTP acotado, límites de dominio/profundidad/páginas,
  timeout, cancelación y exportación JSON.
- Fase 2 completada: análisis de títulos, meta descripción, encabezados, errores
  HTTP y enlaces hacia páginas fallidas.
- UI inicial completada: **Vista → Website Tools...**, URL precompletada desde la
  pestaña actual, ejecución en worker y guardado del crawl.
- Downloader MVP completado: descarga de páginas y recursos CSS/JS/imagen
  enlazados, límites de bytes, rutas seguras, hashes, manifest JSON y
  reescritura de enlaces HTML para navegación offline. La UI permite elegir
  carpeta y confirma la descarga antes de escribir.
- Scraper MVP iniciado: reglas JSON con selectores CSS simples, extracción de
  texto/atributos y exportación JSON/JSONL/CSV/SQLite.
- UI de crawl actualizada con estado en vivo de páginas procesadas y profundidad.
- Whitelist/blacklist de URLs y de contenido HTML integrada en crawler, CLI y UI.
- Workers concurrentes configurables para el crawler, limitados por CPU y a 16.
- El crawler respeta `robots.txt` por defecto y permite omitirlo explícitamente
  solo para entornos controlados.

## Próximas fases

1. **Completar Downloader MVP**
   - [x] Integrar carpeta destino y confirmación desde la UI.
   - [x] Resolver URLs relativas y reescribir enlaces HTML offline.
   - [ ] Usar carpeta temporal y mover resultados a la carpeta final tras confirmar.
2. **Completar Scraper MVP**
   - [x] Exportar JSON, JSONL, CSV y SQLite.
   - Reglas CSS/XPath en JSON/YAML.
   - Extraer texto, atributos, enlaces, imágenes y tablas.
   - Paginación y exportación CSV/JSON/JSONL/SQLite.
3. **Contenido dinámico**
   - Usar QWebEngine solo cuando el sitio requiera JavaScript.
   - Esperar selectores, scroll y paginación controlada.
4. **Selector visual**
   - Seleccionar elementos desde una página.
   - Generar y probar selectores antes de ejecutar un trabajo.
5. **IA y comparación**
   - Enviar informes compactos a Copilot/AnyAPI.
   - Comparar dos crawls y generar tareas o documentación.

## Reglas de seguridad

- Restringir dominio por defecto y bloquear localhost/redes privadas en trabajos
  remotos.
- Permitir listas explícitas de URL y selectores de contenido para limitar el
  alcance del trabajo.
- Mantener límites de páginas, bytes, redirecciones, concurrencia y timeout.
- Respetar `robots.txt` por defecto.
- No reutilizar cookies o credenciales del perfil sin autorización explícita.
- No ejecutar archivos descargados.
- Mantener la verificación TLS activa salvo pruebas locales explícitas.

## Comandos de prueba

```bash
python scripts/website_crawl.py https://example.com/ --output crawl.json --max-depth 1
python scripts/website_analyze.py crawl.json --output report.json
```
