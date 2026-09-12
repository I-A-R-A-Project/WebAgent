# Plan actual de implementación de agentes

Este plan reemplaza temporalmente al diseño exploratorio de
`framework_plan.md`. Mantiene el comportamiento actual de WebAgent y prioriza
cambios pequeños, observables y reversibles.

## Objetivo

Convertir la integración de Copilot en una ejecución trazable y reanudable,
con métricas estructuradas, permisos explícitos y una única ruta de ejecución.
El modo ACP queda preparado como una futura implementación, no como requisito
para la primera fase.

## Fase 1 — Observabilidad y seguridad operativa

Estado: completada. Ya están implementados `--usage-output-file`,
`--max-ai-credits`, el aislamiento de métricas, las exclusiones locales y una
política que no concede acceso global a URLs por defecto.

1. Guardar las estadísticas finales de Copilot con `--usage-output-file` en
   `%APPDATA%\IARA\WebAgent\agent_usage\<profile_id>\`, fuera del repositorio.
2. Asociar cada archivo de uso con el log de WebAgent y registrar sus datos
   principales sin guardar secretos.
3. Mantener el scraping de cuota mensual como dato independiente y tolerante a
   fallos; no usarlo como sustituto de las estadísticas de una ejecución.
4. Incorporar límites configurables por ejecución (`--max-ai-credits`) y
   conservar el control de cancelación de `QProcess`.
5. Reemplazar permisos globales de URL por una política explícita cuando la
   tarea no necesite acceso web. El comando por defecto y la rotación ya no
   añaden `--allow-all-urls`; los comandos personalizados conservan sus
   opciones explícitas.
6. Aclarar `.gitignore` para excluir cachés, logs y artefactos locales.

## Fase 2 — Sesiones y salida estructurada

Estado: completada. Quedó implementado el rastreo de sesión por ejecución,
la salida legible de Copilot y la lectura de métricas estructuradas desde
archivos JSON separados.

1. Asignar `--session-id` o `--name` estable a cada ejecución y guardar la
   relación con el log, perfil, carpeta y tarea.
2. Mantener la salida visual en texto; las métricas estructuradas se guardan
   con `--usage-output-file` sin convertir la consola en JSONL.
3. Mostrar en `agent_runs` los créditos, tokens, duración, modelo y archivo de
   uso asociado.
4. Reservar `--output-format json` para integraciones automatizadas futuras;
   no usarlo como formato predeterminado de la consola interactiva.
5. Centralizar helpers de sesión, uso y permisos en módulos de agentes.

## Fase 3 — ACP y workflows

1. Prototipar un `CopilotAcpProvider` aislado del runner legado.
2. Implementar transporte, eventos, aprobación, cancelación y reanudación ACP.
3. Integrarlo con un flujo de intake, inspección, plan, aprobación, ejecución,
   verificación y revisión.
4. Mantener `copilot -p` como fallback mientras ACP no cubra autenticación,
   perfiles, permisos y recuperación de errores.

## Reglas de implementación

- No guardar tokens, cookies ni prompts completos en archivos de uso.
- No escribir reportes de sesión automáticamente dentro del workspace.
- No activar `--share` automáticamente; será una exportación manual futura.
- No habilitar `--autopilot` sin límite de créditos y confirmación explícita.
- Validar cada fase con las herramientas y pruebas ya presentes en el repo.
