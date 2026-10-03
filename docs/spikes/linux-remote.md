# Spike: Linux remote runtime (W4-05)

**Статус:** исследование, **не** для merge в prod-код.

## Контекст

UI уже различает `target_os=linux` / `interpreter=bash`, но remote канал — PsExec (Windows). Roadmap сознательно откладывает Linux remote.

## Варианты канала

| Канал | Плюсы | Минусы |
| --- | --- | --- |
| SSH + key/password | Стандарт для Linux | Нужен vault ключей, host key policy, sudo |
| Ansible runner | Идемпотентность | Тяжёлый dependency |
| WinRM analog N/A | — | — |

## Минимальный контракт (если брать позже)

- Отдельный `run_as` / executor в `script_service`, не ломая PsExec path
- Authz тот же (`user_can_run_script` + sector)
- `script_runs` polling без изменений
- Feature-flag / env `LINUX_REMOTE_ENABLED=0` по умолчанию

## Решение сейчас

Оставить flash «Linux не реализован» на run routes; не начинать SSH в W4.
