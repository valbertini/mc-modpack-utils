"""A política de rede compartilhada pelos dois clientes de API.

Estava sem teste nenhum nos dois — e é o código que decide se uma conversão
sobrevive a um `429` do CurseForge no meio de 400 mods.
"""

import httpx
import pytest

from mrpack2curseforge.config import Config
from mrpack2curseforge.exceptions import ApiError
from mrpack2curseforge.services import http
from mrpack2curseforge.services.cache import SimpleCache
from mrpack2curseforge.services.curseforge import CurseForgeClient
from mrpack2curseforge.services.modrinth import ModrinthClient


@pytest.fixture
def esperas(monkeypatch):
    """Nenhum teste dorme de verdade; a lista guarda quanto teria dormido."""

    registradas: list[float] = []
    monkeypatch.setattr(http.time, "sleep", registradas.append)
    monkeypatch.setattr(Config, "HTTP_RETRIES", 3)
    return registradas


def cliente(*respostas: httpx.Response, base: str = "") -> tuple[httpx.Client, list]:
    """Cliente que devolve uma resposta por chamada, na ordem."""

    chamadas: list[httpx.Request] = []
    fila = list(respostas)

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return fila.pop(0) if fila else respostas[-1]

    return (
        httpx.Client(transport=httpx.MockTransport(handler), base_url=base),
        chamadas,
    )


def test_o_429_e_respeitado_e_a_chamada_repetida(esperas):
    client, chamadas = cliente(
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"ok": True}),
    )

    assert http.fetch_json(client, "GET", "http://x/mods") == {"ok": True}
    assert len(chamadas) == 2
    assert esperas == [7.0]


def test_um_retry_after_absurdo_e_limitado(esperas):
    client, _ = cliente(
        httpx.Response(429, headers={"Retry-After": "9000"}),
        httpx.Response(200, json={}),
    )

    http.fetch_json(client, "GET", "http://x/mods")

    assert esperas == [http.MAX_RETRY_AFTER]


def test_o_erro_de_servidor_e_retentado_e_no_fim_vira_ApiError(esperas):
    client, chamadas = cliente(httpx.Response(500))

    with pytest.raises(ApiError) as erro:
        http.fetch_json(client, "GET", "http://x/mods", label="CurseForge ")

    # uma chamada por tentativa, e o motivo da última vai na mensagem
    assert len(chamadas) == Config.HTTP_RETRIES
    assert "CurseForge http://x/mods falhou" in str(erro.value)


def test_o_json_quebrado_tambem_e_retentado(esperas):
    client, chamadas = cliente(
        httpx.Response(200, content=b"isto nao e json"),
        httpx.Response(200, json={"ok": 1}),
    )

    assert http.fetch_json(client, "GET", "http://x/mods") == {"ok": 1}
    assert len(chamadas) == 2


def test_um_status_de_ausencia_nao_e_retentado(esperas):
    client, chamadas = cliente(httpx.Response(404))

    assert http.fetch_json(client, "GET", "http://x/mods") is http.VAZIO
    assert len(chamadas) == 1
    assert esperas == []


def test_o_backoff_cresce_mas_tem_teto(esperas, monkeypatch):
    monkeypatch.setattr(Config, "HTTP_RETRIES", 6)
    client, _ = cliente(httpx.Response(500))

    with pytest.raises(ApiError):
        http.fetch_json(client, "GET", "http://x/mods")

    assert esperas == [1, 2, 4, 8, http.MAX_BACKOFF, http.MAX_BACKOFF]


# ------------------------------------------------------------- nos clientes
def test_o_curseforge_trata_400_e_404_como_vazio(esperas, tmp_path):
    with SimpleCache(tmp_path / "c.sqlite3") as cache:
        client = CurseForgeClient(cache, api_key="x")
        client.client, _ = cliente(httpx.Response(400), base="http://cf")

        assert client._get("/mods/0", {}) == {}


def test_o_modrinth_prefere_ficar_sem_dados_a_derrubar_a_conversao(esperas, tmp_path):
    """Desistir e não existir dão no mesmo lá: nenhuma chamada é obrigatória."""

    with SimpleCache(tmp_path / "c.sqlite3") as cache, ModrinthClient(cache) as client:
        client.client, chamadas = cliente(httpx.Response(500), base="http://mr")

        assert client._request("GET", "/project/x") is None
        assert len(chamadas) == Config.HTTP_RETRIES
