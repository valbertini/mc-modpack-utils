"""Resourcepack, shader e o que já vinha em `overrides/` também viram manifest.

O export do CurseForge lista essas três coisas no `manifest.json`; até a v0.23 o
conversor procurava só os mods do índice e mandava o resto para `overrides/`.
"""

import json
import zipfile
from pathlib import Path

from mrpack2curseforge.constants import CURSEFORGE_CLASSES
from mrpack2curseforge.converter import Converter
from mrpack2curseforge.domain import (
    MatchResult,
    MinecraftInfo,
    Modpack,
    ModrinthProject,
    PackFile,
)
from mrpack2curseforge.parsers.mrpack import MrpackParser
from mrpack2curseforge.progress import Reporter
from mrpack2curseforge.services.matcher import (
    CurseForgeMatcher,
    normalize_mod_name,
)


class Mudo(Reporter):
    """Silêncio: o teste não é sobre o que aparece no terminal."""

    def stage(self, label, total=None):
        pass

    def advance(self, step=1):
        pass

    def log(self, message):
        pass

    def info(self, message):
        pass

    def stop(self):
        pass


RESOURCEPACK = "Fast Better Grass.zip"
SHADER = "photon_v1.3b.zip"


class FakeCurseForge:
    """Guarda com que `classId` cada busca foi feita."""

    def __init__(self, projects):
        self.projects = projects
        self.searches: list[tuple[str, int | None]] = []

    def search(self, query=None, slug=None, pages=None, class_id=None):
        """`slug` e lookup exato; `query` e busca textual, como na API."""

        self.searches.append((slug or query or "", class_id))
        daquela_secao = [p for p in self.projects if p.get("class") == class_id]

        if slug:
            return [p for p in daquela_secao if p["slug"] == slug]

        term = (query or "").lower()
        return [p for p in daquela_secao if term in p["name"].lower()]

    def get_files(self, mod_id, game_version=None, max_pages=None):
        return []


def projeto(id_, name, slug, class_id, file_name):
    return {
        "id": id_,
        "name": name,
        "slug": slug,
        "class": class_id,
        "latestFiles": [{"id": id_ * 10, "fileName": file_name}],
    }


def arquivo(path):
    return PackFile(file_name=path.split("/")[-1], file_path=path)


# --------------------------------------------------------------------- matcher
def test_resourcepack_is_searched_in_its_own_section():
    """`classId` de mods nunca devolveria um resourcepack — e era o único usado."""

    client = FakeCurseForge(
        [projeto(1, "Fast Better Grass", "fast-better-grass", 12, RESOURCEPACK)]
    )
    matcher = CurseForgeMatcher(client, loader="fabric")

    result = matcher.match(arquivo(f"resourcepacks/{RESOURCEPACK}"))

    assert result.matched
    assert result.project_id == 1
    assert {class_id for _, class_id in client.searches} == {
        CURSEFORGE_CLASSES["resourcepacks"]
    }


def test_shader_is_searched_in_its_own_section():
    client = FakeCurseForge(
        [projeto(2, "Photon Shader", "photon-shader", 6552, SHADER)]
    )
    matcher = CurseForgeMatcher(client)

    result = matcher.match(arquivo(f"shaderpacks/{SHADER}"))

    assert result.matched
    assert result.project_id == 2


def test_the_loader_is_never_added_to_a_resourcepack_query():
    """"Fast Better Grass fabric" não existe: resourcepack não tem loader."""

    client = FakeCurseForge([])
    matcher = CurseForgeMatcher(client, loader="fabric")

    matcher.match(arquivo(f"resourcepacks/{RESOURCEPACK}"), diagnose=False)

    assert not any("fabric" in termo for termo, _ in client.searches)


def test_diagnosis_can_be_turned_off():
    """Sem diagnóstico não há consulta extra — e não há conflito para mostrar."""

    client = FakeCurseForge([])
    matcher = CurseForgeMatcher(client)

    result = matcher.match(arquivo("mods/nada.jar"), diagnose=False)

    assert not result.matched
    assert result.diagnosis is None


def test_the_diagnosis_link_points_at_the_right_section():
    client = FakeCurseForge([])
    matcher = CurseForgeMatcher(client)

    result = matcher.match(arquivo(f"shaderpacks/{SHADER}"))
    result.diagnosis.project_slug = "photon-shader"

    assert "/shaders/photon-shader" in result.diagnosis.curseforge_url


# ---------------------------------------------------------------------- parser
def mrpack(tmp_path: Path, files: list[dict], overrides: list[str]) -> Path:
    destino = tmp_path / "pack.mrpack"

    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": "1",
        "name": "Pack",
        "files": files,
        "dependencies": {"minecraft": "1.21.11", "fabric-loader": "0.19.2"},
    }

    with zipfile.ZipFile(destino, "w") as z:
        z.writestr("modrinth.index.json", json.dumps(index))
        for path in overrides:
            z.writestr(f"overrides/{path}", "x")

    return destino


def test_the_parser_weighs_the_overrides(tmp_path: Path):
    """Sem esses tamanhos o card de confirmação não sabe estimar o zip.

    O peso é o **comprimido**: é o que o arquivo ocupa dentro de um `.zip`, que
    é a pergunta que a tela faz. E o candidato leva o seu, porque achá-lo no
    CurseForge é justamente o que tira aquele peso do pacote.
    """

    destino = tmp_path / "pack.mrpack"
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": "1",
        "name": "Pack",
        "files": [],
        "dependencies": {"minecraft": "1.21.11", "fabric-loader": "0.19.2"},
    }

    with zipfile.ZipFile(destino, "w", zipfile.ZIP_STORED) as z:
        z.writestr("modrinth.index.json", json.dumps(index))
        z.writestr("overrides/mods/pesado.jar", b"j" * 1000)
        z.writestr("overrides/config/leve.json", b"c" * 4000)

    pack = MrpackParser(destino).parse()

    assert pack.override_bytes == 5000
    assert [f.file_size for f in pack.override_candidates] == [1000]


def indexado(path):
    return {
        "path": path,
        "hashes": {"sha1": "a" * 40, "sha512": "b" * 128},
        "downloads": [f"https://example.invalid/{path}"],
        "fileSize": 1,
    }


def test_override_candidates_only_pick_what_curseforge_could_have(tmp_path: Path):
    """O que fica de fora é tão importante quanto o que entra.

    `.rpo`/`.txt` são packs desligados pelo launcher, `config/` não tem seção no
    CurseForge e um caminho aninhado não é um arquivo instalável — nenhum deles
    justifica gastar requisição.
    """

    pack = MrpackParser(
        mrpack(
            tmp_path,
            [indexado("mods/indexado.jar")],
            [
                "mods/WI-Zoom-1.7.jar",
                "mods/desligado.jar.disabled",
                "mods/indexado.jar",
                "mods/nested/outro.jar",
                "resourcepacks/xali.zip",
                "resourcepacks/Redstone Tweaks.zip.rpo",
                "shaderpacks/photon.zip",
                "config/qualquer.json",
                "leia-me.txt",
            ],
        )
    ).parse()

    assert sorted(f.file_path for f in pack.override_candidates) == [
        "mods/WI-Zoom-1.7.jar",
        "mods/desligado.jar.disabled",
        "resourcepacks/xali.zip",
        "shaderpacks/photon.zip",
    ]
    assert all(f.from_overrides for f in pack.override_candidates)


def test_convertible_and_plain_extras_split_the_pack():
    pack = Modpack(
        name="P",
        version="1",
        minecraft=MinecraftInfo(version="1.21", loader="fabric", loader_version="1"),
        mods=[arquivo("mods/a.jar")],
        extra_files=[arquivo("resourcepacks/b.zip"), arquivo("datapacks/c.zip")],
        override_candidates=[
            PackFile(file_name="d.jar", file_path="mods/d.jar", from_overrides=True)
        ],
    )

    assert [f.file_path for f in pack.convertible] == [
        "mods/a.jar",
        "resourcepacks/b.zip",
        "mods/d.jar",
    ]
    assert [f.file_path for f in pack.plain_extras] == ["datapacks/c.zip"]


# ------------------------------------------------------- nomes e ambiguidade
def test_a_leading_number_survives_the_camelcase_split():
    """`3D Default …` virava só "default", e a busca por "default" não acha nada.

    O separador de CamelCase cortava entre o dígito e a letra: "3 D Default".
    Aí o filtro de tokens jogava fora "3" (dígito) e "d" (uma letra só).
    """

    assert normalize_mod_name("3D Default 1.21.2+ v1.14.0.zip") == "3d default"
    # e o que ele já fazia continua valendo
    assert normalize_mod_name("ImmediatelyFast-Fabric-1.14.2+1.21.11.jar") == (
        "immediately fast"
    )


def test_non_mods_also_try_the_minecraft_prefixed_slug():
    """No CurseForge o texture pack `3d-default` se chama `minecraft-3d-default`.

    A busca textual não devolve o projeto de jeito nenhum (150 resultados sem
    ele); quem acha é o lookup exato por slug.
    """

    client = FakeCurseForge(
        # o nome do projeto la e outro, entao so o slug encontra
        [projeto(3, "GeForceLegend 3D", "minecraft-3d-default", 12, "3D Default.zip")]
    )
    matcher = CurseForgeMatcher(client)

    result = matcher.match(
        arquivo("resourcepacks/3D Default.zip"),
        ModrinthProject(project_id="x", slug="3d-default", title="3D Default"),
    )

    assert result.matched
    assert "minecraft-3d-default" in [termo for termo, _ in client.searches]


def test_the_mod_path_does_not_gain_the_minecraft_prefix():
    client = FakeCurseForge([])
    matcher = CurseForgeMatcher(client)

    matcher.match(arquivo("mods/sodium.jar"), diagnose=False)

    assert not any(termo.startswith("minecraft-") for termo, _ in client.searches)


def test_among_files_with_the_same_name_the_pack_version_wins():
    """Resourcepack publica toda release com o mesmo nome de arquivo.

    "Low Shield.zip" é o nome de 40 arquivos do projeto, um por versão do
    Minecraft. Pegar o primeiro que aparecesse instalava a versão errada.
    """

    files = [
        {"id": 1, "fileName": "Low Shield.zip", "fileDate": "2026-06-15",
         "gameVersions": ["1.21.4", "Fabric"]},
        {"id": 2, "fileName": "Low Shield.zip", "fileDate": "2025-12-10",
         "gameVersions": ["1.21.11"]},
        {"id": 3, "fileName": "Low Shield.zip", "fileDate": "2024-01-01",
         "gameVersions": ["1.21.11"]},
    ]
    matcher = CurseForgeMatcher(FakeCurseForge([]), minecraft_version="1.21.11")

    escolhido = matcher._pick_file(files, "low shield")

    # entre as duas que servem, a mais recente
    assert escolhido["id"] == 2


def test_with_no_version_in_common_the_newest_file_still_wins():
    files = [
        {"id": 1, "fileName": "x.zip", "fileDate": "2024-01-01",
         "gameVersions": ["1.20"]},
        {"id": 2, "fileName": "x.zip", "fileDate": "2026-01-01",
         "gameVersions": ["1.20.1"]},
    ]
    matcher = CurseForgeMatcher(FakeCurseForge([]), minecraft_version="1.21.11")

    assert matcher._pick_file(files, "x")["id"] == 2
    assert matcher._pick_file(files, "outro") is None


# ------------------------------------------------------------- empacotamento
def test_assembling_moves_the_matched_override_into_the_manifest(tmp_path: Path):
    """O caminho completo, sem rede: o jar sai de `overrides/` e vira manifest.

    O resto do `overrides/` do mrpack (o que não casou e o que nem é procurado)
    continua intocado dentro do zip.
    """

    origem = mrpack(
        tmp_path,
        [],
        ["mods/casou.jar", "mods/nao-casou.jar", "config/x.json"],
    )

    parser = MrpackParser(origem)
    pack = parser.parse()

    casou = next(f for f in pack.override_candidates if f.file_name == "casou.jar")
    results = [
        MatchResult(
            mod=casou,
            project_id=7,
            file_id=8,
            project_name="Casou",
            project_slug="casou",
        )
    ]

    converter = Converter(output_dir=tmp_path / "out", reporter=Mudo())
    destino, _, falhas = converter._assemble(pack, results, parser, base_name="t")

    assert falhas == 0

    with zipfile.ZipFile(destino) as z:
        nomes = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
        modlist = z.read("modlist.html").decode("utf-8")

    assert manifest["files"] == [
        {"projectID": 7, "fileID": 8, "required": True, "isLocked": False}
    ]
    assert "overrides/mods/casou.jar" not in nomes
    assert "overrides/mods/nao-casou.jar" in nomes
    assert "overrides/config/x.json" in nomes
    assert "curseforge.com/minecraft/mc-mods/casou" in modlist
