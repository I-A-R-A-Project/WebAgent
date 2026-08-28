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
- Pruebas externas realizadas con Books to Scrape, Quotes to Scrape,
  Scrapethissite y JSONPlaceholder: crawl, análisis, scraping y descarga
  acotada respondieron correctamente.

## Próximas fases

1. **Completar Downloader MVP**
   - [x] Integrar carpeta destino y confirmación desde la UI.
   - [x] Resolver URLs relativas y reescribir enlaces HTML offline.
   - [x] Usar carpeta temporal y mover resultados a la carpeta final tras confirmar.
2. **Completar Scraper MVP**
   - [x] Exportar JSON, JSONL, CSV y SQLite.
   - [x] Extraer texto, atributos y contenido anidado para enlaces, imágenes y tablas.
   - [x] Paginación limitada mediante `next_selector`.
   - [x] Exportación CSV/JSON/JSONL/SQLite.
   - [ ] XPath en JSON/YAML (requiere motor opcional).
3. **Extensiones opcionales**
   - Renderizado dinámico con QWebEngine: pendiente, requiere un worker de
     navegador aislado, espera de selectores y políticas explícitas de cookies.
   - XPath en reglas: pendiente, requiere incorporar un motor XPath.
   - Selector visual: pendiente, requiere interacción directa con una pestaña
     QWebEngine y generación de selectores.
4. **IA y comparación**
   - Enviar informes compactos a Copilot/AnyAPI.
   - [x] Comparar dos crawls y exportar diferencias.

## Estado de la primera versión

La primera versión funcional queda completa para sitios HTML estáticos y APIs
HTTP, con límites, robots.txt, whitelists/blacklists, concurrencia controlada,
descarga offline, extracción paginada y comparación. Las extensiones anteriores
se mantienen separadas para no introducir dependencias ni reutilizar
credenciales del navegador sin autorización explícita.

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
