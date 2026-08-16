"""Registros de conversão: persistência dos metadados e regeneração do .zip."""

import json
import re
import zipfile
from pathlib import Path

from mrpack2curseforge.converter import ConversionOutcome, Resolution
from mrpack2curseforge.domain import (
    Diagnosis,
    MatchResult,
    MatchStrategy,
    MinecraftInfo,
    MissingReason,
    Modpack,
    PackFile,
)
from mrpack2curseforge.parsers.mrpack import MrpackParser
from mrpack2curseforge.records import (
    build_record,
    delete_record,
    list_records,
    load_record,
    results_from_record,
    save_record,
)
from mrpack2curseforge.reporting import build_report

INDEX = {
    "formatVersion": 1,
    "versionId": "1.0.0",
    "name": "Pack",
    "files": [
        {"path": "mods/a.jar", "hashes": {"sha1": "1"},
         "downloads": ["http://x/a.jar"]},
        {"path": "mods/b.jar", "hashes": {"sha1": "2"},
         "downloads": ["http://x/b.jar"]},
        {"path": "mods/c.jar", "hashes": {"sha1": "3"},
         "downloads": ["http://x/c.jar"]},
    ],
    "dependencies": {"minecraft": "1.21", "fabric-loader": "0.16.0"},
}


def make_source(tmp_path: Path) -> Path:
    path = tmp_path / "pack.mrpack"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("modrinth.index.json", json.dumps(INDEX))
    return path


def make_outcome(tmp_path: Path) -> tuple[ConversionOutcome, dict]:
    source = make_source(tmp_path)
    pack = MrpackParser(source).parse()
    a, b, c = pack.mods

    results = [
        # achado automaticamente
        MatchResult(
            mod=a,
            strategy=MatchStrategy.MODRINTH_SLUG,
            project_id=10,
            file_id=100,
            project_name="Mod A",
            project_slug="mod-a",
        ),
        # escolhido à mão
        MatchResult(
            mod=b,
            strategy=MatchStrategy.MANUAL,
            project_id=20,
            file_id=200,
            project_name="Mod B",
        ),
        # foi para overrides, com diagnóstico
        MatchResult(
            mod=c,
            diagnosis=Diagnosis(
                reason=MissingReason.VERSION_UNAVAILABLE,
                similarity=0.91,
                project_id=30,
                project_name="Mod C",
                closest_file_name="c-1.2.3.jar",
            ),
        ),
    ]

    outcome = ConversionOutcome(
        source=source,
        pack=pack,
        results=results,
        report=build_report(pack, results, source),
        output=tmp_path / "out" / "Pack-1.0.0-curseforge.zip",
        record_path=tmp_path / "out" / "conversions" / "Pack-1.0.0-curseforge.json",
    )

    resolutions = {
        "b.jar": Resolution(project_id=20, file_id=200, project_name="Mod B")
    }
    return outcome, resolutions


def test_record_keeps_every_decision(tmp_path: Path):
    outcome, resolutions = make_outcome(tmp_path)

    record = build_record(outcome, resolutions)

    assert record["source"] == "pack.mrpack"
    assert record["summary"]["matched"] == 2
    assert record["summary"]["version_unavailable"] == 1
    assert record["resolutions"]["b.jar"]["file_id"] == 200

    by_file = {mod["file_name"]: mod for mod in record["mods"]}
    assert by_file["a.jar"]["strategy"] == "modrinth-slug"
    assert by_file["b.jar"]["strategy"] == "manual"
    assert by_file["c.jar"]["diagnosis"]["closest_file_name"] == "c-1.2.3.jar"
    assert by_file["c.jar"]["status"] == "version-unavailable"


def test_save_load_and_list(tmp_path: Path):
    outcome, resolutions = make_outcome(tmp_path)
    output_dir = tmp_path / "out"

    saved = save_record(build_record(outcome, resolutions), output_dir)
    assert saved.parent.name == "conversions"

    rows = list_records(output_dir, input_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0]["pack"]["name"] == "Pack"
    assert rows[0]["manual_choices"] == 1
    assert rows[0]["source_available"] is True  # o .mrpack está lá

    # sem o .mrpack de origem não dá para regerar — a listagem avisa
    assert list_records(output_dir, input_dir=tmp_path / "vazio")[0][
        "source_available"
    ] is False

    assert load_record(output_dir, rows[0]["id"])["id"] == rows[0]["id"]
    assert delete_record(output_dir, rows[0]["id"]) is True
    assert list_records(output_dir, tmp_path) == []


def test_created_at_survives_a_reconversion(tmp_path: Path):
    outcome, resolutions = make_outcome(tmp_path)
    output_dir = tmp_path / "out"

    first = build_record(outcome, resolutions)
    save_record(first, output_dir)

    second = build_record(outcome, resolutions)
    second["created_at"] = first["created_at"] + 1000
    save_record(second, output_dir)

    stored = load_record(output_dir, first["id"])
    assert stored["created_at"] == first["created_at"]
    assert stored["updated_at"] >= stored["created_at"]


def test_results_are_rebuilt_from_the_record(tmp_path: Path):
    """O que permite regerar o .zip sem consultar o CurseForge de novo."""

    outcome, resolutions = make_outcome(tmp_path)
    record = build_record(outcome, resolutions)

    pack = MrpackParser(outcome.source).parse()
    restored = results_from_record(record, pack)

    assert [(r.project_id, r.file_id) for r in restored] == [
        (10, 100),
        (20, 200),
        (None, None),
    ]
    assert restored[1].strategy is MatchStrategy.MANUAL
    assert restored[2].diagnosis.reason is MissingReason.VERSION_UNAVAILABLE
    assert restored[2].diagnosis.similarity == 0.91


def test_unknown_mods_in_the_record_are_treated_as_unmatched(tmp_path: Path):
    """Se o .mrpack mudou depois da conversão, o mod novo vai para overrides."""

    outcome, resolutions = make_outcome(tmp_path)
    record = build_record(outcome, resolutions)
    record["mods"] = [m for m in record["mods"] if m["file_name"] != "a.jar"]

    pack = MrpackParser(outcome.source).parse()
    restored = results_from_record(record, pack)

    assert not restored[0].matched
    assert restored[1].matched


def test_rebuild_regenerates_the_zip_without_the_curseforge_api(
    tmp_path: Path, monkeypatch
):
    from mrpack2curseforge.converter import Converter

    outcome, resolutions = make_outcome(tmp_path)
    record = build_record(outcome, resolutions)
    output_dir = tmp_path / "out"
    save_record(record, output_dir)

    converter = Converter(output_dir=output_dir)

    # o único acesso à rede seria o download dos jars de overrides
    calls = {}

    def fake_assemble(pack, results, parser, reuse=False, base_name=None):
        calls["results"] = results
        calls["base_name"] = base_name
        outcome.output.parent.mkdir(parents=True, exist_ok=True)
        outcome.output.write_bytes(b"zip")
        return outcome.output, None, 0

    monkeypatch.setattr(converter, "_assemble", fake_assemble)

    rebuilt = converter.rebuild(load_record(output_dir, record["id"]), outcome.source)

    assert rebuilt.packaged
    assert rebuilt.report.matched == 2
    # regerar não renomeia: o nome vem do registro, não do padrão atual
    assert calls["base_name"] == record["id"]
    # a escolha manual continua valendo depois de regerar
    manual = [r for r in calls["results"] if r.strategy is MatchStrategy.MANUAL]
    assert [(r.project_id, r.file_id) for r in manual] == [(20, 200)]


# --------------------------------------------------- resumo estilo terraform
class CapturingReporter:
    """Reporter que guarda as linhas (sem a marcação do rich) em vez de imprimir."""

    def __init__(self):
        self.lines: list[str] = []

    def start(self): ...
    def stop(self): ...
    def stage(self, name, total=None): ...
    def advance(self, amount=1): ...

    def _add(self, message: str) -> None:
        self.lines.append(re.sub(r"\[/?[a-z]+\]", "", message))

    log = _add
    info = _add


def test_analysis_summary_groups_and_hides_the_successes(tmp_path: Path):
    from mrpack2curseforge.converter import Converter

    outcome, _ = make_outcome(tmp_path)

    # um quarto mod, sem projeto no CurseForge
    extra = PackFile(file_name="d.jar", file_path="mods/d.jar")
    outcome.results.append(
        MatchResult(
            mod=extra,
            diagnosis=Diagnosis(reason=MissingReason.NOT_ON_CURSEFORGE, similarity=0.3),
        )
    )

    reporter = CapturingReporter()
    Converter(output_dir=tmp_path, reporter=reporter)._log_analysis(outcome.results)

    text = "\n".join(reporter.lines)

    # os que deram certo entram como contagem, não como lista
    assert "2 mod(s) encontrados no CurseForge (não listados)" in text
    assert "a.jar" not in text and "b.jar" not in text

    # os que exigem decisão aparecem, agrupados e com o motivo
    assert "1 mod(s) sem essa versão no CurseForge" in text
    assert "c.jar" in text and "c-1.2.3.jar" in text
    assert "1 mod(s) sem projeto no CurseForge" in text
    assert "d.jar" in text

    # e o status final
    assert "2 no manifest" in text
    assert "1 sem a versão" in text
    assert "1 sem projeto" in text

    # ordem: sucesso -> versão indisponível -> sem projeto -> resumo
    assert (
        text.index("não listados")
        < text.index("sem essa versão")
        < text.index("sem projeto no CurseForge")
        < text.index("Resumo:")
    )
