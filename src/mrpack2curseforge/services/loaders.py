"""Versões publicadas de cada modloader, por versão do Minecraft.

Nem o Modrinth nem o CurseForge listam isso: cada loader tem o seu próprio
serviço de metadados. São consultas pequenas e cacheadas — o objetivo é só
preencher um dropdown, para o usuário não precisar caçar o número à mão.

Se um serviço estiver fora do ar, a lista volta vazia e a interface deixa o
usuário digitar: não conseguir listar não pode impedir a atualização.
"""

import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from mrpack2curseforge.config import Config
from mrpack2curseforge.constants import USER_AGENT
from mrpack2curseforge.services.cache import SimpleCache

FABRIC_META = "https://meta.fabricmc.net/v2/versions/loader"
QUILT_META = "https://meta.quiltmc.org/v3/versions/loader"
NEOFORGE_MAVEN = (
    "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
)
FORGE_MAVEN = (
    "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
)

# quantas versões devolver (as mais novas primeiro)
LIMIT = 40


def _fetch(url: str, tentativas: int = 3) -> str | None:
    """GET simples com retentativa.

    O maven do NeoForge devolve 404 de vez em quando para uma URL que existe
    (inconsistência entre os nós do mirror deles) — medido aqui: ~1 em 3 na
    primeira chamada, e a seguinte funciona. Sem retentar, o dropdown ficaria
    vazio sem motivo.
    """

    for tentativa in range(tentativas):
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=Config.HTTP_TIMEOUT,
                follow_redirects=True,
            )
            response.raise_for_status()
            return response.text
        except (httpx.HTTPError, ValueError):
            if tentativa + 1 < tentativas:
                time.sleep(0.4 * (tentativa + 1))

    return None


def _sort_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)) or (0,)


def _maven_versions(url: str) -> list[str]:
    """Versões do `maven-metadata.xml`, da mais nova para a mais antiga."""

    corpo = _fetch(url)
    if not corpo:
        return []

    try:
        raiz = ET.fromstring(corpo)
    except ET.ParseError:
        return []

    versoes = [item.text for item in raiz.iter("version") if item.text]
    versoes.sort(key=_sort_key, reverse=True)
    return versoes


def _meta_versions(base: str, minecraft: str) -> list[dict[str, Any]]:
    """Fabric e Quilt publicam a lista já filtrada pela versão do Minecraft."""

    import json

    corpo = _fetch(f"{base}/{minecraft}")
    if not corpo:
        return []

    try:
        dados = json.loads(corpo)
    except json.JSONDecodeError:
        return []

    versoes = [
        {
            "version": item["loader"]["version"],
            "stable": bool(item["loader"].get("stable")),
        }
        for item in dados
        if isinstance(item, dict) and (item.get("loader") or {}).get("version")
    ]

    # o Quilt devolve as betas fora de ordem; ordenamos todos igual
    versoes.sort(key=lambda item: _sort_key(item["version"]), reverse=True)
    return versoes


def _neoforge_prefix(minecraft: str) -> str | None:
    """MC `1.21.11` -> versões `21.11.*`; MC `1.21` -> `21.0.*`."""

    partes = minecraft.split(".")
    if len(partes) < 2 or partes[0] != "1":
        return None

    patch = partes[2] if len(partes) > 2 else "0"
    return f"{partes[1]}.{patch}."


def loader_versions(
    loader: str, minecraft: str, cache: SimpleCache | None = None
) -> list[dict[str, Any]]:
    """Versões do loader que servem naquela versão do Minecraft."""

    loader = (loader or "").lower()
    minecraft = (minecraft or "").strip()

    if not loader or not minecraft:
        return []

    chave = f"{loader}|{minecraft}"
    if cache is not None:
        guardado = cache.get("loader_versions", chave)
        if guardado is not None:
            return guardado

    if loader == "fabric":
        versoes = _meta_versions(FABRIC_META, minecraft)
    elif loader == "quilt":
        versoes = _meta_versions(QUILT_META, minecraft)
    elif loader == "neoforge":
        prefixo = _neoforge_prefix(minecraft)
        versoes = (
            [
                {"version": v, "stable": "beta" not in v.lower()}
                for v in _maven_versions(NEOFORGE_MAVEN)
                if prefixo and v.startswith(prefixo)
            ]
            if prefixo
            else []
        )
    elif loader == "forge":
        # o maven do Forge usa "<minecraft>-<versão do forge>"
        versoes = [
            {"version": v.split("-", 1)[1], "stable": True}
            for v in _maven_versions(FORGE_MAVEN)
            if v.startswith(f"{minecraft}-") and "-" in v
        ]
    else:
        versoes = []

    versoes = versoes[:LIMIT]

    # só guarda o que deu certo: uma falha de rede não pode virar cache vazio
    if cache is not None and versoes:
        cache.set("loader_versions", chave, versoes)

    return versoes
