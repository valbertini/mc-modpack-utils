"""Testes do parser do .mrpack e do builder do manifest (sem rede)."""

import json
import zipfile
from pathlib import Path

import pytest

from mrpack2curseforge.builders.curseforge_manifest import CurseForgeManifestBuilder
from mrpack2curseforge.builders.package import safe_name
from mrpack2curseforge.domain import (
    MatchResult,
    MatchStrategy,
    MinecraftInfo,
    Modpack,
    PackFile,
)
from mrpack2curseforge.exceptions import InvalidMrpackError
from mrpack2curseforge.parsers.mrpack import MrpackParser

INDEX = {
    "formatVersion": 1,
    "game": "minecraft",
    "versionId": "1.0.0",
    "name": "Pack de Teste",
    "files": [
        {
            "path": "mods/sodium-fabric-0.9.0+mc26.2.jar",
            "hashes": {"sha1": "abc123"},
            "downloads": [
                "https://cdn.modrinth.com/data/AANobbMI/versions/x/sodium.jar"
            ],
            "fileSize": 10,
        },
        {
            "path": "resourcepacks/faithful.zip",
            "hashes": {"sha1": "def456"},
            "downloads": ["https://cdn.modrinth.com/data/ZZZ/versions/y/faithful.zip"],
        },
    ],
    "dependencies": {"minecraft": "1.21", "fabric-loader": "0.16.0"},
}


@pytest.fixture
def mrpack(tmp_path: Path) -> Path:
    path = tmp_path / "teste.mrpack"

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("modrinth.index.json", json.dumps(INDEX))
        z.writestr("overrides/config/sodium.json", "{}")
        z.writestr("overrides/options.txt", "fov:80")

    return path


def test_parse_separates_mods_from_other_files(mrpack: Path):
    pack = MrpackParser(mrpack).parse()

    assert pack.name == "Pack de Teste"
    assert pack.minecraft.version == "1.21"
    assert pack.loader_id == "fabric-0.16.0"
    assert [m.file_name for m in pack.mods] == ["sodium-fabric-0.9.0+mc26.2.jar"]
    assert [f.file_path for f in pack.extra_files] == ["resourcepacks/faithful.zip"]
    assert len(pack.override_paths) == 2


def test_extract_overrides(mrpack: Path, tmp_path: Path):
    destination = tmp_path / "work" / "overrides"
    extracted = MrpackParser(mrpack).extract_overrides(destination)

    assert extracted == 2
    assert (destination / "config" / "sodium.json").exists()
    assert (destination / "options.txt").read_text() == "fov:80"


def test_invalid_file_is_rejected(tmp_path: Path):
    broken = tmp_path / "quebrado.mrpack"
    broken.write_text("nao sou um zip")

    with pytest.raises(InvalidMrpackError):
        MrpackParser(broken).validate()


def test_manifest_structure():
    pack = Modpack(
        name="Pack",
        version="1.0.0",
        minecraft=MinecraftInfo(
            version="1.21", loader="fabric", loader_version="0.16.0"
        ),
    )

    mod = PackFile(file_name="sodium.jar", file_path="mods/sodium.jar")
    results = [
        MatchResult(
            mod=mod,
            strategy=MatchStrategy.MODRINTH_SLUG,
            project_id=1,
            file_id=2,
            project_name="Sodium",
            project_slug="sodium",
        ),
        MatchResult(mod=PackFile(file_name="x.jar", file_path="mods/x.jar")),
    ]

    manifest = CurseForgeManifestBuilder().build(pack, results)

    assert manifest["manifestType"] == "minecraftModpack"
    assert manifest["minecraft"]["modLoaders"][0]["id"] == "fabric-0.16.0"
    assert manifest["files"] == [
        {"projectID": 1, "fileID": 2, "required": True, "isLocked": False}
    ]
    assert manifest["overrides"] == "overrides"

    modlist = CurseForgeManifestBuilder().build_modlist(results)
    assert "curseforge.com/minecraft/mc-mods/sodium" in modlist
    assert "x.jar (overrides)" in modlist


def test_disabled_mods_become_optional_entries():
    """`.jar.disabled` no mrpack = mod desmarcado no CurseForge.

    É assim que o export do launcher grava: `required: false`, que ele reinstala
    como `.jar.disabled`. Marcar `true` religaria um mod que estava desligado.
    """

    pack = Modpack(
        name="Pack",
        version="1",
        minecraft=MinecraftInfo(version="1.21", loader="fabric", loader_version="1"),
    )
    results = [
        MatchResult(
            mod=PackFile(
                file_name="chunky.jar.disabled", file_path="mods/chunky.jar.disabled"
            ),
            project_id=1,
            file_id=2,
        ),
        MatchResult(
            mod=PackFile(file_name="sodium.jar", file_path="mods/sodium.jar"),
            project_id=3,
            file_id=4,
        ),
    ]

    files = CurseForgeManifestBuilder().build(pack, results)["files"]

    assert [f["required"] for f in files] == [False, True]


def test_modlist_uses_the_section_of_each_kind_and_names_the_author():
    """Resourcepack não mora em `/mc-mods/`, e o CurseForge assina cada linha."""

    results = [
        MatchResult(
            mod=PackFile(file_name="rp.zip", file_path="resourcepacks/rp.zip"),
            project_id=1,
            file_id=2,
            project_name="Redstone Tweaks",
            project_slug="redstone-tweaks",
            project_author="Fulano",
        ),
        MatchResult(
            mod=PackFile(file_name="sh.zip", file_path="shaderpacks/sh.zip"),
            project_id=3,
            file_id=4,
            project_name="Photon",
            project_slug="photon-shader",
        ),
    ]

    modlist = CurseForgeManifestBuilder().build_modlist(results)

    assert "curseforge.com/minecraft/texture-packs/redstone-tweaks" in modlist
    assert "Redstone Tweaks (by Fulano)" in modlist
    assert "curseforge.com/minecraft/shaders/photon-shader" in modlist
    assert "mc-mods" not in modlist


def test_safe_name_strips_invalid_characters():
    assert safe_name("Meu Pack: v2 / final?") == "Meu Pack_ v2 _ final"


def test_build_zip_is_atomic(tmp_path: Path):
    """O zip é montado num .part e só depois assume o nome final.

    Sobrescrever no lugar fazia um download em andamento ver o arquivo mudar de
    tamanho no meio (e o servidor abortar com erro de Content-Length).
    """

    from mrpack2curseforge.builders.package import build_zip

    work = tmp_path / "work"
    (work / "overrides").mkdir(parents=True)
    (work / "manifest.json").write_text('{"a": 1}')
    (work / "overrides" / "options.txt").write_text("fov:80")

    destination = tmp_path / "saida" / "pack.zip"
    destination.parent.mkdir()
    destination.write_bytes(b"versao antiga")

    build_zip(work, destination)

    assert not (destination.parent / "pack.zip.part").exists()

    with zipfile.ZipFile(destination) as archive:
        assert sorted(archive.namelist()) == ["manifest.json", "overrides/options.txt"]
        assert archive.read("manifest.json") == b'{"a": 1}'


def test_build_zip_reports_a_locked_destination(tmp_path: Path, monkeypatch):
    """Se o .zip estiver em uso, falha com mensagem clara em vez de corromper."""

    import os as os_module

    from mrpack2curseforge.builders import package
    from mrpack2curseforge.exceptions import Mrpack2CurseForgeError

    work = tmp_path / "work"
    work.mkdir()
    (work / "manifest.json").write_text("{}")

    monkeypatch.setattr(package.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        os_module, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError())
    )

    with pytest.raises(Mrpack2CurseForgeError, match="está em uso"):
        package.build_zip(work, tmp_path / "pack.zip")

    assert not (tmp_path / "pack.zip.part").exists()
