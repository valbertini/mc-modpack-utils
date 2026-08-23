"""O conversor sem servidor nenhum: `finish`, `plan` e o log da análise.

Nada aqui sobe FastAPI — é o `converter.py` puro. Os testes da API web ficam
no `test_web_api.py`; o que separa os dois é justamente isto: aqui não há
cliente HTTP, ali toda asserção passa por uma rota.
"""

import json
import threading
import zipfile
from pathlib import Path

import pytest

from mrpack2curseforge.converter import ConversionOutcome, Converter, Resolution
from mrpack2curseforge.domain import (
    Diagnosis,
    MatchResult,
    MatchStrategy,
    MinecraftInfo,
    MissingReason,
    Modpack,
    PackFile,
)
from mrpack2curseforge.exceptions import ConversionCancelled
from mrpack2curseforge.reporting import build_report

INDEX = {
    "formatVersion": 1,
    "game": "minecraft",
    "versionId": "1.0.0",
    "name": "Pack Web",
    "files": [
        {
            "path": "mods/litematica-fabric-26.2-0.28.2.jar",
            "hashes": {"sha1": "abc"},
            "downloads": ["https://cdn.modrinth.com/data/AAA/versions/x/lite.jar"],
        }
    ],
    "dependencies": {"minecraft": "1.21", "fabric-loader": "0.16.0"},
}


def write_mrpack(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("modrinth.index.json", json.dumps(INDEX))
        z.writestr("overrides/options.txt", "fov:80")
    return path


def test_cancelled_converter_raises_before_working(tmp_path: Path):
    event = threading.Event()
    event.set()

    converter = Converter(output_dir=tmp_path, cancel_event=event)

    assert converter.cancelled
    with pytest.raises(ConversionCancelled):
        converter._check_cancel()


def test_finish_applies_manual_choice(tmp_path: Path, monkeypatch):
    """A escolha manual entra no manifest e o mod sai de overrides."""

    mod = PackFile(file_name="litematica.jar", file_path="mods/litematica.jar")
    pack = Modpack(
        name="P",
        version="1",
        minecraft=MinecraftInfo(version="1.21", loader="fabric", loader_version="0.1"),
        mods=[mod],
    )
    result = MatchResult(mod=mod)
    report = build_report(pack, [result], tmp_path / "p.mrpack")

    outcome = ConversionOutcome(
        source=write_mrpack(tmp_path / "p.mrpack"),
        pack=pack,
        results=[result],
        report=report,
        output=tmp_path / "p.zip",
        record_path=tmp_path / "conversions" / "p.json",
    )
    outcome.output.write_bytes(b"x")

    converter = Converter(output_dir=tmp_path)
    monkeypatch.setattr(
        converter, "_assemble", lambda *a, **k: (outcome.output, None, 0)
    )

    updated = converter.finish(
        outcome,
        {
            "litematica.jar": Resolution(
                project_id=1, file_id=2, project_name="Litematica"
            )
        },
    )

    assert updated.results[0].matched
    assert updated.results[0].strategy is MatchStrategy.MANUAL
    assert updated.report.matched == 1
    assert updated.report.overrides == 0
    assert updated.packaged

    # desfazendo, o mod volta para overrides
    reverted = converter.finish(updated, {})
    assert not reverted.results[0].matched
    assert reverted.report.matched == 0


def test_plan_describes_what_finish_will_do(tmp_path: Path):
    mega = 1024 * 1024
    mods = [
        PackFile(file_name="a.jar", file_path="mods/a.jar", file_size=9 * mega),
        PackFile(file_name="b.jar", file_path="mods/b.jar", file_size=3 * mega),
    ]
    pack = Modpack(
        name="P",
        version="1",
        minecraft=MinecraftInfo(version="1.21", loader="fabric", loader_version="0.1"),
        mods=mods,
        extra_files=[
            PackFile(
                file_name="dp.zip", file_path="datapacks/dp.zip", file_size=mega
            )
        ],
        override_paths=[Path("config/a.json")],
        override_bytes=2 * mega,
    )

    results = [
        MatchResult(mod=mods[0], project_id=1, file_id=2),
        MatchResult(mod=mods[1]),
    ]

    outcome = ConversionOutcome(
        source=tmp_path / "p.mrpack",
        pack=pack,
        results=results,
        report=build_report(pack, results, tmp_path / "p.mrpack"),
        output=tmp_path / "p.zip",
        record_path=tmp_path / "conversions" / "p.json",
    )

    assert outcome.plan() == {
        "manifest": 1,
        "manual": 0,
        "from_overrides": 0,
        # b.jar (sem match) + o datapack, que nem chega a ser procurado lá
        "downloads": 2,
        "download_mods": 1,
        # 2 MB que ficam em overrides/ mais 4 MB a baixar (3 do b.jar, 1 do
        # datapack), os dois tirados do `fileSize` do índice
        "zip_mb": 6.0,
    }


def test_the_plan_discounts_what_leaves_the_overrides(tmp_path: Path):
    """Achar no CurseForge um arquivo que viajava em `overrides/` encolhe o zip.

    É a única boa notícia do painel de confirmação, e sem o desconto o tamanho
    estimado ficaria maior que o arquivo que vai nascer.
    """

    mega = 1024 * 1024
    veio_de_la = PackFile(
        file_name="xali.zip",
        file_path="resourcepacks/xali.zip",
        from_overrides=True,
        file_size=5 * mega,
    )
    pack = Modpack(
        name="P",
        version="1",
        minecraft=MinecraftInfo(version="1.21", loader="fabric", loader_version="0.1"),
        override_paths=[Path("resourcepacks/xali.zip"), Path("config/a.json")],
        override_bytes=8 * mega,
        override_candidates=[veio_de_la],
    )

    outcome = ConversionOutcome(
        source=tmp_path / "p.mrpack",
        pack=pack,
        results=[MatchResult(mod=veio_de_la, project_id=1, file_id=2)],
        report=build_report(pack, [], tmp_path / "p.mrpack"),
        output=tmp_path / "p.zip",
        record_path=tmp_path / "conversions" / "p.json",
    )

    plano = outcome.plan()

    assert plano["from_overrides"] == 1
    # 8 MB de overrides menos os 5 que subiram para o manifest
    assert plano["zip_mb"] == 3.0


def test_drop_resolved_overrides_removes_whatever_entered_the_manifest(
    tmp_path: Path,
):
    """Quem entrou no manifest sai de `overrides/` — inclusive resourcepack.

    Mesmo o arquivo que já vinha no `overrides/` do mrpack: deixá-lo aqui depois
    de virar entrada do manifest instalaria o mesmo mod duas vezes.
    """

    overrides = tmp_path / "overrides"
    (overrides / "mods").mkdir(parents=True)
    (overrides / "resourcepacks").mkdir(parents=True)

    do_pack = overrides / "mods" / "do-pack.jar"
    do_matcher = overrides / "mods" / "resolvido.jar"
    sem_match = overrides / "mods" / "sobra.jar"
    resourcepack = overrides / "resourcepacks" / "rp.zip"
    for arquivo in (do_pack, do_matcher, sem_match, resourcepack):
        arquivo.write_text("x")

    results = [
        MatchResult(
            mod=PackFile(
                file_name="do-pack.jar",
                file_path="mods/do-pack.jar",
                from_overrides=True,
            ),
            project_id=1,
            file_id=2,
        ),
        MatchResult(
            mod=PackFile(file_name="resolvido.jar", file_path="mods/resolvido.jar"),
            project_id=3,
            file_id=4,
        ),
        MatchResult(
            mod=PackFile(file_name="rp.zip", file_path="resourcepacks/rp.zip"),
            project_id=5,
            file_id=6,
        ),
        MatchResult(mod=PackFile(file_name="sobra.jar", file_path="mods/sobra.jar")),
    ]

    Converter._drop_resolved_overrides(results, overrides)

    assert not do_pack.exists()
    assert not do_matcher.exists()
    assert not resourcepack.exists()
    assert sem_match.exists()


def test_streamed_line_per_mod():
    """Durante a busca cada mod gera uma linha, no estilo do terraform."""

    mod = PackFile(file_name="sodium.jar", file_path="mods/sodium.jar")

    achado = MatchResult(mod=mod, project_id=1, file_id=2, project_name="Sodium")
    assert Converter._result_line(achado) == "[green]++[/green] sodium.jar -> Sodium"

    sem_versao = MatchResult(
        mod=mod,
        diagnosis=Diagnosis(
            reason=MissingReason.VERSION_UNAVAILABLE, project_name="Sodium"
        ),
    )
    assert "[yellow]--[/yellow]" in Converter._result_line(sem_versao)
    assert "sem essa versão" in Converter._result_line(sem_versao)

    sem_projeto = MatchResult(
        mod=mod, diagnosis=Diagnosis(reason=MissingReason.NOT_ON_CURSEFORGE)
    )
    assert Converter._result_line(sem_projeto) == (
        "[red]--[/red] sodium.jar -> sem projeto no CurseForge"
    )

    com_erro = MatchResult(mod=mod, error="timeout")
    assert (
        Converter._result_line(com_erro)
        == "[red]--[/red] sodium.jar: erro (timeout)"
    )
