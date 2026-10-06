"""Каноническое тело тихой установки TightVNC для библиотеки «Скрипты».

Админ заполняет ``$VncPassword`` и ``$BawhServerIp`` в UI и при необходимости
публикует. Пароль в git не кладём. MSI с интернета не качается.

Сидируется из ``ensure_schema``, если в ``scripts`` ещё нет строки с этим именем.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AppSetting, RunAs, Script

logger = logging.getLogger(__name__)

TIGHTVNC_INSTALL_SCRIPT_NAME = "TightVNC (тихая установка)"
TIGHTVNC_SCRIPT_SEED_KEY = "data_fix.seed_tightvnc_script_v170"

TIGHTVNC_INSTALL_SCRIPT_DESCRIPTION = (
    "Тихая установка TightVNC Server как службы (порт 5900). "
    "Перед запуском заполните $VncPassword (до 8 символов, тот же что в "
    "Параметрах → VNC) и $BawhServerIp. MSI — $MsiPath или tightvnc*.msi в "
    "C:\\Windows\\Temp. Файрвол только с IP bAWH; из интернета ничего не качает. "
    "После подстановки пароля можно опубликовать для операторов."
)

# PowerShell / SYSTEM / Windows / storage=db. Коды: 0 ок; 2 нет пароля;
# 3 нет IP bAWH; 4 нет MSI; 5 msiexec; 6 служба не поднялась; 1 прочая ошибка.
TIGHTVNC_INSTALL_SCRIPT_BODY = r"""$ErrorActionPreference = 'Stop'

function L([string]$Message) {
    Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $Message)
}

# «Параметры» только здесь. Пароль не длиннее 8 символов (VNC DES).
$VncPassword = ''
$BawhServerIp = ''
$MsiPath = ''
$Port = 5900
$ForceReinstall = $false

function Convert-VncPasswordBytes([string]$Plain) {
    $key = New-Object byte[] 8
    $raw = [System.Text.Encoding]::ASCII.GetBytes($Plain)
    [Array]::Copy($raw, $key, [Math]::Min(8, $raw.Length))
    for ($i = 0; $i -lt 8; $i++) {
        $b = [int]$key[$i]
        $rev = 0
        for ($bit = 0; $bit -lt 8; $bit++) {
            if ($b -band (1 -shl $bit)) { $rev = $rev -bor (1 -shl (7 - $bit)) }
        }
        $key[$i] = [byte]$rev
    }
    $des = New-Object System.Security.Cryptography.DESCryptoServiceProvider
    $des.Mode = [System.Security.Cryptography.CipherMode]::ECB
    $des.Padding = [System.Security.Cryptography.PaddingMode]::None
    $des.Key = $key
    $encryptor = $des.CreateEncryptor()
    $magic = [byte[]](0x17, 0x52, 0x6B, 0x06, 0x23, 0x4E, 0x58, 0x07)
    try {
        return $encryptor.TransformFinalBlock($magic, 0, 8)
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

function Resolve-TightVncMsi([string]$Explicit, [string]$TempDir) {
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

function Set-TvnRegistry([string]$RegPath, [byte[]]$PasswordBytes, [int]$RfbPort, [string]$Access) {
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
    New-ItemProperty -LiteralPath $RegPath -Name IpAccessControl -PropertyType String -Value $Access -Force | Out-Null
}

try {
    L ("Компьютер: {0}" -f $env:COMPUTERNAME)
    L 'Тихая установка TightVNC Server.'

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
        L 'Ищу MSI TightVNC (интернет не трогаю).'
        $msiSrc = Resolve-TightVncMsi -Explicit $MsiPath -TempDir $tempDir
        if (-not $msiSrc) {
            L 'Положите tightvnc-*-setup-64bit.msi в C:\Windows\Temp или укажите $MsiPath (UNC, который читает SYSTEM).'
            exit 4
        }
        L ("Установщик: {0}" -f $msiSrc)
        $localMsi = Join-Path $tempDir 'bawh-tightvnc.msi'
        if ([string]::Compare($msiSrc, $localMsi, $true) -ne 0) {
            L 'Копирую MSI в %SystemRoot%\Temp (msiexec с UNC часто падает).'
            Copy-Item -LiteralPath $msiSrc -Destination $localMsi -Force
        }
        $ipAcl = '{0}-{0}:0,0.0.0.0-255.255.255.255:1' -f $BawhServerIp
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
            'SET_IPACCESSCONTROL=1',
            ('VALUE_OF_IPACCESSCONTROL="{0}"' -f $ipAcl)
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
        L 'MSI не трогаю — служба уже есть. Обновляю пароль, порт и файрвол.'
    }

    $pwBytes = Convert-VncPasswordBytes $VncPassword
    $ipAcl = '{0}-{0}:0,0.0.0.0-255.255.255.255:1' -f $BawhServerIp
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
        Set-TvnRegistry -RegPath $regPath -PasswordBytes $pwBytes -RfbPort $Port -Access $ipAcl
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
"""


def seed_tightvnc_install_script(*, table_names: set[str]) -> None:
    """Один раз вставить скрипт в библиотеку, если имени ещё нет.

    Не затирает уже существующую строку (админ мог править тело). Маркер в
    ``app_settings``, чтобы не повторять INSERT после ручного удаления.
    """
    if "scripts" not in table_names or "app_settings" not in table_names:
        return

    marker = db.session.get(AppSetting, TIGHTVNC_SCRIPT_SEED_KEY)
    if marker is not None and (marker.value or "").strip() == "1":
        return

    existing = db.session.scalar(
        select(Script.id).where(Script.name == TIGHTVNC_INSTALL_SCRIPT_NAME)
    )
    inserted = False
    if existing is None:
        db.session.add(
            Script(
                name=TIGHTVNC_INSTALL_SCRIPT_NAME,
                description=TIGHTVNC_INSTALL_SCRIPT_DESCRIPTION,
                target_os="windows",
                interpreter="powershell",
                run_as=RunAs.SYSTEM,
                storage="db",
                file_path=None,
                content=TIGHTVNC_INSTALL_SCRIPT_BODY,
                is_published=False,
                created_by_id=None,
            )
        )
        inserted = True

    if marker is None:
        db.session.add(AppSetting(key=TIGHTVNC_SCRIPT_SEED_KEY, value="1"))
    else:
        marker.value = "1"

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        logger.info(
            "ensure_schema: TightVNC script seed skipped (name already taken)",
        )
        marker = db.session.get(AppSetting, TIGHTVNC_SCRIPT_SEED_KEY)
        if marker is None:
            db.session.add(AppSetting(key=TIGHTVNC_SCRIPT_SEED_KEY, value="1"))
            db.session.commit()
        return

    if inserted:
        logger.info(
            "ensure_schema: seeded unpublished script %r",
            TIGHTVNC_INSTALL_SCRIPT_NAME,
        )
    else:
        logger.info(
            "ensure_schema: TightVNC script already present, seed marker set",
        )
