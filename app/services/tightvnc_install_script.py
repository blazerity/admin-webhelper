"""Каноническое тело агента TightVNC: установка при подключении и удаление из библиотеки.

Админ кладёт ``tightvnc-*-setup-64bit.msi`` в каталог ``vnc-agents/``
(на сервере ``/opt/bawh/vnc-agents/``). При «Подключить» MSI копируется
на ПК через ADMIN$; запасной путь — HTTP ``/vnc-agents/64bit.msi``.

Пароль в git и в ``script_runs.command_text`` не кладём.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AppSetting, RunAs, Script

logger = logging.getLogger(__name__)

TIGHTVNC_INSTALL_SCRIPT_NAME = "TightVNC (тихая установка)"
TIGHTVNC_UNINSTALL_SCRIPT_NAME = "TightVNC (тихое удаление)"
TIGHTVNC_SCRIPT_SEED_KEY = "data_fix.seed_tightvnc_script_v176"
TIGHTVNC_PAYLOAD_SENTINEL = "# bAWH-payload: tightvnc-msi"
TIGHTVNC_UNINSTALL_SENTINEL = "# bAWH-payload: tightvnc-uninstall"
VNC_AGENTS_DIRNAME = "vnc-agents"
_SAFE_AGENT_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_MSI_BYTES = 40 * 1024 * 1024
_VNC_PASS_BAD = re.compile(r"""[\s"'`$&|;<>%]""")
_ensure_secrets_lock = threading.Lock()
_ensure_secrets: dict[int, str] = {}

TIGHTVNC_UNINSTALL_SCRIPT_DESCRIPTION = (
    "Тихое снятие TightVNC: служба tvnserver, MSI, реестр, правило файрвола "
    "bAWH VNC, ярлыки меню Пуск и папка в Program Files. Без пароля."
)

TIGHTVNC_INSTALL_SCRIPT_DESCRIPTION = TIGHTVNC_UNINSTALL_SCRIPT_DESCRIPTION

# PowerShell / SYSTEM. Коды ensure: 0 ок; 2 нет пароля; 3 нет IP/порта;
# 4 нет MSI; 5 msiexec; 6 служба не поднялась; 1 прочая ошибка.
# Удаление: 0 ок; 1 ошибка.
TIGHTVNC_ENSURE_SCRIPT_BODY = r"""$ErrorActionPreference = 'Stop'

function L([string]$Message) {
    Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $Message)
}

# Параметры подставляет bAWH из Параметров → VNC и исходящего IP к ПК.
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
"""

TIGHTVNC_UNINSTALL_SCRIPT_BODY = r"""$ErrorActionPreference = 'Stop'

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
"""


def vnc_agents_dir(root: str | Path | None = None) -> Path:
    """Каталог, куда админ кладёт MSI. По умолчанию PROJECT_ROOT/vnc-agents."""
    if root is None:
        try:
            from flask import current_app, has_app_context

            if has_app_context():
                root = current_app.config.get("PROJECT_ROOT")
        except Exception:
            root = None
    if not root:
        root = Path(__file__).resolve().parents[2]
    return Path(root) / VNC_AGENTS_DIRNAME


def list_tightvnc_msis(root: str | Path | None = None) -> list[Path]:
    """MSI TightVNC в vnc-agents/, без обхода каталога."""
    folder = vnc_agents_dir(root)
    if not folder.is_dir():
        return []
    found: list[Path] = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    for path in entries:
        if not path.is_file():
            continue
        if path.suffix.lower() != ".msi":
            continue
        if "tightvnc" not in path.name.lower() and path.name.lower() not in {
            "64bit.msi",
            "32bit.msi",
        }:
            continue
        if not _SAFE_AGENT_NAME.match(path.name):
            continue
        found.append(path)
    found.sort(key=lambda item: item.name.lower())
    return found


def pick_tightvnc_msi(prefer: str, root: str | Path | None = None) -> Path | None:
    """Выбрать MSI под 64bit/32bit. Имя файла должно содержать метку архитектуры."""
    want = "64bit" if str(prefer).lower() in {"64", "64bit", "x64"} else "32bit"
    other = "32bit" if want == "64bit" else "64bit"
    files = list_tightvnc_msis(root)
    tagged = [path for path in files if want in path.name.lower()]
    if tagged:
        return tagged[0]
    generic = [path for path in files if other not in path.name.lower()]
    if generic:
        return generic[0]
    return None


def resolve_vnc_agent_file(name: str, root: str | Path | None = None) -> Path | None:
    """Безопасный путь к MSI: точное имя или псевдоним 64bit.msi / 32bit.msi."""
    raw = (name or "").strip()
    if not raw or not _SAFE_AGENT_NAME.match(raw):
        return None
    lowered = raw.lower()
    if lowered in {"64bit", "64bit.msi", "x64", "x64.msi"}:
        return pick_tightvnc_msi("64bit", root)
    if lowered in {"32bit", "32bit.msi", "x86", "x86.msi"}:
        return pick_tightvnc_msi("32bit", root)
    folder = vnc_agents_dir(root)
    try:
        folder_real = folder.resolve()
        path = (folder / raw).resolve()
    except OSError:
        return None
    if path.parent != folder_real or not path.is_file():
        return None
    if path.suffix.lower() != ".msi":
        return None
    return path


def extra_admin_files_for_script(script: Script | None) -> dict[str, bytes]:
    """MSI в ADMIN$\\Temp только для старого скрипта установки, не для удаления."""
    if script is None:
        return {}
    if script.name == TIGHTVNC_UNINSTALL_SCRIPT_NAME:
        return {}
    body = script.content or ""
    if script.name != TIGHTVNC_INSTALL_SCRIPT_NAME and TIGHTVNC_PAYLOAD_SENTINEL not in body:
        return {}
    return extra_admin_files()


def extra_admin_files(root: str | Path | None = None) -> dict[str, bytes]:
    """Имя в ADMIN$ (Temp\\file.msi) → содержимое."""
    payload: dict[str, bytes] = {}
    for path in list_tightvnc_msis(root):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= 0 or size > _MAX_MSI_BYTES:
            logger.warning("skip VNC MSI %s (size %s)", path.name, size)
            continue
        try:
            data = path.read_bytes()
        except OSError:
            logger.warning("cannot read VNC MSI %s", path)
            continue
        payload[rf"Temp\{path.name}"] = data
    return payload


def list_vnc_agent_summaries(root: str | Path | None = None) -> list[dict[str, str | int]]:
    """Для Параметров: какие MSI лежат в vnc-agents/."""
    rows: list[dict[str, str | int]] = []
    for path in list_tightvnc_msis(root):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        rows.append({"name": path.name, "size": size})
    return rows


class TightVncEnsureError(ValueError):
    """Пароль/порт/IP для установки агента при подключении. Текст для UI."""


def remember_ensure_password(run_id: int, password: str) -> None:
    with _ensure_secrets_lock:
        _ensure_secrets[int(run_id)] = password


def take_ensure_password(run_id: int | None) -> str:
    if run_id is None:
        return ""
    with _ensure_secrets_lock:
        return _ensure_secrets.pop(int(run_id), "")


def validate_vnc_agent_password(password: str) -> str:
    text = password or ""
    if not text:
        raise TightVncEnsureError(
            "Задайте пароль VNC в Параметрах или на странице стола (до 8 символов)."
        )
    if len(text) > 8:
        raise TightVncEnsureError("Пароль TightVNC не длиннее 8 символов.")
    if _VNC_PASS_BAD.search(text):
        raise TightVncEnsureError(
            "В пароле есть пробел или служебный символ — смените в Параметрах."
        )
    return text


def build_ensure_script(*, password: str, bawh_ip: str, port: int) -> str:
    """Тело для PsExec: пароль только здесь, не в journal command_text."""
    secret = validate_vnc_agent_password(password)
    quoted = secret.replace("'", "''")
    ip = (bawh_ip or "").strip()
    port_num = int(port)
    text = TIGHTVNC_ENSURE_SCRIPT_BODY
    text = text.replace("$VncPassword = ''", f"$VncPassword = '{quoted}'", 1)
    text = text.replace("$BawhServerIp = ''", f"$BawhServerIp = '{ip}'", 1)
    text = text.replace("$Port = 5900", f"$Port = {port_num}", 1)
    return text


def _is_our_tightvnc_library_script(script: Script) -> bool:
    name = script.name or ""
    body = script.content or ""
    if name in {TIGHTVNC_INSTALL_SCRIPT_NAME, TIGHTVNC_UNINSTALL_SCRIPT_NAME}:
        return True
    return TIGHTVNC_PAYLOAD_SENTINEL in body or TIGHTVNC_UNINSTALL_SENTINEL in body


def _is_stock_uninstall_body(content: str) -> bool:
    text = content or ""
    if not text.strip():
        return True
    return TIGHTVNC_UNINSTALL_SENTINEL in text and "sc.exe delete tvnserver" in text


def _apply_uninstall_fields(script: Script) -> None:
    script.name = TIGHTVNC_UNINSTALL_SCRIPT_NAME
    script.description = TIGHTVNC_UNINSTALL_SCRIPT_DESCRIPTION
    script.target_os = "windows"
    script.interpreter = "powershell"
    script.run_as = RunAs.SYSTEM
    script.storage = "db"
    script.file_path = None
    script.content = TIGHTVNC_UNINSTALL_SCRIPT_BODY
    script.is_published = False


def seed_tightvnc_install_script(*, table_names: set[str]) -> None:
    """Скрипт удаления в библиотеке; старое тело установки заменяется."""
    if "scripts" not in table_names or "app_settings" not in table_names:
        return

    marker = db.session.get(AppSetting, TIGHTVNC_SCRIPT_SEED_KEY)
    if marker is not None and (marker.value or "").strip() == "1":
        return

    rows = db.session.scalars(
        select(Script).where(
            or_(
                Script.name == TIGHTVNC_UNINSTALL_SCRIPT_NAME,
                Script.name == TIGHTVNC_INSTALL_SCRIPT_NAME,
            )
        )
    ).all()
    uninstall = next(
        (row for row in rows if row.name == TIGHTVNC_UNINSTALL_SCRIPT_NAME), None
    )
    install = next(
        (row for row in rows if row.name == TIGHTVNC_INSTALL_SCRIPT_NAME), None
    )

    inserted = False
    updated = False
    if uninstall is None and install is None:
        db.session.add(
            Script(
                name=TIGHTVNC_UNINSTALL_SCRIPT_NAME,
                description=TIGHTVNC_UNINSTALL_SCRIPT_DESCRIPTION,
                target_os="windows",
                interpreter="powershell",
                run_as=RunAs.SYSTEM,
                storage="db",
                file_path=None,
                content=TIGHTVNC_UNINSTALL_SCRIPT_BODY,
                is_published=False,
                created_by_id=None,
            )
        )
        inserted = True
    elif uninstall is not None:
        if _is_stock_uninstall_body(uninstall.content or ""):
            _apply_uninstall_fields(uninstall)
            updated = True
        if install is not None and _is_our_tightvnc_library_script(install):
            db.session.delete(install)
            updated = True
    elif install is not None:
        _apply_uninstall_fields(install)
        updated = True

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
            TIGHTVNC_UNINSTALL_SCRIPT_NAME,
        )
    elif updated:
        logger.info(
            "ensure_schema: TightVNC library script is now silent uninstall",
        )
    else:
        logger.info(
            "ensure_schema: TightVNC uninstall script customized, seed marker set",
        )
