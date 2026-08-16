"""Versões dos modloaders (sem rede: o `_fetch` é substituído)."""

import json
from pathlib import Path

import pytest

from mrpack2curseforge.services import loaders
from mrpack2curseforge.services.cache import SimpleCache

FABRIC = json.dumps(
    [
        {"loader": {"version": "0.16.9", "stable": True}},
        {"loader": {"version": "0.17.0-beta.1", "stable": False}},
        {"loader": {"version": "0.16.10", "stable": True}},
    ]
)

MAVEN = """<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <versioning>
    <versions>
      <version>21.1.100</version>
      <version>21.1.9</version>
      <version>21.11.45</version>
      <version>20.4.1</version>
    </versions>
  </versioning>
</metadata>"""

FORGE_MAVEN = """<?xml version="1.0" encoding="UTF-8"?>
<metadata><versioning><versions>
  <version>1.20.1-47.4.0</version>
  <version>1.21.1-52.0.40</version>
  <version>1.20.1-47.4.10</version>
</versions></versioning></metadata>"""


@pytest.fixture
def sem_rede(monkeypatch):
    """Devolve o corpo conforme a URL, e conta as chamadas."""

    chamadas = []

    def fake(url, tentativas=3):
        chamadas.append(url)
        if "fabricmc" in url or "quiltmc" in url:
            return None if "9.9.9" in url else FABRIC
        if "neoforged" in url:
            return MAVEN
        if "minecraftforge" in url:
            return FORGE_MAVEN
        return None

    monkeypatch.setattr(loaders, "_fetch", fake)
    return chamadas


def test_fabric_vem_ordenado_da_mais_nova(sem_rede):
    versoes = loaders.loader_versions("fabric", "1.21.1")

    assert [v["version"] for v in versoes] == [
        "0.17.0-beta.1",
        "0.16.10",
        "0.16.9",
    ]
    assert versoes[0]["stable"] is False


def test_neoforge_filtra_pelo_minecraft(sem_rede):
    """A versão do NeoForge codifica o Minecraft: 1.21.11 -> 21.11.*"""

    assert [v["version"] for v in loaders.loader_versions("neoforge", "1.21.11")] == [
        "21.11.45"
    ]
    # ordenação numérica, não alfabética: 100 > 9
    assert [v["version"] for v in loaders.loader_versions("neoforge", "1.21.1")] == [
        "21.1.100",
        "21.1.9",
    ]
    # 1.21 sem patch é a série .0
    assert loaders.loader_versions("neoforge", "1.21") == []


def test_forge_tira_o_prefixo_do_minecraft(sem_rede):
    versoes = loaders.loader_versions("forge", "1.20.1")

    assert [v["version"] for v in versoes] == ["47.4.10", "47.4.0"]


def test_loader_desconhecido_e_entrada_vazia(sem_rede):
    assert loaders.loader_versions("babel", "1.21.1") == []
    assert loaders.loader_versions("fabric", "") == []
    assert loaders.loader_versions("", "1.21.1") == []
    # nem tentou a rede quando não havia o que perguntar
    assert not [u for u in sem_rede if "1.21.1" in u and "babel" in u]


def test_falha_de_rede_nao_vira_cache_vazio(tmp_path: Path, sem_rede):
    """Um serviço fora do ar não pode envenenar o cache com uma lista vazia."""

    with SimpleCache(tmp_path / "c.sqlite3") as cache:
        assert loaders.loader_versions("fabric", "9.9.9", cache) == []
        assert cache.get("loader_versions", "fabric|9.9.9") is None

        # o que deu certo fica guardado
        assert loaders.loader_versions("fabric", "1.21.1", cache)
        assert cache.get("loader_versions", "fabric|1.21.1")

        antes = len(sem_rede)
        loaders.loader_versions("fabric", "1.21.1", cache)
        assert len(sem_rede) == antes  # veio do cache


def test_fetch_retenta_antes_de_desistir(monkeypatch):
    """O maven do NeoForge devolve 404 esporádico; sem retry a lista some."""

    import httpx

    tentativas = {"n": 0}

    def fake_get(*_a, **_k):
        tentativas["n"] += 1
        if tentativas["n"] < 3:
            raise httpx.ConnectError("boom")

        class Resposta:
            text = "ok"

            def raise_for_status(self):
                return None

        return Resposta()

    monkeypatch.setattr(loaders.httpx, "get", fake_get)
    monkeypatch.setattr(loaders.time, "sleep", lambda _s: None)

    assert loaders._fetch("https://exemplo/x") == "ok"
    assert tentativas["n"] == 3

    tentativas["n"] = 0
    assert loaders._fetch("https://exemplo/x", tentativas=2) is None
