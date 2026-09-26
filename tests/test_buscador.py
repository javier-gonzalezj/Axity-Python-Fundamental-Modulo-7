"""Pruebas de buscador.py sin conexión a internet.

httpx trae su propia herramienta para esto: httpx.MockTransport recibe una
función que, dada la petición, devuelve la respuesta. Así las pruebas son
rápidas y no dependen de que Open Library esté en línea.
"""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from libreria_m7 import buscador, utilidades
from libreria_m7.excepciones import ServicioExternoError

# Respuestas simplificadas con la misma forma que las reales de Open Library,
# indexadas por la ruta de la URL
RESPUESTAS: dict[str, dict[str, Any]] = {
    "/books/OL24292433M.json": {
        "title": "Cien años de soledad",
        "authors": [{"key": "/authors/OL4586796A"}],
        "works": [{"key": "/works/OL274505W"}],
        "publishers": ["Vintage Español"],
        "publish_date": "May 2009",
    },
    "/works/OL274505W.json": {
        "title": "Cien años de soledad",
        "subjects": ["Fiction", "Magic realism", "Colombia", "Families"],
    },
    "/authors/OL4586796A.json": {"name": "Gabriel García Márquez"},
}


def transporte_falso(respuestas: dict[str, dict[str, Any]]) -> httpx.MockTransport:
    """Imita a Open Library: /isbn/9780307474728.json redirige a la edición,
    las rutas de `respuestas` devuelven su JSON y todo lo demás es 404.
    """

    def responder(peticion: httpx.Request) -> httpx.Response:
        ruta = peticion.url.path
        if ruta == "/isbn/9780307474728.json":
            return httpx.Response(302, headers={"Location": "/books/OL24292433M.json"})
        if ruta in respuestas:
            return httpx.Response(200, json=respuestas[ruta])
        return httpx.Response(404)

    return httpx.MockTransport(responder)


def transporte_contando(
    responder: Callable[[httpx.Request], httpx.Response],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Envuelve `responder` y guarda cada petición recibida, para contarlas."""
    peticiones: list[httpx.Request] = []

    def registrar(peticion: httpx.Request) -> httpx.Response:
        peticiones.append(peticion)
        return responder(peticion)

    return httpx.MockTransport(registrar), peticiones


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita que @reintentar duerma entre intentos durante las pruebas."""
    monkeypatch.setattr(utilidades.time, "sleep", lambda _segundos: None)


def test_encuentra_libro() -> None:
    # Con guiones también funciona; además prueba que se sigue la redirección
    datos = buscador.buscar_por_isbn("978-0-307-47472-8", transporte_falso(RESPUESTAS))

    assert datos is not None
    assert datos.titulo == "Cien años de soledad"
    assert datos.autor == "Gabriel García Márquez"
    assert datos.generos == ["Fiction", "Magic realism", "Colombia"]  # máximo 3, de la obra
    assert datos.año_publicacion == 2009
    assert datos.editorial == "Vintage Español"


def test_autores_desde_la_obra() -> None:
    """Si la edición no trae autores, se toman de la obra."""
    respuestas = {
        **RESPUESTAS,
        "/books/OL24292433M.json": {**RESPUESTAS["/books/OL24292433M.json"], "authors": []},
        "/works/OL274505W.json": {
            **RESPUESTAS["/works/OL274505W.json"],
            "authors": [{"author": {"key": "/authors/OL4586796A"}}],
        },
    }

    datos = buscador.buscar_por_isbn("9780307474728", transporte_falso(respuestas))

    assert datos is not None
    assert datos.autor == "Gabriel García Márquez"


def test_isbn_no_encontrado() -> None:
    # Open Library responde 404 cuando no conoce el ISBN: no es un error
    assert buscador.buscar_por_isbn("0000000000", transporte_falso(RESPUESTAS)) is None


def test_sin_conexion_reintenta_y_lanza_error() -> None:
    def sin_red(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=peticion)

    transporte, peticiones = transporte_contando(sin_red)

    with pytest.raises(ServicioExternoError):
        buscador.buscar_por_isbn("9780307474728", transporte)
    assert len(peticiones) == 2  # @reintentar(intentos=2)


def test_error_del_servidor_no_se_reintenta() -> None:
    transporte, peticiones = transporte_contando(lambda peticion: httpx.Response(500))

    with pytest.raises(ServicioExternoError):
        buscador.buscar_por_isbn("9780307474728", transporte)
    assert len(peticiones) == 1


def test_respuesta_que_no_es_json() -> None:
    transporte = httpx.MockTransport(lambda peticion: httpx.Response(200, text="<html>"))

    with pytest.raises(ServicioExternoError):
        buscador.buscar_por_isbn("9780307474728", transporte)


@pytest.mark.parametrize(
    ("fecha", "esperado"),
    [("1967", 1967), ("May 1967", 1967), ("1967-05-30", 1967), ("s/f", None), ("", None)],
)
def test_extraer_año(fecha: str, esperado: int | None) -> None:
    assert buscador._extraer_año(fecha) == esperado


def test_normalizar_isbn() -> None:
    assert buscador.normalizar_isbn(" 978-607-07-1234-x ") == "978607071234X"
