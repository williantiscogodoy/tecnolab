from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from .imagenes import optimizada
from .models import Categoria, Movimiento, Producto, ProductoImagen
from .roles import GRUPO_TECNICO

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


class TecnicoCreaProductosTests(TestCase):
    """El técnico da de alta productos (solo categorías técnicas, sin precio de compra)."""

    URL = "/tecnico/producto/nuevo/"

    def setUp(self):
        self.tec_cat = Categoria.objects.create(nombre="Pantallas", es_tecnica=True)
        self.otra_cat = Categoria.objects.create(nombre="Accesorios", es_tecnica=False)
        self.tecnico = User.objects.create_user("tec", password="x")
        self.tecnico.groups.add(Group.objects.get_or_create(name=GRUPO_TECNICO)[0])
        self.admin = User.objects.create_user("adm", password="x", is_staff=True)
        self.client.force_login(self.tecnico)

    def datos(self, **extra):
        d = {"nombre": "Pantalla A10", "sku": "PA10", "categoria": self.tec_cat.pk, "descripcion": "",
             "precio_venta": "150", "stock": "4", "stock_minimo": "1"}
        d.update(extra)
        return d

    def test_el_formulario_no_pide_precio_de_compra(self):
        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("precio_compra", r.context["form"].fields)
        self.assertContains(r, "lo completa el administrador")

    def test_solo_ofrece_categorias_tecnicas(self):
        r = self.client.get(self.URL)
        self.assertEqual(list(r.context["form"].fields["categoria"].queryset), [self.tec_cat])

    def test_crea_producto_con_costo_cero_activo_y_stock_como_movimiento(self):
        r = self.client.post(self.URL, self.datos())
        self.assertRedirects(r, "/tecnico/", fetch_redirect_response=False)
        p = Producto.objects.get(sku="PA10")
        self.assertEqual(p.precio_compra, 0)
        self.assertTrue(p.activo)
        self.assertEqual(p.stock, 4)
        mov = Movimiento.objects.get(producto=p)
        self.assertEqual((mov.tipo, mov.cantidad, mov.usuario, mov.nota), (Movimiento.ENTRADA, 4, self.tecnico, "Stock inicial"))

    def test_ignora_un_precio_de_compra_enviado_a_mano(self):
        self.client.post(self.URL, self.datos(precio_compra="99", activo=""))
        self.assertEqual(Producto.objects.get(sku="PA10").precio_compra, 0)

    def test_rechaza_categoria_no_tecnica(self):
        r = self.client.post(self.URL, self.datos(categoria=self.otra_cat.pk))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Producto.objects.exists())

    def test_sku_repetido_da_error_y_no_duplica(self):
        self.client.post(self.URL, self.datos())
        r = self.client.post(self.URL, self.datos(nombre="Otra"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Producto.objects.count(), 1)

    def test_con_imagen_de_referencia(self):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO(); Image.new("RGB", (20, 20), "red").save(buf, "PNG")
        foto = SimpleUploadedFile("f.png", buf.getvalue(), content_type="image/png")
        with override_settings(MEDIA_ROOT="/tmp/media_tests_tecnico"):
            self.client.post(self.URL, self.datos(referencias=foto))
        self.assertEqual(ProductoImagen.objects.filter(producto__sku="PA10").count(), 1)

    def test_sin_categorias_tecnicas_avisa_y_vuelve_al_panel(self):
        Categoria.objects.filter(es_tecnica=True).update(es_tecnica=False)
        r = self.client.get(self.URL)
        self.assertRedirects(r, "/tecnico/", fetch_redirect_response=False)

    def test_el_administrador_va_al_formulario_completo(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(self.URL), "/productos/nuevo/", fetch_redirect_response=False)

    def test_sin_sesion_va_al_login_y_sin_rol_es_403(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.URL).status_code, 302)
        self.client.force_login(User.objects.create_user("sinrol", password="x"))
        self.assertEqual(self.client.get(self.URL).status_code, 403)

    def test_el_tecnico_sigue_sin_poder_usar_el_alta_del_administrador(self):
        r = self.client.post("/productos/nuevo/", self.datos())
        self.assertRedirects(r, "/tecnico/", fetch_redirect_response=False)
        self.assertFalse(Producto.objects.exists())

    def test_el_administrador_ve_falta_costo_y_puede_filtrar(self):
        self.client.post(self.URL, self.datos())
        Producto.objects.create(nombre="Con costo", categoria=self.otra_cat, precio_compra=5, precio_venta=9)
        self.client.force_login(self.admin)
        r = self.client.get("/productos/")
        self.assertContains(r, "Falta costo")
        r = self.client.get("/productos/?estado=sin_costo")
        nombres = [p.nombre for p in r.context["page_obj"]]
        self.assertEqual(nombres, ["Pantalla A10"])
