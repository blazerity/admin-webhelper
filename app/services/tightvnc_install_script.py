"""Каноническое тело тихой установки TightVNC и раздача MSI с сервера bAWH.

Админ кладёт ``tightvnc-*-setup-64bit.msi`` в каталог ``vnc-agents/``
(на сервере ``/opt/bawh/vnc-agents/``). При запуске скрипта файл копируется
на ПК через уже открытый ADMIN$; запасной путь — HTTP ``/vnc-agents/64bit.msi``.

Пароль в git не кладём. Сид не затирает строку, в которой уже подставлен пароль.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AppSetting, RunAs, Script

logger = logging.getLogger(__name__)

TIGHTVNC_INSTALL_SCRIPT_NAME = "TightVNC (тихая установка)"
TIGHTVNC_SCRIPT_SEED_KEY = "data_fix.seed_tightvnc_script_v174"
TIGHTVNC_PAYLOAD_SENTINEL = "# bAWH-payload: tightvnc-msi"
VNC_AGENTS_DIRNAME = "vnc-agents"
_SAFE_AGENT_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_MSI_BYTES = 40 * 1024 * 1024

TIGHTVNC_INSTALL_SCRIPT_DESCRIPTION = (
    "Тихая установка TightVNC Server как службы (порт 5900). "
    "MSI берётся с сервера bAWH (каталог vnc-agents/). "
    "Перед запуском заполните $VncPassword (до 8 символов, тот же что в "
    "Параметрах → VNC) и $BawhServerIp. Файрвол только с IP bAWH. "
    "После установки убирает ярлыки TightVNC из меню Пуск. "
    "После подстановки пароля можно опубликовать для операторов."
)

# PowerShell / SYSTEM / Windows / storage=db. Коды: 0 ок; 2 нет пароля;
# 3 нет IP bAWH; 4 нет MSI; 5 msiexec; 6 служба не поднялась; 1 прочая ошибка.
TIGHTVNC_INSTALL_SCRIPT_BODY = r"""$ErrorActionPreference = 'Stop'

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
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $dirs = @(
            (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs')
        )
        if ($env:ALLUSERSPROFILE) {
            $dirs += (Join-Path $env:ALLUSERSPROFILE 'Microsoft\Windows\Start Menu\Programs')
        }
        $usersRoot = Join-Path $env:SystemDrive 'Users'
        if (Test-Path -LiteralPath $usersRoot) {
            foreach ($userDir in @(Get-ChildItem -LiteralPath $usersRoot -Directory -Force -ErrorAction SilentlyContinue)) {
                $dirs += (Join-Path $userDir.FullName 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs')
            }
        }
        $seen = @{}
        $n = 0
        foreach ($programs in $dirs) {
            if (-not $programs) { continue }
            $key = $programs.ToLowerInvariant()
            if ($seen.ContainsKey($key)) { continue }
            $seen[$key] = $true
            if (-not (Test-Path -LiteralPath $programs)) { continue }
            $folder = Join-Path $programs 'TightVNC'
            if (Test-Path -LiteralPath $folder) {
                Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
                if (-not (Test-Path -LiteralPath $folder)) {
                    $n++
                    L ("Удалил папку меню Пуск: {0}" -f $folder)
                } else {
                    L ("Не удалось удалить папку меню Пуск: {0}" -f $folder)
                }
            }
            foreach ($lnk in @(Get-ChildItem -LiteralPath $programs -Filter '*TightVNC*.lnk' -Force -ErrorAction SilentlyContinue)) {
                Remove-Item -LiteralPath $lnk.FullName -Force -ErrorAction SilentlyContinue
                if (-not (Test-Path -LiteralPath $lnk.FullName)) {
                    $n++
                    L ("Удалил ярлык: {0}" -f $lnk.FullName)
                }
            }
        }
        if ($n -eq 0) {
            L 'Ярлыков TightVNC в меню Пуск не нашёл.'
        }
    } finally {
        $ErrorActionPreference = $prev
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
    """Файлы для ADMIN$\\Temp, если это скрипт установки TightVNC."""
    if script is None:
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


_OLD_VNC_PASS_MAGIC = "$magic = [byte[]](0x17, 0x52, 0x6B, 0x06, 0x23, 0x4E, 0x58, 0x07)"
# Закрывающая «}» функции в каноническом теле на колонке 0; вложенные — с отступом.
_ENCODER_BLOCK = re.compile(
    r"(?m)^function Convert-VncPasswordBytes\([^\n]*\) \{.*?\n\}",
    re.DOTALL,
)


def _canonical_password_encoder() -> str:
    match = _ENCODER_BLOCK.search(TIGHTVNC_INSTALL_SCRIPT_BODY)
    if match is None:
        raise RuntimeError("в каноническом теле нет Convert-VncPasswordBytes")
    return match.group(0)


def patch_vnc_password_encoder(content: str) -> str | None:
    """Заменить старый (неверный) DES-кодировщик, сохранив $VncPassword и IP."""
    text = content or ""
    if _OLD_VNC_PASS_MAGIC not in text:
        return None
    encoder = _canonical_password_encoder()
    patched, count = _ENCODER_BLOCK.subn(lambda _m: encoder, text, count=1)
    if count != 1:
        logger.warning(
            "TightVNC script has old password encoder, but Convert-VncPasswordBytes was not replaced"
        )
        return None
    return patched


_START_MENU_BLOCK = re.compile(
    r"(?m)^function Remove-TightVncStartMenu \{.*?\n\}",
    re.DOTALL,
)
_START_MENU_CALL = re.compile(r"(?m)^[ \t]*Remove-TightVncStartMenu[ \t]*$")
_MAIN_TRY = re.compile(r"(?m)^try \{")
_GOTOVO_LINE = re.compile(r'(?m)^(?P<ind>[ \t]*)L \("Готово\. Агент слушает')


def _canonical_start_menu_remover() -> str:
    match = _START_MENU_BLOCK.search(TIGHTVNC_INSTALL_SCRIPT_BODY)
    if match is None:
        raise RuntimeError("в каноническом теле нет Remove-TightVncStartMenu")
    return match.group(0)


def patch_tightvnc_start_menu(content: str) -> str | None:
    """Вставить удаление ярлыков меню Пуск, сохранив $VncPassword и IP."""
    text = content or ""
    has_fn = "function Remove-TightVncStartMenu" in text
    has_call = _START_MENU_CALL.search(text) is not None
    if has_fn and has_call:
        return None
    if not has_fn:
        func = _canonical_start_menu_remover()
        text, count = _MAIN_TRY.subn(lambda m: func + "\n\n" + m.group(0), text, count=1)
        if count != 1:
            logger.warning(
                "TightVNC script: cannot insert Remove-TightVncStartMenu function"
            )
            return None
    if not has_call:
        text, count = _GOTOVO_LINE.subn(
            lambda m: m.group("ind") + "Remove-TightVncStartMenu\n" + m.group(0),
            text,
            count=1,
        )
        if count != 1:
            logger.warning(
                "TightVNC script: cannot insert Remove-TightVncStartMenu call"
            )
            return None
    return text


def _is_stock_body(content: str) -> bool:
    """Пустой пароль и каноническое/прошлое тело — можно обновить сидом."""
    text = content or ""
    if not text.strip():
        return True
    if "$VncPassword = ''" not in text and '$VncPassword = ""' not in text:
        return False
    if "SET_PASSWORD=1" not in text:
        return False
    return TIGHTVNC_PAYLOAD_SENTINEL in text or (
        "$MsiPath = ''" in text and "Ищу MSI TightVNC" in text
    )


def seed_tightvnc_install_script(*, table_names: set[str]) -> None:
    """Вставить или обновить стоковое тело, если админ ещё не подставлял пароль."""
    if "scripts" not in table_names or "app_settings" not in table_names:
        return

    marker = db.session.get(AppSetting, TIGHTVNC_SCRIPT_SEED_KEY)
    if marker is not None and (marker.value or "").strip() == "1":
        return

    existing = db.session.scalar(
        select(Script).where(Script.name == TIGHTVNC_INSTALL_SCRIPT_NAME)
    )
    inserted = False
    updated = False
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
    elif _is_stock_body(existing.content or ""):
        existing.description = TIGHTVNC_INSTALL_SCRIPT_DESCRIPTION
        existing.target_os = "windows"
        existing.interpreter = "powershell"
        existing.run_as = RunAs.SYSTEM
        existing.storage = "db"
        existing.content = TIGHTVNC_INSTALL_SCRIPT_BODY
        existing.is_published = False
        updated = True
    else:
        patched = existing.content or ""
        changed = False
        encoded = patch_vnc_password_encoder(patched)
        if encoded is not None:
            patched = encoded
            changed = True
        with_menu = patch_tightvnc_start_menu(patched)
        if with_menu is not None:
            patched = with_menu
            changed = True
        if changed:
            existing.content = patched
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
            TIGHTVNC_INSTALL_SCRIPT_NAME,
        )
    elif updated:
        logger.info(
            "ensure_schema: refreshed TightVNC script body (start menu / encoder / stock)",
        )
    else:
        logger.info(
            "ensure_schema: TightVNC script customized, seed marker set",
        )
