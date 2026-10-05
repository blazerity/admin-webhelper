"""Очистка журнала PsExec: PowerShell CLIXML не должен попадать в лог.

Запуск: python -m unittest tests.test_psexec_output
"""

from __future__ import annotations

import unittest

from app.services.psexec_service import _combine, _split_powershell_clixml, _strip_powershell_clixml

_CLIXML = (
    '#< CLIXML\n'
    '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
    '<Obj S="information" RefId="1"><ToString>НАСТРОЕН</ToString></Obj>'
    "</Objs>\n"
)


class StripPowershellClixmlTests(unittest.TestCase):
    def test_keeps_plain_log(self) -> None:
        text = "=== Настройка Ассистента ===\nНАСТРОЕН\n"
        self.assertEqual(_strip_powershell_clixml(text), text)
        self.assertEqual(_split_powershell_clixml(text), (text, False))

    def test_cuts_at_clixml_marker(self) -> None:
        readable = "Конфиг записан\nAstService запущена\nКонфигурация добавлена корректно"
        keep, drop = _split_powershell_clixml(readable + "\n" + _CLIXML)
        self.assertTrue(drop)
        self.assertEqual(keep, readable)

    def test_drops_clixml_only_chunk(self) -> None:
        keep, drop = _split_powershell_clixml(_CLIXML)
        self.assertTrue(drop)
        self.assertEqual(keep, "")

    def test_drops_objs_root_without_marker(self) -> None:
        blob = (
            '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
            "<Obj S=\"warning\">ожидание</Obj></Objs>"
        )
        keep, drop = _split_powershell_clixml("готово\n" + blob)
        self.assertTrue(drop)
        self.assertEqual(keep, "готово")

    def test_combine_stdout_plus_clixml_stderr(self) -> None:
        stdout = "=== Настройка Ассистента ===\nКонфигурация добавлена корректно\n"
        combined = _combine(stdout.encode("utf-8"), _CLIXML.encode("utf-8"))
        self.assertEqual(
            combined,
            "=== Настройка Ассистента ===\nКонфигурация добавлена корректно",
        )
        self.assertNotIn("CLIXML", combined)
        self.assertNotIn("<Objs", combined)

    def test_combine_empty_stderr(self) -> None:
        self.assertEqual(_combine(b"ok\n", b""), "ok\n")


if __name__ == "__main__":
    unittest.main()
