"""Versiones livianas de las imágenes guardadas en Cloudinary.

Cloudinary entrega la foto original (a veces de varios MB). Si a la URL se le
inserta una transformación, devuelve una copia reducida y comprimida sin tocar
el archivo guardado:

    c_limit,w_400   no pasa de 400 px de ancho (nunca agranda)
    q_auto          calidad automática
    f_auto          el formato más liviano que soporte el navegador (WebP/AVIF)

Si la URL no es de Cloudinary (tu PC, con la carpeta media/), se devuelve igual.
"""
ANCHO_MINIATURA = 400   # listas de productos, panel del técnico, miniaturas
ANCHO_VISTA = 800       # vista previa en el formulario de producto
ANCHO_GALERIA = 1400    # imagen grande de la galería (modal)

_MARCA = "/image/upload/"


def optimizada(url, ancho):
    if not url or _MARCA not in url or f"{_MARCA}c_limit," in url:
        return url
    return url.replace(_MARCA, f"{_MARCA}c_limit,w_{ancho},q_auto,f_auto/", 1)
