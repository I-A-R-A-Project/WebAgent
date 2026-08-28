# Plan de Website Tools

## Estado actual

- Rama de trabajo: `master`.
- Fase 1 completada: crawler HTTP acotado, límites de dominio/profundidad/páginas,
  timeout, cancelación y exportación JSON.
- Fase 2 completada: análisis de títulos, meta descripción, encabezados, errores
  HTTP y enlaces hacia páginas fallidas.
- UI inicial completada: **Vista → Website Tools...**, URL precompletada desde la
  pestaña actual, ejecución en worker y guardado del crawl.
- Downloader MVP iniciado: descarga de páginas de un crawl, límites de bytes,
  rutas seguras, hashes y manifest JSON.
- Sitio local de prueba: `http://127.0.0.1:8765/`.

## Próximas fases

1. **Completar Downloader MVP**
   - Descargar recursos CSS, JavaScript e imágenes seleccionados.
   - Resolver URLs relativas y reescribir enlaces offline.
   - Integrar carpeta temporal y confirmación desde la UI.
   - Confirmación antes de mover resultados a la carpeta final.
2. **Scraper MVP**
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
- Mantener límites de páginas, bytes, redirecciones, concurrencia y timeout.
- Respetar `robots.txt` por defecto.
- No reutilizar cookies o credenciales del perfil sin autorización explícita.
- No ejecutar archivos descargados.
- Mantener la verificación TLS activa salvo pruebas locales explícitas.

## Comandos de prueba

```bash
python scripts/website_tools_test_server.py
python scripts/website_crawl.py http://127.0.0.1:8765/ --output crawl.json --max-depth 1
python scripts/website_analyze.py crawl.json --output report.json
```
