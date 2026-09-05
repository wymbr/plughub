"""
test_dialog_form_pin.py — o pin de versão do DialogForm (S1 / D14).

O que este arquivo protege, e por que cada caso existe:

  * o pin RESOLVE quando a dialog-api responde — controle positivo, sem o qual um
    resolvedor que devolvesse `None` sempre passaria em todos os outros casos;
  * o pin devolve `None` em toda falha, e **nunca um palpite** — versão inventada
    é pior que ausência, porque fixa o submit num documento que pode não ser o
    exibido, com cara de garantia;
  * `True` não vira versão 1. Em Python `bool` é subclasse de `int`, então um JSON
    malformado com `"version": true` passaria por `isinstance(v, int)` e o pin
    apontaria para a versão 1 — plausível, errado e mudo.

A degradação é fail-open DECLARADA: sem pin, cada leitura resolve "a última
publicada", que é o comportamento anterior ao mecanismo. Por isso os casos negativos
asseguram `None` (omitir a tag), nunca uma exceção que derrubaria o delegate.
"""
import httpx
import pytest

from plughub_channel_gateway.dialog_form_pin import resolve_published_version


def _client(handler):
    """Monkeypatch de `httpx.AsyncClient` por um transporte de mentira."""
    class _FakeAsyncClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None, headers=None):
            return handler(url, params, headers)
    return _FakeAsyncClient


def _resp(status: int, payload):
    req = httpx.Request("GET", "http://dialog/x")
    return httpx.Response(status_code=status, json=payload, request=req)


@pytest.mark.asyncio
async def test_resolve_devolve_a_versao_publicada(monkeypatch):
    """CONTROLE POSITIVO — sem ele, um resolvedor que só devolve None passa em tudo."""
    vistos = {}

    def handler(url, params, headers):
        vistos["url"] = url
        vistos["params"] = params
        vistos["headers"] = headers
        return _resp(200, {"form_id": "f1", "version": 7, "status": "published"})

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    v = await resolve_published_version("http://dialog:3760", "t1", "f1")

    assert v == 7
    # Pede a PUBLICADA — pinar um rascunho apontaria para documento que ninguém vê.
    assert vistos["params"] == {"status": "published"}
    assert vistos["headers"]["X-Tenant-ID"] == "t1"
    assert vistos["url"].endswith("/v1/dialog/forms/f1")


@pytest.mark.asyncio
async def test_url_vazia_nao_pina_e_nao_levanta(monkeypatch):
    """Config ausente degrada para 'sem pin', nunca derruba o delegate."""
    assert await resolve_published_version("", "t1", "f1") is None


@pytest.mark.asyncio
async def test_form_id_vazio_nao_consulta(monkeypatch):
    chamou = {"n": 0}

    def handler(url, params, headers):
        chamou["n"] += 1
        return _resp(200, {"version": 1})

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    assert await resolve_published_version("http://dialog:3760", "t1", "") is None
    assert chamou["n"] == 0


@pytest.mark.asyncio
async def test_erro_http_devolve_none(monkeypatch):
    """404/500 ⇒ sem pin. NUNCA um default de versão."""
    monkeypatch.setattr(httpx, "AsyncClient", _client(lambda u, p, h: _resp(404, {"detail": "x"})))
    assert await resolve_published_version("http://dialog:3760", "t1", "f1") is None


@pytest.mark.asyncio
async def test_excecao_de_rede_devolve_none(monkeypatch):
    def handler(url, params, headers):
        raise httpx.ConnectError("dialog-api fora do ar")

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    assert await resolve_published_version("http://dialog:3760", "t1", "f1") is None


@pytest.mark.asyncio
async def test_version_booleana_nao_vira_1(monkeypatch):
    """
    `True` é `int` em Python. Sem a guarda de `bool`, `"version": true` passaria e o
    pin apontaria para a versão 1 — o valor plausível mais barato de produzir.
    """
    monkeypatch.setattr(httpx, "AsyncClient", _client(lambda u, p, h: _resp(200, {"version": True})))
    assert await resolve_published_version("http://dialog:3760", "t1", "f1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("valor", [None, 0, -3, "2", 1.5, {"v": 1}])
async def test_version_invalida_nao_vira_pin(monkeypatch, valor):
    """Zero e negativo saem junto: `version` começa em 1 no store."""
    monkeypatch.setattr(httpx, "AsyncClient", _client(lambda u, p, h: _resp(200, {"version": valor})))
    assert await resolve_published_version("http://dialog:3760", "t1", "f1") is None
