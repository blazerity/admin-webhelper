# VNC в браузере (экспериментально)

Карточка устройства → **Рабочий стол** / **Подключиться**.
Debian открывает TCP на IP машины (порт 5900–5999) и отдаёт картинку в noVNC.
Gunicorn пиксели не гоняет: отдельный процесс `bawh-vnc` (~десятки МБ) + Nginx `/vnc/ws`.

Не путать с PsExec: скрипты — без GUI. VNC — консоль того, кто сидит за ПК.

## Что нужно на Windows

Агент **TightVNC** или **UltraVNC** (сервер, не Viewer). Файрвол: порт агента
только с IP сервера bAWH. Пароль TightVNC — не больше 8 символов (VNC DES);
тот же строкой в Параметрах → VNC.

### Установщик с сервера bAWH

MSI кладите в каталог [`vnc-agents/`](../vnc-agents/README.md) репозитория
(на Debian это `/opt/bawh/vnc-agents/`). Имя файла с `64bit` или `32bit`,
например `tightvnc-2.8.81-gpl-setup-64bit.msi`. В git MSI не коммитится
и при обновлении не стирается. Лицензия — ваша.

При запуске скрипта bAWH копирует MSI на ПК через уже открытый `ADMIN$`
в `C:\Windows\Temp`. Если копии нет — скрипт качает
`http://<IP-bAWH>/vnc-agents/64bit.msi` (псевдоним файла в каталоге).

### Библиотека «Скрипты»

После обновления в библиотеке появляется **TightVNC (тихая установка)**
(PowerShell, `NT AUTHORITY\SYSTEM`, хранение в базе, **не опубликован**).
Каноническое тело: `app/services/tightvnc_install_script.py`. Стоковое тело
(пустой `$VncPassword`) при старте обновляется целиком. В теле с уже
подставленным паролем сид v175 не затирает пароль и IP: чинит
`Convert-VncPasswordBytes` при необходимости и синхронизирует удаление
ярлыков TightVNC из общего меню Пуск.

Перед запуском:

1. Положите MSI в `/opt/bawh/vnc-agents/` на Debian (Параметры → VNC показывает, нашёлся ли файл).
2. В теле заполните `$VncPassword` и `$BawhServerIp` (IPv4 Debian с bAWH).
3. Сохраните тот же пароль в Параметрах → VNC.
4. Если операторы должны ставить агент сами — опубликуйте после подстановки.

| Поле | Значение |
| --- | --- |
| Название | TightVNC (тихая установка) |
| ОС | windows |
| Интерпретатор | powershell |
| От чьего имени | NT AUTHORITY\SYSTEM |
| Где хранить | в базе |
| Опубликован | нет, пока в теле нет пароля |

Если служба `tvnserver` уже есть, MSI не ставится повторно: обновляются пароль,
порт, файрвол `bAWH VNC` и удаляются ярлыки TightVNC из меню Пуск.
Принудительно: `$ForceReinstall = $true`.

Коды выхода: `0` ок; `2` нет/плохой пароль; `3` нет IP/порта; `4` нет MSI;
`5` msiexec; `6` служба или файрвол; `1` исключение.

MSI-свойства — по [официальной инструкции TightVNC 2.7](https://www.tightvnc.com/doc/win/TightVNC_2.7_for_Windows_Installing_from_MSI_Packages.pdf):
пары `SET_*` + `VALUE_OF_*` (не `VALUE=SET_PASSWORD`), `ADDLOCAL=Server`,
`SERVER_REGISTER_AS_SERVICE=1`, `SERVER_ALLOW_SAS=1`,
`SERVER_ADD_FIREWALL_EXCEPTION=0` (дырку в 5900 на весь мир не открываем).
После MSI пароль ещё пишется в реестр (VNC DES). Ярлыки TightVNC из общего
меню Пуск (`ProgramData\...\Programs\TightVNC`) удаляются.

Текст для ручной вставки в форму (тот же, что сидируется):

```powershell
$ErrorActionPreference = 'Stop'

function L([string]$Message) {
    Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $Message)
}

# «Параметры» только здесь. Пароль не длиннее 8 символов (VNC DES).
# bAWH-payload: tightvnc-msi
$VncPassword = ''
$BawhServerIp = ''
$MsiPath = ''
$Port = 5900
$ForceReinstall = $false

function Convert-VncPasswordBytes([string]$Plain) {
    # TightVNC хранит пароль как vncpasswd: DES-ECB, ключ E8 4A D6 60 C4 72 1A E0
    # (бит-реверс {23,82,107,6,35,78,88,7} для обычного DES/.NET).
    # Не путать с RFB challenge, где пароль сам становится ключом DES.
    $plainBytes = New-Object byte[] 8
    $raw = [System.Text.Encoding]::ASCII.GetBytes($Plain)
    [Array]::Copy($raw, $plainBytes, [Math]::Min(8, $raw.Length))
    $des = New-Object System.Security.Cryptography.DESCryptoServiceProvider
    $des.Mode = [System.Security.Cryptography.CipherMode]::ECB
    $des.Padding = [System.Security.Cryptography.PaddingMode]::None
    $des.Key = [byte[]](0xE8, 0x4A, 0xD6, 0x60, 0xC4, 0x72, 0x1A, 0xE0)
    $encryptor = $des.CreateEncryptor()
    try {
        return $encryptor.TransformFinalBlock($plainBytes, 0, 8)
    } finally {
        $encryptor.Dispose()
        $des.Dispose()
    }
}

function Get-TvnServerExe {
    foreach ($root in @(${env:ProgramFiles}, ${env:ProgramFiles(x86)})) {
        if (-not $root) { continue }
        $candidate = Join-Path $root 'TightVNC\tvnserver.exe'
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Get-MsiexecPath {
    $sysnative = Join-Path $env:SystemRoot 'Sysnative\msiexec.exe'
    if (Test-Path -LiteralPath $sysnative) { return $sysnative }
    return (Join-Path $env:SystemRoot 'System32\msiexec.exe')
}

function Test-MsiFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    if ((Get-Item -LiteralPath $Path).Length -lt 100000) { return $false }
    $fs = [System.IO.File]::OpenRead($Path)
    try {
        $buf = New-Object byte[] 8
        if ($fs.Read($buf, 0, 8) -lt 4) { return $false }
        return ($buf[0] -eq 0xD0 -and $buf[1] -eq 0xCF -and $buf[2] -eq 0x11 -and $buf[3] -eq 0xE0)
    } finally {
        $fs.Close()
    }
}

function Find-LocalTightVncMsi([string]$Explicit, [string]$TempDir) {
    $prefer = if ([Environment]::Is64BitOperatingSystem) { '64bit' } else { '32bit' }
    if ($Explicit) {
        if (Test-Path -LiteralPath $Explicit -PathType Container) {
            $hit = Get-ChildItem -LiteralPath $Explicit -Filter ("tightvnc*{0}*.msi" -f $prefer) -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if (-not $hit) {
                $hit = Get-ChildItem -LiteralPath $Explicit -Filter 'tightvnc*.msi' -ErrorAction SilentlyContinue |
                    Select-Object -First 1
            }
            if ($hit) { return $hit.FullName }
        } elseif (Test-Path -LiteralPath $Explicit -PathType Leaf) {
            return $Explicit
        }
        return $null
    }
    $hit = Get-ChildItem -LiteralPath $TempDir -Filter ("tightvnc*{0}*.msi" -f $prefer) -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $hit) {
        $hit = Get-ChildItem -LiteralPath $TempDir -Filter 'tightvnc*.msi' -ErrorAction SilentlyContinue |
            Select-Object -First 1
    }
    if ($hit) { return $hit.FullName }
    return $null
}

function Disable-BawhSslCheck {
    $code = @"
using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
public static class BawhTlsTrust {
  public static void Allow() {
    ServicePointManager.ServerCertificateValidationCallback = delegate { return true; };
    ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
  }
}
"@
    try { Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue } catch {}
    try { [BawhTlsTrust]::Allow() } catch {}
}

function Save-MsiFromBawh([string]$Ip, [string]$Dest) {
    Disable-BawhSslCheck
    $arch = if ([Environment]::Is64BitOperatingSystem) { '64bit' } else { '32bit' }
    $names = @(($arch + '.msi'), $arch)
    foreach ($scheme in @('http', 'https')) {
        foreach ($name in $names) {
            $url = '{0}://{1}/vnc-agents/{2}' -f $scheme, $Ip, $name
            L ("Качаю MSI с bAWH: {0}" -f $url)
            try {
                if (Test-Path -LiteralPath $Dest) { Remove-Item -LiteralPath $Dest -Force }
                $wc = New-Object System.Net.WebClient
                $wc.Headers.Add('User-Agent', 'bawh-tightvnc')
                $wc.DownloadFile($url, $Dest)
                if (Test-MsiFile $Dest) { return $Dest }
                L 'Ответ не похож на MSI — на сервере должен лежать файл в vnc-agents/.'
            } catch {
                L ("не скачалось: {0}" -f $_.Exception.Message)
            }
        }
    }
    return $null
}

function Set-TvnRegistry([string]$RegPath, [byte[]]$PasswordBytes, [int]$RfbPort) {
    if (-not (Test-Path -LiteralPath $RegPath)) {
        New-Item -Path $RegPath -Force | Out-Null
    }
    New-ItemProperty -LiteralPath $RegPath -Name Password -PropertyType Binary -Value $PasswordBytes -Force | Out-Null
    New-ItemProperty -LiteralPath $RegPath -Name ControlPassword -PropertyType Binary -Value $PasswordBytes -Force | Out-Null
    New-ItemProperty -LiteralPath $RegPath -Name UseVncAuthentication -PropertyType DWord -Value 1 -Force | Out-Null
    New-ItemProperty -LiteralPath $RegPath -Name UseControlAuthentication -PropertyType DWord -Value 1 -Force | Out-Null
    New-ItemProperty -LiteralPath $RegPath -Name AcceptRfbConnections -PropertyType DWord -Value 1 -Force | Out-Null
    New-ItemProperty -LiteralPath $RegPath -Name AcceptHttpConnections -PropertyType DWord -Value 0 -Force | Out-Null
    New-ItemProperty -LiteralPath $RegPath -Name RfbPort -PropertyType DWord -Value $RfbPort -Force | Out-Null
    # Не режем IP внутри TightVNC: deny-all ломает вход, если $BawhServerIp не тот
    # интерфейс, с которого Debian ходит на ПК. Ограничение — правило файрвола bAWH VNC.
    Remove-ItemProperty -LiteralPath $RegPath -Name IpAccessControl -ErrorAction SilentlyContinue
}

function Remove-TightVncStartMenu {
    $programs = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'
    if (-not (Test-Path -LiteralPath $programs)) { return }
    $folder = Join-Path $programs 'TightVNC'
    if (Test-Path -LiteralPath $folder) {
        Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $folder)) {
            L ("Удалил папку меню Пуск: {0}" -f $folder)
        } else {
            L ("Не удалось удалить папку меню Пуск: {0}" -f $folder)
        }
    }
    foreach ($lnk in @(Get-ChildItem -LiteralPath $programs -Filter '*TightVNC*.lnk' -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $lnk.FullName -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $lnk.FullName)) {
            L ("Удалил ярлык: {0}" -f $lnk.FullName)
        }
    }
}

try {
    L ("Компьютер: {0}" -f $env:COMPUTERNAME)
    L 'Тихая установка TightVNC Server. MSI — с сервера bAWH.'

    $VncPassword = [string]$VncPassword
    $BawhServerIp = ([string]$BawhServerIp).Trim()
    $MsiPath = ([string]$MsiPath).Trim()
    $Port = [int]$Port

    if (-not $VncPassword) {
        L 'Задайте $VncPassword в начале скрипта (тот же, что в Параметрах → VNC).'
        exit 2
    }
    if ($VncPassword.Length -gt 8) {
        L 'Пароль TightVNC не длиннее 8 символов (VNC DES). Укоротите и в скрипте, и в Параметрах.'
        exit 2
    }
    if ($VncPassword -match '[\s"''`$&|;<>%]') {
        L 'В пароле есть пробел или служебный символ — смените, иначе msiexec может сломаться.'
        exit 2
    }

    $parsedIp = $null
    if (-not $BawhServerIp -or -not [System.Net.IPAddress]::TryParse($BawhServerIp, [ref]$parsedIp)) {
        L 'Задайте $BawhServerIp — IPv4 сервера bAWH. Без него 5900 в мир не открываем.'
        exit 3
    }
    $BawhServerIp = $parsedIp.ToString()
    if ($Port -lt 5900 -or $Port -gt 5999) {
        L 'Порт должен быть 5900–5999.'
        exit 3
    }

    $exe = Get-TvnServerExe
    $installed = [bool]$exe
    if ($installed) {
        L ("Уже стоит: {0}" -f $exe)
    }

    if ((-not $installed) -or $ForceReinstall) {
        $tempDir = Join-Path $env:SystemRoot 'Temp'
        L 'Ищу MSI TightVNC (сначала то, что bAWH положил в Temp).'
        $msiSrc = Find-LocalTightVncMsi -Explicit $MsiPath -TempDir $tempDir
        if (-not $msiSrc) {
            $downloaded = Join-Path $tempDir 'bawh-tightvnc-download.msi'
            $msiSrc = Save-MsiFromBawh -Ip $BawhServerIp -Dest $downloaded
        }
        if (-not $msiSrc) {
            L 'На этом ПК нет MSI, и скачать с bAWH не вышло. Положите tightvnc-*-setup-64bit.msi в /opt/bawh/vnc-agents/ на Debian.'
            exit 4
        }
        L ("Установщик: {0}" -f $msiSrc)
        $localMsi = Join-Path $tempDir 'bawh-tightvnc.msi'
        if ([string]::Compare($msiSrc, $localMsi, $true) -ne 0) {
            Copy-Item -LiteralPath $msiSrc -Destination $localMsi -Force
        }
        if (-not (Test-MsiFile $localMsi)) {
            L 'Файл установщика повреждён или это не MSI.'
            exit 4
        }
        $msiArgs = @(
            '/i', ('"{0}"' -f $localMsi),
            '/qn',
            '/norestart',
            'ADDLOCAL=Server',
            'SERVER_REGISTER_AS_SERVICE=1',
            'SERVER_ADD_FIREWALL_EXCEPTION=0',
            'SERVER_ALLOW_SAS=1',
            'SET_USEVNCAUTHENTICATION=1',
            'VALUE_OF_USEVNCAUTHENTICATION=1',
            'SET_PASSWORD=1',
            ('VALUE_OF_PASSWORD={0}' -f $VncPassword),
            'SET_USECONTROLAUTHENTICATION=1',
            'VALUE_OF_USECONTROLAUTHENTICATION=1',
            'SET_CONTROLPASSWORD=1',
            ('VALUE_OF_CONTROLPASSWORD={0}' -f $VncPassword),
            'SET_ACCEPTHTTPCONNECTIONS=1',
            'VALUE_OF_ACCEPTHTTPCONNECTIONS=0',
            'SET_ACCEPTRFBCONNECTIONS=1',
            'VALUE_OF_ACCEPTRFBCONNECTIONS=1',
            'SET_RFBPORT=1',
            ('VALUE_OF_RFBPORT={0}' -f $Port),
            'SET_IPACCESSCONTROL=-1'
        ) -join ' '
        $msiexec = Get-MsiexecPath
        L ("msiexec: {0} (пароль в журнал не пишу)" -f $msiexec)
        $p = Start-Process -FilePath $msiexec -ArgumentList $msiArgs -Wait -PassThru
        if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) {
            L ("msiexec код {0}" -f $p.ExitCode)
            exit 5
        }
        if ($p.ExitCode -eq 3010) {
            L 'msiexec 3010: нужен reboot, отложен. Продолжаю настройку.'
        } else {
            L 'MSI установлен.'
        }
        $exe = Get-TvnServerExe
    } else {
        L 'MSI не трогаю — служба уже есть. Обновляю пароль, порт, файрвол и меню Пуск.'
    }

    $pwBytes = Convert-VncPasswordBytes $VncPassword
    $native = 'HKLM:\SOFTWARE\TightVNC\Server'
    $wow = 'HKLM:\SOFTWARE\Wow6432Node\TightVNC\Server'
    $regPaths = @()
    if (Test-Path -LiteralPath $native) { $regPaths += $native }
    if (Test-Path -LiteralPath $wow) { $regPaths += $wow }
    if ($regPaths.Count -eq 0) {
        $fallback = $native
        if (${env:ProgramFiles(x86)} -and $exe -and $exe.StartsWith(${env:ProgramFiles(x86)}, [System.StringComparison]::OrdinalIgnoreCase)) {
            $fallback = $wow
        }
        $regPaths = @($fallback)
    }
    foreach ($regPath in $regPaths) {
        L ("Пишу HKLM ({0})" -f $regPath)
        Set-TvnRegistry -RegPath $regPath -PasswordBytes $pwBytes -RfbPort $Port
    }

    L ("Файрвол {0}/tcp только с {1}" -f $Port, $BawhServerIp)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    foreach ($ruleName in @('bAWH VNC', 'TightVNC', 'TightVNC Server')) {
        netsh advfirewall firewall delete rule name=$ruleName | Out-Null
    }
    netsh advfirewall firewall add rule name='bAWH VNC' dir=in action=allow protocol=TCP localport=$Port remoteip=$BawhServerIp profile=any enable=yes
    $fwCode = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($fwCode -ne 0) {
        L ("netsh add rule код {0}" -f $fwCode)
        exit 6
    }

    Remove-TightVncStartMenu

    $svc = Get-Service -Name 'tvnserver' -ErrorAction SilentlyContinue
    if (-not $svc) {
        L 'Служба tvnserver не появилась.'
        exit 6
    }
    L 'Перезапуск tvnserver, чтобы подхватить пароль из реестра.'
    Restart-Service -Name 'tvnserver' -Force
    $deadline = (Get-Date).AddSeconds(20)
    do {
        $svc.Refresh()
        if ($svc.Status -eq 'Running') { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    if ($svc.Status -ne 'Running') {
        L ("Служба tvnserver: {0}" -f $svc.Status)
        exit 6
    }

    L ("Готово. Агент слушает {0}, пароль как в Параметрах, вход только с {1}." -f $Port, $BawhServerIp)
    exit 0
} catch {
    L ("Ошибка: {0}" -f $_.Exception.Message)
    exit 1
}
```

## HTTPS

Параметры → **HTTPS-сертификат**: вставить PEM или загрузить файлы →
«Сохранить» → «Включить HTTPS». Редирект с :80 и `SESSION_COOKIE_SECURE`
включаются галками. Ключ лежит в `/opt/bawh/certs/`, не в PostgreSQL.

Буфер обмена в браузере на телефоне чаще работает уже по HTTPS.

## Если не открывается

- служба `bawh-vnc` запущена (Параметры → VNC);
- Nginx проксирует `/vnc/ws` (шаблон `deploy/nginx-bawh.conf`);
- на ПК слушает **тот же порт**, что в Параметрах (обычно **5900**, не 5909);
- `$BawhServerIp` — адрес Debian, с которого он ходит на ПК (`ip -4 addr`, не IP самой машины);
- пароль агента совпадает с полем на странице стола / Параметрами (до 8 символов);
- билет живёт 90 секунд — обновите страницу стола, если долго ждали.

Лог шлюза: `journalctl -u bawh-vnc -n 80 --no-pager`.
`vnc session … ip:port` значит TCP открылся; дальше должна быть строка `vnc greeting … RFB 003.008`.
Если `not RFB` — на порту не TightVNC (часто порт в Параметрах не 5900).
Если после `RFB` сразу `tcp closed` — пароль в реестре или старый ACL TightVNC (`IpAccessControl`).
`nc` до `RFB 003.008`, пустой `IpAccessControl` и перезапуск службы **не** лечат неверный
`HKLM:\SOFTWARE\TightVNC\Server\Password`: первый скрипт писал RFB-challenge DES
(пароль как ключ), TightVNC ждёт vncpasswd (ключ `E8 4A D6 60 C4 72 1A E0`).
После обновления bAWH ещё раз запустите «TightVNC (тихая установка)» на этот ПК
(MSI не ставится повторно — перепишется реестр и перезапустится `tvnserver`).

На уже поставленном агенте ACL можно снять вручную:

```powershell
Remove-ItemProperty HKLM:\SOFTWARE\TightVNC\Server -Name IpAccessControl -ErrorAction SilentlyContinue
Restart-Service tvnserver
```
