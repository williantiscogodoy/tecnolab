from django.test import TestCase, override_settings


class SaludTests(TestCase):
    def test_salud_liviana_no_pide_login_ni_toca_la_base(self):
        with self.assertNumQueries(0):
            r = self.client.get("/salud/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["estado"], "ok")
        self.assertIn("no-cache", r["Cache-Control"])

    def test_salud_acepta_head(self):
        self.assertEqual(self.client.head("/salud/").status_code, 200)

    def test_salud_db_verifica_la_base(self):
        r = self.client.get("/salud/db/")
        self.assertEqual(r.status_code, 200)
        datos = r.json()
        self.assertEqual(datos["db"], "ok")
        self.assertIn(datos["almacenamiento"], ("local", "cloudinary"))

    @override_settings(SALUD_TOKEN="secreto")
    def test_salud_db_con_token(self):
        self.assertEqual(self.client.get("/salud/db/").status_code, 403)
        self.assertEqual(self.client.get("/salud/db/?token=mal").status_code, 403)
        self.assertEqual(self.client.get("/salud/db/?token=secreto").status_code, 200)

    @override_settings(SALUD_TOKEN="secreto")
    def test_salud_liviana_no_usa_token(self):
        self.assertEqual(self.client.get("/salud/").status_code, 200)
