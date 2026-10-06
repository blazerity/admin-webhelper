"""Маршрут веб-VNC: роли, тип устройства, билет в странице.

Запуск: python -m unittest tests.test_vnc_routes
"""

from __future__ import annotations

import unittest

from app import create_app
from app.authz import user_can_connect_vnc
from app.extensions import db
from app.models import Device, DeviceStatus, Sector, SectorAccess, User
from app.schema import ensure_schema


class VncRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app("testing")

    def setUp(self) -> None:
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        ensure_schema()
        sector = Sector(name="lab", description="")
        db.session.add(sector)
        db.session.flush()
        self.sector_id = sector.id
        self.pc = Device(
            ip="10.0.0.50",
            hostname="n-lab-01",
            sector_id=sector.id,
            last_status=DeviceStatus.ONLINE,
        )
        self.printer = Device(
            ip="10.0.0.90",
            hostname="p-lab-printer",
            sector_id=sector.id,
            last_status=DeviceStatus.ONLINE,
        )
        self.admin = User(username="admin", display_name="Admin", is_admin=True)
        self.operator = User(username="ops", display_name="Ops", is_operator=True)
        self.viewer = User(username="view", display_name="View", is_viewer=True)
        db.session.add_all([self.pc, self.printer, self.admin, self.operator, self.viewer])
        db.session.flush()
        db.session.add_all(
            [
                SectorAccess(
                    sector_id=sector.id, subject_type="user", subject_name="ops"
                ),
                SectorAccess(
                    sector_id=sector.id, subject_type="user", subject_name="view"
                ),
            ]
        )
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user: User) -> None:
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def test_roles(self) -> None:
        self.assertTrue(user_can_connect_vnc(self.admin))
        self.assertTrue(user_can_connect_vnc(self.operator))
        self.assertFalse(user_can_connect_vnc(self.viewer))

    def test_operator_gets_session_page(self) -> None:
        self.assertTrue(self.pc.shows_commands)
        self._login(self.operator)
        response = self.client.get(f"/devices/{self.pc.id}/vnc")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("noVNC", body)
        self.assertIn("/vnc/ws?token=", body)
        self.assertIn("10.0.0.50", body)

    def test_viewer_forbidden(self) -> None:
        self._login(self.viewer)
        response = self.client.get(f"/devices/{self.pc.id}/vnc")
        self.assertEqual(response.status_code, 403)

    def test_printer_not_found(self) -> None:
        self.assertFalse(self.printer.shows_commands)
        self._login(self.admin)
        response = self.client.get(f"/devices/{self.printer.id}/vnc")
        self.assertEqual(response.status_code, 404)

    def test_anonymous_redirects_to_login(self) -> None:
        response = self.client.get(f"/devices/{self.pc.id}/vnc")
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
