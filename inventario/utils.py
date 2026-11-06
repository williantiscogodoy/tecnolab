from urllib.parse import urlencode


def url_params(**params):
    """Arma un '?a=1&b=2' ignorando los valores vacíos."""
    limpios = {k: v for k, v in params.items() if v not in ("", None, False)}
    return "?" + urlencode(limpios) if limpios else "?"