"""Сервисный слой: бизнес-логика без HTTP.

Маршруты (app/routes) принимают запрос и вызывают функции отсюда.
Модели (app/models) только описывают таблицы.

Карта модулей
-------------
net_utils.py           разбор IP/CIDR и MAC
crypto_service.py      Fernet-шифрование пароля PsExec
credential_service.py  откуда читать учётку удалённого администратора
settings_service.py    интервал опроса и прочие ключи app_settings
ldap_service.py        проверка пароля в LDAP и список групп
sector_service.py      создание и правка секторов
ping_service.py        ICMP-пинг и запись истории
discovery_service.py   обратный DNS и MAC из соседской таблицы ARP
scheduler_service.py   цикл опроса для отдельного процесса
search_service.py      поиск устройств по IP, MAC, hostname
psexec_service.py      удалённая команда на Windows
script_service.py      библиотека скриптов и запуск в фоновом потоке
update_service.py      обновление кода из публичного git и откат на копию

Куда расти
----------
* Планировщик заменяется на Celery: задача вызывает ping_service.poll_all_sectors.
* PsExec и скрипты — кандидат на отдельный сервис «remote-exec».
  Веб тогда только создаёт строку script_runs и читает лог.
* Поиск по подстроке при росте таблицы devices можно ускорить
  расширением pg_trgm, не меняя контракт search_service.
"""
