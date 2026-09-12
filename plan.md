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

Estado: en progreso. Ya están implementados `--usage-output-file`,
`--max-ai-credits`, el aislamiento de métricas y las exclusiones locales.

1. Guardar las estadísticas finales de Copilot con `--usage-output-file` en
   `%APPDATA%\IARA\WebAgent\agent_usage\<profile_id>\`, fuera del repositorio.
2. Asociar cada archivo de uso con el log de WebAgent y registrar sus datos
   principales sin guardar secretos.
3. Mantener el scraping de cuota mensual como dato independiente y tolerante a
   fallos; no usarlo como sustituto de las estadísticas de una ejecución.
4. Incorporar límites configurables por ejecución (`--max-ai-credits`) y
   conservar el control de cancelación de `QProcess`.
5. Reemplazar permisos globales de URL por una política explícita cuando la
   tarea no necesite acceso web.
6. Aclarar `.gitignore` para excluir cachés, logs y artefactos locales.

## Fase 2 — Sesiones y salida estructurada

Estado: en progreso. Ya están implementados los identificadores de sesión y
la lectura de métricas estructuradas en el historial.

1. Asignar `--session-id` o `--name` estable a cada ejecución y guardar la
   relación con el log, perfil, carpeta y tarea.
2. Evaluar `--output-format json` para Copilot y procesar JSONL sin depender de
   texto libre para detectar finalización, errores o estadísticas.
3. Mostrar en `agent_runs` los créditos, tokens, duración, modelo y archivo de
   uso asociado.
4. Centralizar helpers de sesión, uso y permisos en módulos de agentes.

## Fase 3 — Consolidación de la ejecución

Estado: iniciada. El diálogo de agentes ya puede delegar tareas de Copilot al
runner central de `AgentConsolePanel`; queda retirar gradualmente la ruta Qt
duplicada después de migrar sus usos restantes.

1. Hacer que `AIAgentsDialog` delegue la ejecución en `AgentConsolePanel` o
   extraer un runner común, eliminando la lógica duplicada de `ai_manager.py`.
2. Mantener compatibilidad con comandos personalizados de `AgentConfigStore`.
3. Migrar respuestas y errores a eventos tipados para que la UI no dependa de
   regex sobre la salida.

## Fase 4 — ACP y workflows

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
