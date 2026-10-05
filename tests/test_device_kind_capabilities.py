"""Вкладки/поля карточки устройства по типу."""

from __future__ import annotations

import unittest

from app.services.device_kind import (
    KIND_CAMERA,
    KIND_DESKTOP,
    KIND_FIREWALL,
    KIND_NOTEBOOK,
    KIND_OTHER,
    KIND_PRINTER,
    KIND_ROUTER,
    KIND_SERVER,
    KIND_VDS,
    kind_shows_accounts,
    kind_shows_commands,
    kind_shows_hardware,
    kind_shows_mac,
)


class DeviceKindCapabilityTests(unittest.TestCase):
    def test_accounts_only_notebook_and_desktop(self) -> None:
        self.assertTrue(kind_shows_accounts(KIND_NOTEBOOK))
        self.assertTrue(kind_shows_accounts(KIND_DESKTOP))
        for kind in (
            KIND_SERVER,
            KIND_VDS,
            KIND_CAMERA,
            KIND_PRINTER,
            KIND_ROUTER,
            KIND_FIREWALL,
            KIND_OTHER,
        ):
            self.assertFalse(kind_shows_accounts(kind), kind)

    def test_mac_for_workstations_and_peripherals(self) -> None:
        for kind in (KIND_NOTEBOOK, KIND_DESKTOP, KIND_CAMERA, KIND_PRINTER, KIND_ROUTER):
            self.assertTrue(kind_shows_mac(kind), kind)
        for kind in (KIND_SERVER, KIND_VDS, KIND_FIREWALL, KIND_OTHER):
            self.assertFalse(kind_shows_mac(kind), kind)

    def test_hardware_and_commands_windows_hosts(self) -> None:
        for kind in (KIND_NOTEBOOK, KIND_DESKTOP, KIND_SERVER):
            self.assertTrue(kind_shows_hardware(kind), kind)
            self.assertTrue(kind_shows_commands(kind), kind)
        for kind in (
            KIND_VDS,
            KIND_CAMERA,
            KIND_PRINTER,
            KIND_ROUTER,
            KIND_FIREWALL,
            KIND_OTHER,
        ):
            self.assertFalse(kind_shows_hardware(kind), kind)
            self.assertFalse(kind_shows_commands(kind), kind)


if __name__ == "__main__":
    unittest.main()
