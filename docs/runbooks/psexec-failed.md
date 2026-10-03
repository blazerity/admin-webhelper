# PsExec отказал

## Симптом

Запуск скрипта/команды → `script_runs.status=failed`; в логе отказа SMB/PsExec/доступа.

## Проверка

1. Admin: учётка PsExec в Параметрах (или доменный вход админа).
2. Цель online; Admin$ / SMB 445 доступны с сервера.
3. UAC remote / LocalAccountTokenFilterPolicy для локальных учёток.
4. Скрипт Windows + powershell/cmd; Linux/bash в v1 не remote.
5. Cancel зависшего run, повторить на одном хосте; bulk — страница `/scripts/batches/<id>`.

## Связано

[device-offline.md](device-offline.md), checklist W2 bulk.
