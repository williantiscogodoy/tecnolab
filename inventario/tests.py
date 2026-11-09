from unittest import mock

from django.core.files.storage import FileSystemStorage
from django.test import SimpleTestCase, TestCase, override_settings

from .imagenes import optimizada
from .models import Categoria, Producto, ProductoImagen

CLOUD = "https://res.cloudinary.com/demo/image/upload/v1/media/productos/foto.png"


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


class OptimizarImagenesTests(SimpleTestCase):
    def test_cloudinary_se_reduce_y_comprime(self):
        self.assertEqual(
            optimizada(CLOUD, 400),
            "https://res.cloudinary.com/demo/image/upload/c_limit,w_400,q_auto,f_auto/v1/media/productos/foto.png",
        )

    def test_no_aplica_la_transformacion_dos_veces(self):
        una_vez = optimizada(CLOUD, 400)
        self.assertEqual(optimizada(una_vez, 800), una_vez)

    def test_url_local_o_vacia_no_cambia(self):
        self.assertEqual(optimizada("/media/productos/foto.png", 400), "/media/productos/foto.png")
        self.assertIsNone(optimizada(None, 400))
        self.assertEqual(optimizada("", 400), "")


class ImagenesDeProductoTests(TestCase):
    """Con Cloudinary las listas piden miniaturas y la galería una versión mediana."""

    def setUp(self):
        cat = Categoria.objects.create(nombre="Pruebas")
        self.p = Producto.objects.create(
            nombre="Con foto", categoria=cat, precio_compra=1, precio_venta=2, imagen="productos/foto.png"
        )
        ProductoImagen.objects.create(producto=self.p, imagen="productos/ref.png")

    def test_miniaturas_y_galeria(self):
        with mock.patch.object(FileSystemStorage, "url", return_value=CLOUD):
            self.assertIn("c_limit,w_400,", self.p.portada_url)
            self.assertIn("c_limit,w_400,", self.p.imagen_miniatura)
            self.assertIn("c_limit,w_800,", self.p.imagen_vista)
            self.assertIn("c_limit,w_400,", self.p.imagenes.first().miniatura)
            self.assertTrue(all("c_limit,w_1400," in u for u in self.p.galeria))
            self.assertEqual(len(self.p.galeria), 2)

    def test_sin_cloudinary_las_urls_quedan_igual(self):
        self.assertEqual(self.p.portada_url, "/media/productos/foto.png")
        self.assertEqual(self.p.galeria[0], "/media/productos/foto.png")

    def test_producto_sin_imagen(self):
        sin = Producto.objects.create(
            nombre="Sin foto", categoria=self.p.categoria, precio_compra=1, precio_venta=2
        )
        self.assertIsNone(sin.portada_url)
        self.assertIsNone(sin.imagen_miniatura)
        self.assertEqual(sin.galeria, [])
