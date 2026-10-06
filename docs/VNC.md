# VNC в браузере (экспериментально)

Карточка устройства → **Рабочий стол** / **Подключиться**.
Debian открывает TCP на IP машины (порт 5900–5999) и отдаёт картинку в noVNC.
Gunicorn пиксели не гоняет: отдельный процесс `bawh-vnc` (~десятки МБ) + Nginx `/vnc/ws`.

Не путать с PsExec: скрипты — без GUI. VNC — консоль того, кто сидит за ПК.

## Что нужно на Windows

Агент **TightVNC** (сервер). Пароль — не больше 8 символов, тот же в
Настройки → VNC. Файрвол на ПК: порт агента только с исходящего IPv4 Debian
(его bAWH определяет сам при подключении).

### Установка при «Подключить»

Карточка → **Рабочий стол** → **Подключить**. bAWH через PsExec (`SYSTEM`):

1. если TightVNC ещё нет — ставит MSI из [`vnc-agents/`](../vnc-agents/README.md)
   (`ADMIN$\Temp`, запасной HTTP `/vnc-agents/64bit.msi`);
2. если служба уже есть — MSI не трогает;
3. всегда переписывает пароль, порт из Настройки → VNC, файрвол `bAWH VNC`
   (только с IP сервера) и снимает ярлыки из общего меню Пуск.

Пароль в `script_runs.command_text` не попадает. Коды: `0` ок; `2` нет пароля;
`3` нет IP/порта; `4` нет MSI; `5` msiexec; `6` служба/файрвол; `1` ошибка.

MSI кладите в `/opt/bawh/vnc-agents/` (`tightvnc-*-setup-64bit.msi`).
Свойства MSI — [TightVNC 2.7](https://www.tightvnc.com/doc/win/TightVNC_2.7_for_Windows_Installing_from_MSI_Packages.pdf):
`SET_*` + `VALUE_OF_*`, `ADDLOCAL=Server`, `SERVER_REGISTER_AS_SERVICE=1`,
`SERVER_ALLOW_SAS=1`, `SERVER_ADD_FIREWALL_EXCEPTION=0`.

### Библиотека скриптов (Настройки → Скрипты)

После обновления **TightVNC (тихая установка)** заменяется на
**TightVNC (тихое удаление)** (PowerShell, SYSTEM, в базе, не опубликован).
Стоковое тело сидируется маркером `data_fix.seed_tightvnc_script_v176`.

| Поле | Значение |
| --- | --- |
| Название | TightVNC (тихое удаление) |
| ОС | windows |
| Интерпретатор | powershell |
| От чьего имени | NT AUTHORITY\SYSTEM |
| Где хранить | в базе |
| Опубликован | нет (можно опубликовать) |

Снимает службу `tvnserver`, MSI, `HKLM\SOFTWARE\TightVNC`, правило файрвола
`bAWH VNC`, ярлыки меню Пуск и `Program Files\TightVNC`.

Текст для ручной вставки:

```powershell
$ErrorActionPreference = 'Stop'

function L([string]$Message) {
    Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $Message)
}

# bAWH-payload: tightvnc-uninstall
function Get-MsiexecPath {
    $sysnative = Join-Path $env:SystemRoot 'Sysnative\msiexec.exe'
    if (Test-Path -LiteralPath $sysnative) { return $sysnative }
    return (Join-Path $env:SystemRoot 'System32\msiexec.exe')
}

function Get-TightVncProductIds {
    $roots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    )
    $ids = @()
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
            $props = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
            if (-not $props) { continue }
            $name = [string]$props.DisplayName
            if ($name -and ($name -like 'TightVNC*')) {
                $ids += $key.PSChildName
            }
        }
    }
    return @($ids | Select-Object -Unique)
}

try {
    L ("Компьютер: {0}" -f $env:COMPUTERNAME)
    L 'Тихое удаление TightVNC: служба, MSI, реестр, файрвол, ярлыки.'

    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $svc = Get-Service -Name 'tvnserver' -ErrorAction SilentlyContinue
    if ($svc) {
        L 'Останавливаю службу tvnserver.'
        Stop-Service -Name 'tvnserver' -Force -ErrorAction SilentlyContinue
        sc.exe delete tvnserver | Out-Null
    }

    $msiexec = Get-MsiexecPath
    foreach ($productId in @(Get-TightVncProductIds)) {
        L ("msiexec /x {0}" -f $productId)
        $p = Start-Process -FilePath $msiexec -ArgumentList @('/x', $productId, '/qn', '/norestart') -Wait -PassThru
        if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 1605 -and $p.ExitCode -ne 3010) {
            L ("msiexec код {0} — продолжаю чистить хвосты." -f $p.ExitCode)
        }
    }

    foreach ($regPath in @('HKLM:\SOFTWARE\TightVNC', 'HKLM:\SOFTWARE\Wow6432Node\TightVNC')) {
        if (Test-Path -LiteralPath $regPath) {
            Remove-Item -LiteralPath $regPath -Recurse -Force -ErrorAction SilentlyContinue
            if (-not (Test-Path -LiteralPath $regPath)) {
                L ("Удалил {0}" -f $regPath)
            } else {
                L ("Не удалось удалить {0}" -f $regPath)
            }
        }
    }

    foreach ($ruleName in @('bAWH VNC', 'TightVNC', 'TightVNC Server')) {
        netsh advfirewall firewall delete rule name=$ruleName | Out-Null
    }

    $programs = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'
    if (Test-Path -LiteralPath $programs) {
        $folder = Join-Path $programs 'TightVNC'
        if (Test-Path -LiteralPath $folder) {
            Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
        }
        foreach ($lnk in @(Get-ChildItem -LiteralPath $programs -Filter '*TightVNC*.lnk' -Force -ErrorAction SilentlyContinue)) {
            Remove-Item -LiteralPath $lnk.FullName -Force -ErrorAction SilentlyContinue
        }
    }

    foreach ($root in @(${env:ProgramFiles}, ${env:ProgramFiles(x86)})) {
        if (-not $root) { continue }
        $dir = Join-Path $root 'TightVNC'
        if (Test-Path -LiteralPath $dir) {
            Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
            if (-not (Test-Path -LiteralPath $dir)) {
                L ("Удалил {0}" -f $dir)
            }
        }
    }
    $ErrorActionPreference = $prev

    if (Get-Service -Name 'tvnserver' -ErrorAction SilentlyContinue) {
        L 'Служба tvnserver всё ещё есть.'
        exit 1
    }
    L 'Готово. TightVNC снят.'
    exit 0
} catch {
    L ("Ошибка: {0}" -f $_.Exception.Message)
    exit 1
}
```

## HTTPS

Настройки → **HTTPS**: вставить PEM или загрузить файлы →
«Сохранить» → «Включить HTTPS». Редирект с :80 и `SESSION_COOKIE_SECURE`
включаются галками. Ключ лежит в `/opt/bawh/certs/`, не в PostgreSQL.

Буфер обмена в браузере на телефоне чаще работает уже по HTTPS.

## Если не открывается

- служба `bawh-vnc` запущена (Настройки → VNC);
- Nginx проксирует `/vnc/ws` (шаблон `deploy/nginx-bawh.conf`);
- MSI лежит в `/opt/bawh/vnc-agents/` (Настройки → VNC показывают файл);
- пароль задан в Настройки → VNC и на странице стола (до 8 символов);
- на странице стола виден лог агента (PsExec) до «Готово».

Лог шлюза: `journalctl -u bawh-vnc -n 80 --no-pager`.
`vnc session … ip:port` значит TCP открылся; дальше должна быть строка `vnc greeting … RFB 003.008`.
Если `not RFB` — на порту не TightVNC (часто порт в Настройки → VNC не тот).
Если после `RFB` сразу `tcp closed` — пароль. Ещё раз **Подключить**: агент
перепишет реестр и перезапустит `tvnserver`.
