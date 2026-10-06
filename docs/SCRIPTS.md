# Скрипты из веб-формы

> **Для ИИ и авторов:** как подогнать текст под библиотеку **Скрипты**.
> Карта кода запуска — [`ARCHITECTURE.md`](ARCHITECTURE.md)
> (`script_service`, `psexec_service`, `routes/scripts.py`).
> Готовые `.ps1` / `.bat` **не коммитить** в git (`scripts/` в `.gitignore`).
> Ответ агента — поля формы + тело, которое админ вставляет в UI.

bAWH гоняет скрипт на Windows через PsExec (SMB/`ADMIN$`). Это не консоль
администратора на столе пользователя: нет клавиатуры, нет GUI, нет профиля
PowerShell, stdin закрыт.

---

## 0. Поля формы по умолчанию

Пока задача явно не про однострочник `cmd` — так:

| Поле | Значение |
| --- | --- |
| Название | короткое, уникальное (до 128 символов) |
| Описание | что делает и когда запускать |
| ОС | `windows` |
| Интерпретатор | `powershell` |
| От чьего имени | `NT AUTHORITY\SYSTEM` |
| Где хранить | `в базе` |
| Опубликован | да, если операторам можно запускать; нет — только admin |
| Текст | тело ниже; без обёртки `powershell.exe -EncodedCommand` |

Другие комбинации — §2.

---

## 1. Это библиотека, не пресет и не ping

| Что | Где | Лимит | Как запускается |
| --- | --- | --- | --- |
| **Скрипт из формы** | меню Скрипты, карточка, bulk с карты | 100 000 символов | см. §3 |
| **Команда на карточке** | вкладка «Команды», пресеты `whoami` / `ipconfig` | 4 000 символов | всегда `cmd.exe /c …`, учётка PsExec, не SYSTEM |
| **Ping / tracert** | карточка / bulk | — | локально с сервера bAWH, не на ПК |
| **Опрос железа** | вкладка «Оборудование», планировщик | — | WMI с сервера, не библиотека |

Не дублировать опрос CPU/ОЗУ/дисков/ОС — это уже WMI-job. Скрипт — то, чего
в карточке нет: gpupdate, печать, KMS, сводка, ремонт служб.

Параметров запуска в форме нет. «Аргументы» — переменные в начале тела
(`$KmsHost = ''`). На пачке машин один и тот же текст; отличаться будет
`$env:COMPUTERNAME`.

---

## 2. Как выбрать тип

```
нужен Linux / bash?          → нельзя (v1, runtime отклонит)
одна короткая cmd-утилита?   → интерпретатор cmd, одна строка
всё остальное на Windows?    → powershell + SYSTEM
нужен профиль учётки SMB?    → powershell + учётка PsExec (редко)
```

### `powershell` (предпочтительный)

Многострочный текст. Сервис кладёт его в `C:\Windows\Temp\bawh_*.ps1` (UTF-8 BOM)
и запускает:

```
cmd.exe /c powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File … <nul 2>&1
```

В начало файла **дописывается префикс** (UTF-8, `AutoFlush`,
`$ProgressPreference = 'SilentlyContinue'`). Не рассчитывайте, что ваша первая
строка — первая в файле. `#Requires` допустим. `$PSScriptRoot` — временный
каталог; после выхода файл удаляют.

Не оборачивать тело во второй `powershell.exe`, `-EncodedCommand`,
`Set-ExecutionPolicy`, `Start-Transcript` «вместо журнала».

### `cmd`

Тело **не** пишется в `.cmd`. Это один аргумент:

```
cmd.exe /c {весь текст}
```

Лимит командной строки Windows ≈ 8191 символ. Переносы строк, `goto`,
`setlocal` и блоки в скобках ненадёжны. Годятся одна команда или цепочка
`command1 & command2`. Если больше одной логической строки — берите
`powershell`.

### `bash` / ОС `linux`

В форме есть, **удалённый запуск в v1 не реализован**. PsExec — только Windows.
Не предлагать bash «на будущее» как рабочий скрипт.

---

## 3. Контракт запуска

```
форма / карта
  → script_service (pending → running)
  → SMB (учётка из Параметров или пароль LDAP-входа)
  → процесс на ПК (SYSTEM или та же учётка)
  → stdout/stderr → журнал script_runs (JS поллит ~0.8 с)
  → код выхода 0 = «успешно», иначе «ошибка»
```

| Ограничение | Значение | Где |
| --- | --- | --- |
| Таймаут | 600 с (gpupdate / ccmsetup) | `SCRIPT_TIMEOUT_SECONDS` |
| Длина тела | 100 000 | `MAX_SCRIPT_CHARS` |
| Журнал | 200 000 символов, хвост обрезается | `MAX_LOG_CHARS` |
| Stdin | пустой (`<nul`) | `psexec_service` |
| Профиль PS | нет (`-NoProfile`) | то же |
| Интерактив | нет (`-NonInteractive`) | то же |

Статус «успешно» смотрит **только exit code**, не на текст «OK» в логе.
В конце тела — явный `exit 0` или `exit 1` (`2`, `4`… для разных отказов).

Отмена в UI рвёт сессию. Не полагаться на `finally` с сетью после Kill.

---

## 4. От чьего имени

Сессию SMB **всегда** открывает учётка администратора (Параметры / вход).
`run_as` меняет только процесс на ПК.

| `run_as` | Процесс | Когда |
| --- | --- | --- |
| `NT AUTHORITY\SYSTEM` | службы, HKLM, лицензия, Spooler, w32tm, BitLocker, gpupdate | почти всегда |
| учётка PsExec | тот же admin, что открыл SMB | редко: нужен его профиль или явный Kerberos этого пользователя |

Ни SYSTEM, ни учётка PsExec — это не сессия сидящего за ПК. Нет его
`HKCU`, mapped drives, принтеров пользователя. `quser` при этом работает.

Для лицензии, печати, времени, GPO — только SYSTEM.

---

## 5. Журнал: что писать

Журнал = stdout + stderr. Пишите **по мере работы**, не пачкой в конце.

**PowerShell:** `Write-Output` (или `function L { Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $m) }`).
Первая строка — сразу, до долгого WMI.

Не использовать:

- `Read-Host`, `pause`, `cmd /k`, ожидание клавиши
- `Write-Progress` / `Write-Host -ForegroundColor` как единственный вывод
  (Progress уходит в CLIXML и вырезается)
- `[System.Windows.Forms]`, `Out-GridView`, `msg *`, `slmgr.vbs` без
  `cscript //nologo` (Session 0, окна никто не увидит)
- `Start-Process -Wait` без перенаправления, если нужен текст в журнале

Кириллица в `Write-Output` допустима: сервис ставит UTF-8. Операторы читают
журнал по-русски — пишите сообщения так же.

---

## 6. Типичные ловушки при переносе «готового» скрипта

| Было на столе / в ISE | В bAWH |
| --- | --- |
| `Get-WmiObject` + `ToDateTime($os.LastBootUpTime)` | `Get-CimInstance`: `LastBootUpTime` уже `[datetime]`. Не гонять через `ManagementDateTimeConverter` |
| `$args[0]`, параметры функции | переменные в шапке тела |
| `#Requires` + свой `-ExecutionPolicy` | `#Requires` ок; политику не трогать |
| `powershell -EncodedCommand …` | запрещено: рвёт канал PAExec (`STATUS_PIPE_BROKEN`) |
| `slmgr /ipk` (GUI) | `cscript //nologo slmgr.vbs …` или CIM `InstallProductKey` / `Activate` |
| интерактивный `gpupdate` | `gpupdate.exe /force`; смотреть `$LASTEXITCODE` |
| `.bat` на 40 строк | переписать в PowerShell |
| путь к себе, рядом лежат `.psm1` | на ПК только один temp `.ps1`; всё встроить в тело |
| предполагается пользователь за консолью | не предполагается |

`$ErrorActionPreference`: диагностика — `Continue` и писать ошибки в журнал;
ремонт — `Stop` + `try/catch` + `exit 1`. Не глотать исключения молча.

---

## 7. Чеклист перед вставкой в форму

1. ОС `windows`, интерпретатор выбран по §2.
2. `run_as` = SYSTEM, если нет явной причины иначе.
3. Нет `Read-Host` / GUI / `EncodedCommand` / второго `powershell.exe`.
4. Первая полезная строка — `Write-Output` (или `echo` для cmd).
5. Код выхода явный; 0 только если работа сделана.
6. Долгие шаги логируются до и после (иначе 10 минут «тишины»).
7. CIM-даты не прогоняются через DMTF-конвертер.
8. Нет секретов в теле (пароли, внутренние URL с кредами). Ключи KMS-клиента
   (GVLK) — только официальные Microsoft, плюс **свой** KMS-хост или DNS SRV.
9. Название уникально; описание говорит оператору, что будет на машине.
10. Опасное (reboot, logoff, удаление SCCM) — не публиковать операторам.

---

## 8. Минимальный каркас PowerShell

```powershell
$ErrorActionPreference = 'Stop'

function L([string]$Message) {
    Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $Message)
}

# «Параметры» только здесь:
# $Something = ''

try {
    L ("Компьютер: {0}" -f $env:COMPUTERNAME)
    L 'Старт.'
    # …
    L 'Готово.'
    exit 0
} catch {
    L ("Ошибка: {0}" -f $_.Exception.Message)
    exit 1
}
```

Каркас cmd (одна строка):

```bat
echo Старт & gpupdate.exe /force & echo Код %ERRORLEVEL%
```

---

## 9. Куда смотреть в коде

| Вопрос | Файл |
| --- | --- |
| Поля, лимиты, публикация | `app/services/script_service.py` (`save_script`, `MAX_SCRIPT_CHARS`) |
| Форма UI | `app/templates/scripts/form.html` |
| Запуск PS/cmd, префикс, temp-файл | `app/services/psexec_service.py` (`run_remote_script`, `wrap_powershell_script`) |
| 0 = success | `app/services/script_service.py` (`_perform`) |
| Таймаут | `app/config.py` → `SCRIPT_TIMEOUT_SECONDS` |
| Живой журнал в UI | `app/static/js/run_log.js`, `templates/scripts/run.html` |
| Пресеты карточки (не библиотека) | `app/services/command_presets.py` |
| Слои и authz | [`ARCHITECTURE.md`](ARCHITECTURE.md) |

Готовый пример для библиотеки: тихая установка TightVNC — [`VNC.md`](VNC.md)
(`app/services/tightvnc_install_script.py`, сид при старте).

Меняете запуск (интерпретатор, pipe, SYSTEM) — обновите этот файл в том же PR.
