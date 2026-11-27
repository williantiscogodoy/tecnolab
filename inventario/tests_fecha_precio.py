from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from .models import Categoria, Producto
from .roles import GRUPO_TECNICO


class ActualizarFechaPrecioTests(TestCase):
    def setUp(self):
        self.tec_cat = Categoria.objects.create(nombre="Pantallas", es_tecnica=True)
        self.otra_cat = Categoria.objects.create(nombre="Accesorios", es_tecnica=False)
        self.vieja = timezone.now() - timedelta(days=90)
        self.p = Producto.objects.create(nombre="Pantalla A", categoria=self.tec_cat, precio_compra=40, precio_venta=105)
        self.o = Producto.objects.create(nombre="Cable", categoria=self.otra_cat, precio_compra=2, precio_venta=6)
        Producto.objects.filter(pk__in=[self.p.pk, self.o.pk]).update(precio_venta_actualizado=self.vieja)
        self.admin = User.objects.create_user("adm", password="x", is_staff=True)
        self.tec = User.objects.create_user("tec", password="x")
        self.tec.groups.add(Group.objects.get_or_create(name=GRUPO_TECNICO)[0])

    def url(self, p):
        return f"/productos/{p.pk}/actualizar-fecha/"

    def fecha(self, p):
        return Producto.objects.get(pk=p.pk).precio_venta_actualizado

    def test_el_admin_actualiza_la_fecha_sin_tocar_nada_mas(self):
        self.client.force_login(self.admin)
        r = self.client.post(self.url(self.p))
        self.assertRedirects(r, f"/productos/{self.p.pk}/editar/", fetch_redirect_response=False)
        p = Producto.objects.get(pk=self.p.pk)
        self.assertGreater(p.precio_venta_actualizado, self.vieja + timedelta(days=89))
        self.assertEqual((p.precio_venta, p.precio_compra, p.stock, p.nombre), (105, 40, 0, "Pantalla A"))

    def test_el_admin_puede_con_cualquier_categoria(self):
        self.client.force_login(self.admin)
        self.client.post(self.url(self.o))
        self.assertGreater(self.fecha(self.o), self.vieja + timedelta(days=89))

    def test_el_tecnico_actualiza_un_producto_tecnico_y_vuelve_a_su_edicion(self):
        self.client.force_login(self.tec)
        r = self.client.post(self.url(self.p))
        self.assertRedirects(r, f"/tecnico/producto/{self.p.pk}/editar/", fetch_redirect_response=False)
        self.assertGreater(self.fecha(self.p), self.vieja + timedelta(days=89))

    def test_el_tecnico_no_puede_con_categorias_no_tecnicas_ni_inactivos(self):
        self.client.force_login(self.tec)
        self.assertEqual(self.client.post(self.url(self.o)).status_code, 404)
        Producto.objects.filter(pk=self.p.pk).update(activo=False)
        self.assertEqual(self.client.post(self.url(self.p)).status_code, 404)
        self.assertEqual((self.fecha(self.o), self.fecha(self.p)), (self.vieja, self.vieja))

    def test_solo_acepta_post_y_pide_sesion(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url(self.p)).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.url(self.p)).status_code, 302)
        self.assertEqual(self.fecha(self.p), self.vieja)

    def test_funciona_si_el_producto_nunca_tuvo_fecha(self):
        Producto.objects.filter(pk=self.p.pk).update(precio_venta_actualizado=None)
        self.client.force_login(self.admin)
        self.client.post(self.url(self.p))
        self.assertIsNotNone(self.fecha(self.p))

    def test_el_boton_aparece_al_editar_y_no_al_crear_para_los_dos_roles(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(f"/productos/{self.p.pk}/editar/"), "Actualizar solo fecha")
        self.assertNotContains(self.client.get("/productos/nuevo/"), "Actualizar solo fecha")
        self.client.force_login(self.tec)
        r = self.client.get(f"/tecnico/producto/{self.p.pk}/editar/")
        self.assertContains(r, "Actualizar solo fecha")
        self.assertContains(r, 'action="/productos/%d/actualizar-fecha/"' % self.p.pk)
        self.assertNotContains(self.client.get("/tecnico/producto/nuevo/"), "Actualizar solo fecha")
