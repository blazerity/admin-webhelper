# Установщики TightVNC

Положите сюда MSI с [tightvnc.com/download](https://www.tightvnc.com/download.php)
(лицензия — ваша). На сервере это `/opt/bawh/vnc-agents/`.

- `tightvnc-*-setup-64bit.msi` — основной, для 64-битных Windows
- `tightvnc-*-setup-32bit.msi` — если есть 32-битные ПК

Файлы `.msi` / `.exe` в git не входят и при обновлении bAWH не стираются.

При **Подключить** на карточке bAWH копирует MSI на ПК через PsExec (`ADMIN$`)
и при необходимости качает `http://<IP-bAWH>/vnc-agents/64bit.msi`.
Снять агент — скрипт **TightVNC (тихое удаление)** в библиотеке.
