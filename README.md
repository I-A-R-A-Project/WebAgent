# IA Browser

Navegador de escritorio PyQt6 con pestañas, perfiles aislados y "Colecciones"
(grupos de marcadores multi-perfil con carpeta de descarga y versionado git).

## Estructura

```
ia_browser/
├── main.py               # Ventana principal (IABrowser) + entry point
├── profiles.py             # ProfileManager + NewProfileDialog
├── collections_manager.py    # CollectionManager + NewCollectionDialog + SaveToCollectionDialog
├── downloads.py                # DownloadDialog (descarga + extracción de comprimidos)
├── codex_manager.py               # Codex Manager: crear repo GitHub + limpiar historial con Codex CLI
├── web_engine.py                     # CustomWebEnginePage (popups OAuth + vista custom de carpetas)
├── file_ops.py                         # GitVersioning + FileOps (papelera, split/join, helpers de branch)
└── requirements.txt
```

## Instalación

```bash
pip install -r requirements.txt
```

## Uso

```bash
python main.py
```

Todos los archivos deben estar en la misma carpeta (los imports son relativos
al directorio, sin paquete formal) — así podés correr `python main.py`
directamente sin instalar nada más.

## Datos guardados

Todo se guarda en `~/.ia_browser/`:

```
~/.ia_browser/
├── profiles.json      # Perfiles (nombre, carpeta, zoom, git)
├── collections.json     # Colecciones y sus marcadores
├── session.json            # Última sesión (pestañas abiertas)
└── profiles/<id>/             # storage + cache de cada perfil (Chromium)
    ├── storage/
    └── cache/
```

## Funcionalidad clave por módulo

- **Perfiles**: sesión/cookies/cache aislados por perfil, cada uno con su
  propia carpeta de archivos y zoom. El perfil **Default** no se puede
  eliminar. El botón **"+ Tab"** siempre abre una pestaña con el perfil
  Default; elegir otro perfil en el combo abre una pestaña nueva con ese
  perfil (sin cerrar las demás). Al cambiar de pestaña, el combo se
  sincroniza solo con el perfil de esa pestaña.
- **Colecciones**: grupos de marcadores que pueden mezclar chats de
  distintos perfiles, con carpeta de descarga y versionado git opcionales.
  Se muestran como un árbol desplegable directamente en el sidebar (sin
  ventana modal): cada Colección se expande mostrando sus marcadores +
  un ítem final para abrir/asignar su carpeta.
- **Descargas**: diálogo con vista previa en vivo, detección y extracción
  automática de comprimidos (zip/tar/gz/bz2/rar/7z), y marcado de archivos
  obsoletos para eliminar.
- **OAuth**: `CustomWebEnginePage.createWindow()` abre los popups de login
  (ej. "Continuar con Google") en una ventana separada que comparte el
  mismo perfil que la pestaña de origen.
- **Codex Manager** (menú ⚙ de Perfiles, o click derecho sobre una
  Colección con carpeta asignada): permite conectar la carpeta a un
  repositorio de GitHub que el usuario ya creó a mano — solo pegando su
  URL (HTTPS o SSH), sin manejar tokens (usa la autenticación de git ya
  configurada en el sistema). También permite configurar una rama
  "cruda" (donde caen los commits automáticos de las descargas — con
  clutter de agregar/eliminar el mismo archivo varias veces) y una rama
  "limpia" de destino, y correr Codex CLI (`codex exec`) para que
  analice esa rama cruda y genere commits prolijos y con mensajes
  descriptivos en la rama limpia, sin duplicar mensajes de commit.
  Requiere tener `codex` instalado y autenticado
  (`npm install -g @openai/codex` y `codex login`) — el diálogo avisa
  si no lo encuentra en el PATH.

