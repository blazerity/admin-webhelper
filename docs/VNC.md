# VNC в браузере (экспериментально)

Карточка устройства → **Рабочий стол** / **Подключиться**.
Debian открывает TCP на IP машины (порт 5900–5999) и отдаёт картинку в noVNC.
Gunicorn пиксели не гоняет: отдельный процесс `bawh-vnc` (~десятки МБ) + Nginx `/vnc/ws`.

Не путать с PsExec: скрипты — без GUI. VNC — консоль того, кто сидит за ПК.

## Что нужно на Windows

Агент **TightVNC** или **UltraVNC** (сервер, не Viewer). Файрвол: порт агента
только с IP сервера bAWH.

Текст для библиотеки «Скрипты» (PowerShell, SYSTEM, Windows). Подставьте
`$VncPassword` и `$BawhServerIp`:

```
$VncPassword = ''          # пароль агента; тот же можно сохранить в Параметрах
$BawhServerIp = ''         # IP Debian с bAWH; пусто — правило файрвола не ставим
$Port = 5900

$ErrorActionPreference = 'Stop'
function L($m) { Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $m) }

if (-not $VncPassword) {
  Write-Output 'Задайте $VncPassword в начале скрипта.'
  exit 2
}

L 'ищу установщик TightVNC в C:\Windows\Temp'
$msi = Get-ChildItem -Path "$env:SystemRoot\Temp" -Filter 'tightvnc*.msi' -ErrorAction SilentlyContinue |
  Select-Object -First 1
if (-not $msi) {
  Write-Output 'Положите tightvnc-*-setup-64bit.msi в C:\Windows\Temp (или скопируйте шарой).'
  exit 3
}

L ("ставлю {0}" -f $msi.Name)
$args = @(
  '/i', $msi.FullName, '/qn', '/norestart',
  'ADDLOCAL=Server',
  'SERVER_REGISTER_AS_SERVICE=1',
  'SERVER_ADD_FIREWALL_EXCEPTION=0',
  'SET_USEVNCAUTHENTICATION=1',
  ("VALUE=SET_PASSWORD={0}" -f $VncPassword),
  'SET_USECONTROLAUTHENTICATION=1',
  ("VALUE=SET_CONTROLPASSWORD={0}" -f $VncPassword)
)
$p = Start-Process -FilePath msiexec.exe -ArgumentList $args -Wait -PassThru
if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) {
  Write-Output ("msiexec код {0}" -f $p.ExitCode)
  exit 4
}

if ($BawhServerIp) {
  L ("файрвол {0}/tcp только с {1}" -f $Port, $BawhServerIp)
  netsh advfirewall firewall delete rule name='bAWH VNC' | Out-Null
  netsh advfirewall firewall add rule name='bAWH VNC' dir=in action=allow protocol=TCP localport=$Port remoteip=$BawhServerIp
}

L 'готово'
exit 0
```

MSI TightVNC на ПК скрипт сам не качает — положите его в `C:\Windows\Temp`
или на шару, которую SYSTEM читает. Лицензия — ваша.

## HTTPS

Параметры → **HTTPS-сертификат**: вставить PEM или загрузить файлы →
«Сохранить» → «Включить HTTPS». Редирект с :80 и `SESSION_COOKIE_SECURE`
включаются галками. Ключ лежит в `/opt/bawh/certs/`, не в PostgreSQL.

Буфер обмена в браузере на телефоне чаще работает уже по HTTPS.

## Если не открывается

- служба `bawh-vnc` запущена (Параметры → VNC);
- Nginx проксирует `/vnc/ws` (шаблон `deploy/nginx-bawh.conf`);
- на ПК слушает 5900, файрвол пускает Debian;
- билет живёт 90 секунд — обновите страницу стола, если долго ждали.
