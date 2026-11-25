from django.contrib.auth.models import Group, User
from django.test import TestCase

from .models import Categoria, Movimiento, Producto, Reposicion, ReposicionItem
from .roles import GRUPO_TECNICO


class ReposicionBase(TestCase):
    def setUp(self):
        self.cat_tec = Categoria.objects.create(nombre="Pantallas", es_tecnica=True)
        self.cat_otra = Categoria.objects.create(nombre="Accesorios", es_tecnica=False)
        # p1 y p3 están bajos; p2 tiene stock de sobra. p3 no es de categoría técnica.
        self.p1 = Producto.objects.create(nombre="Pantalla Uno", categoria=self.cat_tec, precio_compra="37.77",
                                          precio_venta="80", stock=1, stock_minimo=3)
        self.p2 = Producto.objects.create(nombre="Flex Dos", categoria=self.cat_tec, precio_compra="5",
                                          precio_venta="12", stock=10, stock_minimo=2)
        self.p3 = Producto.objects.create(nombre="Cable Tres", categoria=self.cat_otra, precio_compra="2",
                                          precio_venta="6", stock=0, stock_minimo=2)
        self.admin = User.objects.create_user("adm", password="x", is_staff=True)
        self.tec = User.objects.create_user("tec", password="x")
        self.tec.groups.add(Group.objects.get_or_create(name=GRUPO_TECNICO)[0])
        self.tec2 = User.objects.create_user("tec2", password="x")
        self.tec2.groups.add(Group.objects.get_or_create(name=GRUPO_TECNICO)[0])

    def entrar(self, usuario):
        self.client.force_login(usuario)

    def lista(self, creador=None, **items):
        """Crea una lista con {producto: (cantidad, pedido_por)}."""
        lista = Reposicion.objects.create(creado_por=creador or self.admin)
        for producto, (cantidad, quien) in items.items():
            ReposicionItem.objects.create(reposicion=lista, producto=getattr(self, producto),
                                          cantidad_pedida=cantidad, pedido_por=quien)
        return lista

    def stock(self, nombre):
        return Producto.objects.get(pk=getattr(self, nombre).pk).stock


class CrearListaTests(ReposicionBase):
    def test_crear_lista_vacia(self):
        self.entrar(self.admin)
        r = self.client.post("/reposicion/nueva/")
        lista = Reposicion.objects.get()
        self.assertRedirects(r, f"/reposicion/{lista.pk}/", fetch_redirect_response=False)
        self.assertEqual((lista.estado, lista.creado_por, lista.items.count()), ("ABIERTA", self.admin, 0))

    def test_crear_solo_acepta_post(self):
        self.entrar(self.admin)
        self.assertEqual(self.client.get("/reposicion/nueva/").status_code, 405)

    def test_con_stock_bajo_el_admin_recibe_todos_con_cantidad_sugerida(self):
        self.entrar(self.admin)
        self.client.post("/reposicion/nueva/", {"bajos": "1"})
        items = {i.producto_id: i.cantidad_pedida for i in Reposicion.objects.get().items.all()}
        # sugerido = doble del mínimo menos el stock actual (al menos 1)
        self.assertEqual(items, {self.p1.pk: 5, self.p3.pk: 4})

    def test_con_stock_bajo_el_tecnico_solo_recibe_categorias_tecnicas(self):
        self.entrar(self.tec)
        self.client.post("/reposicion/nueva/", {"bajos": "1"})
        lista = Reposicion.objects.get()
        self.assertEqual([i.producto for i in lista.items.all()], [self.p1])
        self.assertEqual(lista.items.get().pedido_por, self.tec)

    def test_sin_stock_bajo_crea_lista_vacia_y_avisa(self):
        Producto.objects.filter(pk__in=[self.p1.pk, self.p3.pk]).update(stock=50)
        self.entrar(self.admin)
        r = self.client.post("/reposicion/nueva/", {"bajos": "1"}, follow=True)
        self.assertEqual(Reposicion.objects.get().items.count(), 0)
        self.assertContains(r, "no hay productos con stock bajo")


class AccesoTests(ReposicionBase):
    def test_sin_sesion_va_al_login(self):
        for url in ("/reposicion/", f"/reposicion/{self.lista().pk}/"):
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_usuario_sin_rol_es_403(self):
        self.entrar(User.objects.create_user("sinrol", password="x"))
        self.assertEqual(self.client.get("/reposicion/").status_code, 403)

    def test_el_menu_muestra_reposicion_a_los_dos_roles(self):
        for u in (self.admin, self.tec):
            self.entrar(u)
            self.assertContains(self.client.get("/reposicion/"), "Reposición</a>")

    def test_el_panel_general_ofrece_crear_la_lista(self):
        self.entrar(self.admin)
        self.assertContains(self.client.get("/"), "Crear lista de reposición")


class DetalleTests(ReposicionBase):
    def test_el_tecnico_no_ve_el_precio_de_compra_y_el_admin_si(self):
        lista = self.lista(p1=(5, self.admin))
        self.entrar(self.tec)
        r = self.client.get(f"/reposicion/{lista.pk}/")
        self.assertContains(r, "80,00")        # precio de venta
        self.assertNotContains(r, "37,77")     # precio de compra
        self.assertNotContains(r, "Costo estimado")
        self.entrar(self.admin)
        r = self.client.get(f"/reposicion/{lista.pk}/")
        self.assertContains(r, "37,77")
        self.assertContains(r, "Costo estimado")
        self.assertContains(r, "188,85")       # 5 × 37,77

    def test_el_buscador_ofrece_primero_los_de_stock_bajo_y_no_repite_los_de_la_lista(self):
        lista = self.lista(p1=(5, self.admin))
        self.entrar(self.admin)
        r = self.client.get(f"/reposicion/{lista.pk}/")
        nombres = [p.nombre for p in r.context["resultados"]]
        self.assertEqual(nombres, ["Cable Tres"])      # p1 ya está en la lista, p2 no está bajo
        r = self.client.get(f"/reposicion/{lista.pk}/?ver=todos")
        self.assertEqual({p.nombre for p in r.context["resultados"]}, {"Flex Dos", "Cable Tres"})

    def test_buscar_por_nombre_o_sku_encuentra_aunque_no_este_bajo(self):
        lista = self.lista()
        self.entrar(self.admin)
        r = self.client.get(f"/reposicion/{lista.pk}/?q=flex")
        self.assertEqual([p.nombre for p in r.context["resultados"]], ["Flex Dos"])

    def test_el_tecnico_no_puede_pedir_ni_ver_productos_de_otras_categorias(self):
        lista = self.lista()
        self.entrar(self.tec)
        r = self.client.get(f"/reposicion/{lista.pk}/?ver=todos")
        self.assertNotIn("Cable Tres", [p.nombre for p in r.context["resultados"]])
        r = self.client.post(f"/reposicion/{lista.pk}/agregar/", {"producto": self.p3.pk, "cantidad": 2})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(lista.items.count(), 0)


class PedirProductosTests(ReposicionBase):
    def agregar(self, lista, producto, cantidad):
        return self.client.post(f"/reposicion/{lista.pk}/agregar/", {"producto": producto.pk, "cantidad": cantidad})

    def test_agregar_un_producto(self):
        lista = self.lista()
        self.entrar(self.tec)
        self.agregar(lista, self.p2, 4)
        item = lista.items.get()
        self.assertEqual((item.producto, item.cantidad_pedida, item.pedido_por), (self.p2, 4, self.tec))

    def test_cantidades_invalidas_no_se_guardan(self):
        lista = self.lista()
        self.entrar(self.admin)
        for malo in ("0", "-3", "abc", "", "100001", "2.5"):
            self.agregar(lista, self.p2, malo)
        self.assertEqual(lista.items.count(), 0)

    def test_agregar_de_nuevo_lo_propio_actualiza_la_cantidad(self):
        lista = self.lista(p2=(4, self.tec))
        self.entrar(self.tec)
        self.agregar(lista, self.p2, 9)
        self.assertEqual(lista.items.get().cantidad_pedida, 9)

    def test_el_tecnico_no_pisa_lo_que_pidio_otro(self):
        lista = self.lista(p2=(4, self.admin))
        self.entrar(self.tec)
        r = self.client.post(f"/reposicion/{lista.pk}/agregar/", {"producto": self.p2.pk, "cantidad": 9}, follow=True)
        self.assertEqual(lista.items.get().cantidad_pedida, 4)
        self.assertContains(r, "ya está en la lista")

    def test_el_tecnico_cambia_y_quita_solo_lo_suyo(self):
        lista = self.lista(p1=(5, self.tec), p2=(4, self.admin))
        suyo = lista.items.get(producto=self.p1)
        ajeno = lista.items.get(producto=self.p2)
        self.entrar(self.tec)
        self.client.post(f"/reposicion/{lista.pk}/item/{suyo.pk}/editar/", {"cantidad": 7})
        self.client.post(f"/reposicion/{lista.pk}/item/{ajeno.pk}/editar/", {"cantidad": 99})
        self.client.post(f"/reposicion/{lista.pk}/item/{ajeno.pk}/quitar/")
        suyo.refresh_from_db(); ajeno.refresh_from_db()
        self.assertEqual((suyo.cantidad_pedida, ajeno.cantidad_pedida), (7, 4))
        self.client.post(f"/reposicion/{lista.pk}/item/{suyo.pk}/quitar/")
        self.assertEqual(list(lista.items.all()), [ajeno])

    def test_otro_tecnico_tampoco_toca_lo_ajeno(self):
        lista = self.lista(p1=(5, self.tec))
        item = lista.items.get()
        self.entrar(self.tec2)
        self.client.post(f"/reposicion/{lista.pk}/item/{item.pk}/editar/", {"cantidad": 50})
        self.client.post(f"/reposicion/{lista.pk}/item/{item.pk}/quitar/")
        item.refresh_from_db()
        self.assertEqual(item.cantidad_pedida, 5)

    def test_el_admin_toca_cualquier_producto(self):
        lista = self.lista(p1=(5, self.tec))
        item = lista.items.get()
        self.entrar(self.admin)
        self.client.post(f"/reposicion/{lista.pk}/item/{item.pk}/editar/", {"cantidad": 11})
        item.refresh_from_db()
        self.assertEqual(item.cantidad_pedida, 11)
        self.client.post(f"/reposicion/{lista.pk}/item/{item.pk}/quitar/")
        self.assertEqual(lista.items.count(), 0)

    def test_un_item_de_otra_lista_no_se_puede_tocar_por_url(self):
        otra = self.lista(p1=(5, self.admin))
        mia = self.lista()
        self.entrar(self.admin)
        r = self.client.post(f"/reposicion/{mia.pk}/item/{otra.items.get().pk}/quitar/")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(otra.items.count(), 1)


class CancelarTests(ReposicionBase):
    def test_el_creador_cancela_la_suya(self):
        lista = self.lista(creador=self.tec, p1=(5, self.tec))
        self.entrar(self.tec)
        self.client.post(f"/reposicion/{lista.pk}/cancelar/")
        lista.refresh_from_db()
        self.assertEqual((lista.estado, lista.cerrado_por), ("CANCELADA", self.tec))
        self.assertIsNotNone(lista.cerrado_en)

    def test_el_tecnico_no_cancela_la_de_otro_pero_el_admin_si(self):
        lista = self.lista(creador=self.admin)
        self.entrar(self.tec)
        self.client.post(f"/reposicion/{lista.pk}/cancelar/")
        lista.refresh_from_db()
        self.assertEqual(lista.estado, "ABIERTA")
        self.entrar(self.admin)
        self.client.post(f"/reposicion/{lista.pk}/cancelar/")
        lista.refresh_from_db()
        self.assertEqual(lista.estado, "CANCELADA")

    def test_una_lista_cancelada_ya_no_se_modifica(self):
        lista = self.lista(p1=(5, self.admin))
        Reposicion.objects.filter(pk=lista.pk).update(estado="CANCELADA")
        item = lista.items.get()
        self.entrar(self.admin)
        self.client.post(f"/reposicion/{lista.pk}/agregar/", {"producto": self.p2.pk, "cantidad": 2})
        self.client.post(f"/reposicion/{lista.pk}/item/{item.pk}/editar/", {"cantidad": 9})
        self.client.post(f"/reposicion/{lista.pk}/item/{item.pk}/quitar/")
        item.refresh_from_db()
        self.assertEqual((lista.items.count(), item.cantidad_pedida), (1, 5))


class RecibirTests(ReposicionBase):
    def setUp(self):
        super().setUp()
        self.l = self.lista(p1=(5, self.admin), p2=(3, self.tec), p3=(4, self.admin))
        self.i1 = self.l.items.get(producto=self.p1)
        self.i2 = self.l.items.get(producto=self.p2)
        self.i3 = self.l.items.get(producto=self.p3)
        self.url = f"/reposicion/{self.l.pk}/recibir/"

    def datos(self, **extra):
        """Por defecto: p1 llegó completo, p2 llegó 1 de 3, p3 no llegó."""
        d = {"item": [self.i1.pk, self.i2.pk, self.i3.pk],
             f"llego_{self.i1.pk}": "on", f"cant_{self.i1.pk}": "5",
             f"llego_{self.i2.pk}": "on", f"cant_{self.i2.pk}": "1"}
        d.update(extra)
        return d

    def test_la_pantalla_propone_todo_completo(self):
        self.entrar(self.admin)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(all(f["llego"] and f["cantidad"] == str(f["item"].cantidad_pedida) for f in r.context["filas"]))

    def test_confirmar_suma_lo_recibido_y_deja_el_historial(self):
        self.entrar(self.admin)
        r = self.client.post(self.url, self.datos())
        self.assertRedirects(r, f"/reposicion/{self.l.pk}/", fetch_redirect_response=False)
        self.assertEqual((self.stock("p1"), self.stock("p2"), self.stock("p3")), (6, 11, 0))
        movs = Movimiento.objects.order_by("producto__nombre")
        self.assertEqual([(m.producto, m.tipo, m.cantidad, m.usuario, m.nota) for m in movs],
                         [(self.p2, "ENTRADA", 1, self.admin, f"Reposición #{self.l.pk}"),
                          (self.p1, "ENTRADA", 5, self.admin, f"Reposición #{self.l.pk}")])
        self.l.refresh_from_db()
        self.assertEqual((self.l.estado, self.l.cerrado_por), ("RECIBIDA", self.admin))
        self.assertIsNotNone(self.l.cerrado_en)
        for item in (self.i1, self.i2, self.i3):
            item.refresh_from_db()
        self.assertEqual([i.cantidad_recibida for i in (self.i1, self.i2, self.i3)], [5, 1, 0])
        self.assertEqual([i.resultado for i in (self.i1, self.i2, self.i3)], ["completo", "parcial", "no_llego"])

    def test_el_detalle_de_una_lista_recibida_muestra_lo_que_falto(self):
        self.entrar(self.admin)
        self.client.post(self.url, self.datos())
        r = self.client.get(f"/reposicion/{self.l.pk}/")
        self.assertContains(r, "Faltaron 2")
        self.assertContains(r, "No llegó")
        self.assertContains(r, "Completo")
        self.assertNotContains(r, "Recibir productos")

    def test_llegar_de_mas_tambien_se_suma_y_se_marca(self):
        self.entrar(self.admin)
        self.client.post(self.url, self.datos(**{f"cant_{self.i1.pk}": "8"}))
        self.i1.refresh_from_db()
        self.assertEqual((self.stock("p1"), self.i1.resultado, self.i1.diferencia), (9, "de_mas", 3))

    def test_confirmar_dos_veces_no_suma_dos_veces(self):
        self.entrar(self.admin)
        self.client.post(self.url, self.datos())
        self.client.post(self.url, self.datos())
        self.assertEqual((self.stock("p1"), self.stock("p2")), (6, 11))
        self.assertEqual(Movimiento.objects.count(), 2)

    def test_si_no_se_marca_nada_no_se_cierra_ni_se_suma(self):
        self.entrar(self.admin)
        r = self.client.post(self.url, {"item": [self.i1.pk, self.i2.pk, self.i3.pk]})
        self.assertContains(r, "No marcaste ningún producto")
        self.l.refresh_from_db()
        self.assertEqual((self.l.estado, Movimiento.objects.count(), self.stock("p1")), ("ABIERTA", 0, 1))

    def test_una_cantidad_invalida_no_guarda_nada_de_la_lista(self):
        self.entrar(self.admin)
        r = self.client.post(self.url, self.datos(**{f"cant_{self.i2.pk}": "abc"}))
        self.assertContains(r, "Escribe un número")
        self.l.refresh_from_db()
        self.i1.refresh_from_db()
        self.assertEqual((self.l.estado, Movimiento.objects.count(), self.stock("p1"), self.i1.cantidad_recibida),
                         ("ABIERTA", 0, 1, None))

    def test_si_la_lista_cambio_mientras_se_revisaba_no_confirma(self):
        self.entrar(self.admin)
        ReposicionItem.objects.create(reposicion=self.l, producto=self.p2.__class__.objects.create(
            nombre="Nuevo", categoria=self.cat_tec, precio_compra=1, precio_venta=2), cantidad_pedida=2, pedido_por=self.tec)
        r = self.client.post(self.url, self.datos())
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        self.l.refresh_from_db()
        self.assertEqual((self.l.estado, Movimiento.objects.count()), ("ABIERTA", 0))

    def test_el_tecnico_no_puede_recibir(self):
        self.entrar(self.tec)
        r = self.client.post(self.url, self.datos())
        self.assertRedirects(r, "/tecnico/", fetch_redirect_response=False)
        self.l.refresh_from_db()
        self.assertEqual((self.l.estado, self.stock("p1")), ("ABIERTA", 1))

    def test_no_se_puede_recibir_una_lista_vacia_ni_una_cancelada(self):
        self.entrar(self.admin)
        vacia = self.lista()
        self.assertRedirects(self.client.get(f"/reposicion/{vacia.pk}/recibir/"), f"/reposicion/{vacia.pk}/", fetch_redirect_response=False)
        Reposicion.objects.filter(pk=self.l.pk).update(estado="CANCELADA")
        self.client.post(self.url, self.datos())
        self.assertEqual((Movimiento.objects.count(), self.stock("p1")), (0, 1))


class ListadoTests(ReposicionBase):
    def test_pestanas_y_conteos(self):
        self.lista(p1=(5, self.admin))
        self.lista()
        recibida = self.lista(p2=(2, self.admin))
        Reposicion.objects.filter(pk=recibida.pk).update(estado="RECIBIDA")
        self.entrar(self.tec)
        r = self.client.get("/reposicion/")
        self.assertEqual((r.context["n_abiertas"], r.context["n_recibidas"], r.context["n_canceladas"]), (2, 1, 0))
        self.assertEqual(len(r.context["page_obj"]), 2)
        r = self.client.get("/reposicion/?estado=recibidas")
        self.assertEqual([l.pk for l in r.context["page_obj"]], [recibida.pk])
        self.assertEqual(self.client.get("/reposicion/?estado=inventado").context["estado"], "abiertas")
