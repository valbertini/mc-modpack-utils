"""Atualização de um .mrpack para outra versão do Minecraft (sem rede)."""

import json
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from mrpack2curseforge.builders.mrpack import build_index, build_mrpack
from mrpack2curseforge.domain import ModrinthProject, UpdateStatus
from mrpack2curseforge.exceptions import Mrpack2CurseForgeError
from mrpack2curseforge.parsers.mrpack import MrpackParser
from mrpack2curseforge.progress import Reporter  # noqa: F401  (documenta o contrato)
from mrpack2curseforge.services.modrinth import ModrinthClient
from mrpack2curseforge.updater import (
    ManualPick,
    UpdateDecisions,
    Updater,
    _version_key,
    default_excluded,
)

INDEX = {
    "formatVersion": 1,
    "versionId": "1.0.0",
    "name": "Pack",
    "files": [
        {
            "path": "mods/sodium-0.5.0.jar",
            "hashes": {"sha1": "aaa"},
            "downloads": ["https://cdn.modrinth.com/data/AAA/versions/1/sodium.jar"],
            "env": {"client": "required", "server": "unsupported"},
        },
        {
            "path": "mods/jade-1.0.jar",
            "hashes": {"sha1": "bbb"},
            "downloads": ["https://cdn.modrinth.com/data/BBB/versions/1/jade.jar"],
        },
        {
            "path": "mods/velho-9.jar.disabled",
            "hashes": {"sha1": "ccc"},
            "downloads": ["https://cdn.modrinth.com/data/CCC/versions/1/velho.jar"],
        },
        {
            "path": "resourcepacks/visual.zip",
            "hashes": {"sha1": "ddd"},
            "downloads": ["https://cdn.modrinth.com/data/DDD/versions/1/visual.zip"],
        },
    ],
    "dependencies": {"minecraft": "1.20.1", "fabric-loader": "0.15.0"},
}


@pytest.fixture
def source(tmp_path: Path) -> Path:
    caminho = tmp_path / "pack.mrpack"

    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr("modrinth.index.json", json.dumps(INDEX))
        z.writestr("overrides/config/sodium.json", "{}")
        z.writestr("overrides/options.txt", "fov:80")

    return caminho


def versao(numero: str, arquivo: str, sha1: str, tipo: str = "release") -> dict:
    return {
        "id": numero,
        "version_number": numero,
        "version_type": tipo,
        "file": {
            "filename": arquivo,
            "url": f"https://cdn.modrinth.com/{arquivo}",
            "size": 1234,
            "sha1": sha1,
            "sha512": None,
        },
    }


class FakeModrinth:
    """Responde como o cliente real, sem tocar na rede."""

    def __init__(self, projetos: dict, versoes: dict):
        self.projetos = projetos
        self.versoes = versoes
        self.consultas: list[tuple[str, str, str | None]] = []

    def resolve_projects(self, files):
        return {
            f.file_path: self.projetos[f.file_path]
            for f in files
            if f.file_path in self.projetos
        }

    def latest_version(self, project_id, game_version, loader=None):
        self.consultas.append((project_id, game_version, loader))
        return self.versoes.get(project_id)

    def close(self): ...
    def __enter__(self): return self
    def __exit__(self, *_): ...


@pytest.fixture
def modrinth() -> FakeModrinth:
    return FakeModrinth(
        projetos={
            "mods/sodium-0.5.0.jar": ModrinthProject(
                project_id="AAA", slug="sodium", title="Sodium", version_number="0.5.0"
            ),
            "mods/jade-1.0.jar": ModrinthProject(
                project_id="BBB", slug="jade", title="Jade", version_number="1.0"
            ),
            "mods/velho-9.jar.disabled": ModrinthProject(
                project_id="CCC", slug="velho", title="Velho", version_number="9"
            ),
            # resourcepacks/visual.zip não é identificado -> UNKNOWN
        },
        versoes={
            "AAA": versao("0.6.0", "sodium-0.6.0.jar", "novo-sha"),
            # Jade devolve o mesmo hash: já está atualizado
            "BBB": versao("1.0", "jade-1.0.jar", "bbb"),
            # Velho não tem versão para o alvo
            "CCC": None,
        },
    )


@contextmanager
def fake_modrinth(modrinth: FakeModrinth):
    """O Updater abre o cliente real; aqui ele recebe o falso."""

    import mrpack2curseforge.updater as modulo

    class Fabrica:
        def __init__(self, *_a, **_k): ...
        def __enter__(self): return modrinth
        def __exit__(self, *_): ...

    original = modulo.ModrinthClient
    modulo.ModrinthClient = Fabrica  # type: ignore[assignment]
    try:
        yield
    finally:
        modulo.ModrinthClient = original  # type: ignore[assignment]


def rodar(tmp_path: Path, source: Path, modrinth: FakeModrinth, alvo="1.21.1"):
    with fake_modrinth(modrinth):
        return Updater(output_dir=tmp_path / "out").update(source, alvo)


# ------------------------------------------------------------------ status
def test_classifica_cada_arquivo(tmp_path: Path, source: Path, modrinth: FakeModrinth):
    outcome = rodar(tmp_path, source, modrinth)

    por_arquivo = {r.mod.file_name: r for r in outcome.results}

    assert por_arquivo["sodium-0.5.0.jar"].status is UpdateStatus.UPDATED
    assert por_arquivo["sodium-0.5.0.jar"].to_version == "0.6.0"
    assert por_arquivo["jade-1.0.jar"].status is UpdateStatus.UNCHANGED
    assert por_arquivo["velho-9.jar.disabled"].status is UpdateStatus.INCOMPATIBLE
    assert por_arquivo["visual.zip"].status is UpdateStatus.UNKNOWN

    assert outcome.summary == {
        "total": 4,
        "updated": 1,
        "kept_by_choice": 0,
        "unchanged": 1,
        # nada sem versão entra sozinho: entrar no pack é sempre decisão sua
        "incompatible": 0,
        "unknown": 0,
        "manual": 0,
        "excluded": 2,
        "unlisted": 0,
    }

    assert {r.mod.file_name for r in outcome.without_version} == {
        "velho-9.jar.disabled",
        "visual.zip",
    }
    assert {r.mod.file_name for r in outcome.with_version} == {
        "sodium-0.5.0.jar",
        "jade-1.0.jar",
    }


def test_loader_so_e_exigido_para_mods(tmp_path: Path, source: Path, modrinth):
    rodar(tmp_path, source, modrinth)

    por_projeto = {pid: loader for pid, _mc, loader in modrinth.consultas}

    assert por_projeto["AAA"] == "fabric"  # mod
    assert "DDD" not in por_projeto  # resourcepack não identificado


def test_mods_desativados_continuam_desativados(tmp_path: Path, source: Path):
    """Atualizar não pode reativar um mod que estava `.disabled`."""

    modrinth = FakeModrinth(
        projetos={
            "mods/velho-9.jar.disabled": ModrinthProject(
                project_id="CCC", slug="velho", title="Velho"
            )
        },
        versoes={"CCC": versao("10", "velho-10.jar", "outro-sha")},
    )

    outcome = rodar(tmp_path, source, modrinth)
    resultado = next(
        r for r in outcome.results if r.mod.file_name.endswith(".disabled")
    )

    assert resultado.status is UpdateStatus.UPDATED
    assert resultado.new_file.file_name == "velho-10.jar.disabled"
    assert resultado.new_file.file_path == "mods/velho-10.jar.disabled"


def test_alvo_anterior_ao_pack_e_sinalizado(tmp_path: Path, source: Path, modrinth):
    assert rodar(tmp_path, source, modrinth, alvo="1.19.2").downgrade is True
    assert rodar(tmp_path, source, modrinth, alvo="1.21.1").downgrade is False


def test_version_key():
    assert _version_key("1.21.10") > _version_key("1.21.9")
    assert _version_key("26.2") > _version_key("1.21.10")
    assert _version_key("") == (0,)


# ------------------------------------------------------------------- índice
def test_index_preserva_env_e_troca_so_o_que_mudou(
    tmp_path: Path, source: Path, modrinth
):
    outcome = rodar(tmp_path, source, modrinth)
    index = json.loads(
        zipfile.ZipFile(outcome.output).read("modrinth.index.json")
    )

    por_caminho = {entry["path"]: entry for entry in index["files"]}

    # atualizado: caminho e hash novos
    assert "mods/sodium-0.6.0.jar" in por_caminho
    assert por_caminho["mods/sodium-0.6.0.jar"]["hashes"]["sha1"] == "novo-sha"
    # o env do índice original é preservado
    assert por_caminho["mods/sodium-0.6.0.jar"]["env"]["client"] == "required"

    # não atualizados continuam iguais
    assert por_caminho["mods/jade-1.0.jar"]["hashes"]["sha1"] == "bbb"
    # sem versão para o alvo e sem decisão do usuário: fica de fora
    assert "resourcepacks/visual.zip" not in por_caminho

    assert index["dependencies"] == {"minecraft": "1.21.1", "fabric-loader": "0.15.0"}
    assert index["versionId"] == "1.0.0+mc1.21.1"


def test_mrpack_mantem_os_overrides(tmp_path: Path, source: Path, modrinth):
    outcome = rodar(tmp_path, source, modrinth)

    with zipfile.ZipFile(outcome.output) as pack:
        nomes = pack.namelist()

    assert "overrides/config/sodium.json" in nomes
    assert "overrides/options.txt" in nomes
    assert nomes.count("modrinth.index.json") == 1


def test_loader_version_pode_ser_trocada(tmp_path: Path, source: Path):
    pack = MrpackParser(source).parse()
    index = build_index(pack, [], "1.21.1", loader_version="0.16.9")

    assert index["dependencies"]["fabric-loader"] == "0.16.9"


# --------------------------------------------------------- troca de modloader
def test_index_troca_a_dependencia_do_loader(tmp_path: Path, source: Path):
    """Ir para o neoforge tem de trocar a dependência, não só o número."""

    pack = MrpackParser(source).parse()
    index = build_index(pack, [], "1.21.1", "21.1.5", loader="neoforge")

    assert index["dependencies"] == {"minecraft": "1.21.1", "neoforge": "21.1.5"}
    assert "fabric-loader" not in index["dependencies"]
    # o versionId diz que o pack não é mais o mesmo
    assert index["versionId"] == "1.0.0+mc1.21.1+neoforge"


def test_troca_de_loader_filtra_os_mods_pelo_loader_novo(
    tmp_path: Path, source: Path, modrinth: FakeModrinth
):
    with fake_modrinth(modrinth):
        Updater(output_dir=tmp_path / "out").analyze(
            source, "1.21.1", "21.1.5", loader="neoforge"
        )

    por_projeto = {pid: loader for pid, _mc, loader in modrinth.consultas}

    assert por_projeto["AAA"] == "neoforge"  # mod: filtra pelo loader de destino
    assert "DDD" not in por_projeto  # resourcepack não tem loader


def test_troca_de_loader_exige_a_versao_do_loader(
    tmp_path: Path, source: Path, modrinth: FakeModrinth
):
    """A versão do fabric que está no pack não serve para o neoforge."""

    with fake_modrinth(modrinth):
        updater = Updater(output_dir=tmp_path / "out")

        with pytest.raises(Mrpack2CurseForgeError, match="exige a versão do loader"):
            updater.analyze(source, "1.21.1", loader="neoforge")

        # o mesmo loader do pack continua não exigindo nada
        outcome = updater.analyze(source, "1.21.1", loader="fabric")
        assert outcome.loader_changed is False


def test_nome_da_saida_marca_o_pack_como_atualizado(
    tmp_path: Path, source: Path, modrinth: FakeModrinth
):
    outcome = rodar(tmp_path, source, modrinth)
    assert outcome.output.name.endswith("-[atualizado].mrpack")
    assert "mc1.21.1" in outcome.output.name


def test_build_mrpack_e_atomico(tmp_path: Path, source: Path):
    destino = tmp_path / "saida" / "novo.mrpack"
    build_mrpack(source, {"formatVersion": 1, "files": []}, destino)

    assert destino.exists()
    assert not (destino.parent / "novo.mrpack.part").exists()


# ----------------------------------------------------- escolha da versão
def test_prefere_release_e_depois_a_mais_nova():
    versoes = [
        {
            "id": "1", "version_number": "1.0-beta", "version_type": "beta",
            "date_published": "2026-05-01",
            "files": [{"primary": True, "filename": "b.jar", "url": "u", "hashes": {}}],
        },
        {
            "id": "2", "version_number": "0.9", "version_type": "release",
            "date_published": "2026-01-01",
            "files": [{"primary": True, "filename": "a.jar", "url": "u", "hashes": {}}],
        },
        {
            "id": "3", "version_number": "1.0", "version_type": "release",
            "date_published": "2026-04-01",
            "files": [{"primary": True, "filename": "c.jar", "url": "u", "hashes": {}}],
        },
    ]

    escolhida = ModrinthClient._pick_version(versoes)

    # a beta é mais nova, mas release ganha; entre releases, a mais recente
    assert escolhida["version_number"] == "1.0"

    so_beta = ModrinthClient._pick_version([versoes[0]])
    assert so_beta["version_type"] == "beta"

    assert ModrinthClient._pick_version([]) is None
    # versão sem arquivo utilizável é ignorada
    assert ModrinthClient._pick_version([{"id": "x", "files": []}]) is None


# --------------------------------------------------- escolha manual de versão
class FakeModrinthComVersao(FakeModrinth):
    """Além do automático, responde `version(id)` para as escolhas manuais."""

    def __init__(self, projetos, versoes, por_id):
        super().__init__(projetos, versoes)
        self.por_id = por_id

    def version(self, version_id):
        return self.por_id.get(version_id)

    def project_info(self, project_id):
        return {"title": f"Projeto {project_id}", "slug": project_id.lower()}


def test_escolha_manual_entra_no_pack_e_pode_ser_desfeita(
    tmp_path: Path, source: Path
):
    modrinth = FakeModrinthComVersao(
        projetos={
            "mods/sodium-0.5.0.jar": ModrinthProject(
                project_id="AAA", slug="sodium", title="Sodium"
            )
        },
        versoes={"AAA": None},  # nada para o alvo -> INCOMPATIBLE
        por_id={"escolhida": versao("0.4.9", "sodium-0.4.9.jar", "manual-sha")},
    )

    outcome = rodar(tmp_path, source, modrinth)
    alvo = next(r for r in outcome.results if r.mod.file_name == "sodium-0.5.0.jar")
    assert alvo.status is UpdateStatus.INCOMPATIBLE
    assert [r.mod.file_name for r in outcome.without_version] == [
        "sodium-0.5.0.jar",
        "jade-1.0.jar",
        "velho-9.jar.disabled",
        "visual.zip",
    ]

    with fake_modrinth(modrinth):
        updater = Updater(output_dir=tmp_path / "out")

        atualizado = updater.apply(
            outcome,
            UpdateDecisions(versions={"mods/sodium-0.5.0.jar": "escolhida"}),
        )

        alvo = next(
            r for r in atualizado.results if r.mod.file_name == "sodium-0.5.0.jar"
        )
        assert alvo.status is UpdateStatus.MANUAL
        assert alvo.to_version == "0.4.9"
        assert atualizado.summary["manual"] == 1

        index = json.loads(
            zipfile.ZipFile(atualizado.output).read("modrinth.index.json")
        )
        caminhos = {entry["path"] for entry in index["files"]}
        assert "mods/sodium-0.4.9.jar" in caminhos

        # desfazer: volta ao diagnóstico automático
        revertido = updater.apply(atualizado, UpdateDecisions())
        alvo = next(
            r for r in revertido.results if r.mod.file_name == "sodium-0.5.0.jar"
        )
        assert alvo.status is UpdateStatus.INCOMPATIBLE
        assert alvo.new_file is None


def test_escolha_manual_pode_vir_de_outro_projeto(tmp_path: Path, source: Path):
    """O mod certo às vezes é outro projeto (fork, renomeado, outra origem)."""

    de_outro = versao("2.0", "fork-2.0.jar", "fork-sha")
    de_outro["project_id"] = "ZZZ"

    modrinth = FakeModrinthComVersao(
        projetos={
            "mods/sodium-0.5.0.jar": ModrinthProject(
                project_id="AAA", slug="sodium", title="Sodium"
            )
        },
        versoes={"AAA": None},
        por_id={"do-fork": de_outro},
    )

    with fake_modrinth(modrinth):
        updater = Updater(output_dir=tmp_path / "out")
        outcome = updater.analyze(source, "1.21.1")

        atualizado = updater.apply(
            outcome,
            UpdateDecisions(
                versions={
                    "mods/sodium-0.5.0.jar": ManualPick(
                        version_id="do-fork",
                        project_id="ZZZ",
                        project_title="Sodium Fork",
                    )
                }
            ),
        )

        alvo = next(
            r for r in atualizado.results if r.mod.file_name == "sodium-0.5.0.jar"
        )

        assert alvo.status is UpdateStatus.MANUAL
        # o card passa a mostrar o projeto novo...
        assert alvo.modrinth.project_id == "ZZZ"
        assert alvo.modrinth.title == "Sodium Fork"
        # ...mas o detectado na análise continua guardado, para desfazer
        assert alvo.auto_modrinth.project_id == "AAA"

        index = json.loads(
            zipfile.ZipFile(atualizado.output).read("modrinth.index.json")
        )
        assert "mods/fork-2.0.jar" in {e["path"] for e in index["files"]}

        revertido = updater.apply(atualizado, UpdateDecisions())
        alvo = next(
            r for r in revertido.results if r.mod.file_name == "sodium-0.5.0.jar"
        )
        assert alvo.modrinth.project_id == "AAA"


def test_desmarcar_uma_mudanca_mantem_o_arquivo_atual(
    tmp_path: Path, source: Path, modrinth: FakeModrinth
):
    """A diff é uma proposta: o que o usuário desmarcar fica como está."""

    with fake_modrinth(modrinth):
        updater = Updater(output_dir=tmp_path / "out")
        outcome = updater.analyze(source, "1.21.1")

        # a análise sozinha não escreve nada
        assert outcome.packaged is False
        assert not outcome.output.exists()
        assert [r.mod.file_name for r in outcome.results if r.new_file] == [
            "sodium-0.5.0.jar"
        ]

        aplicado = updater.apply(
            outcome, UpdateDecisions(keep={"mods/sodium-0.5.0.jar"})
        )

        assert aplicado.packaged is True
        assert aplicado.summary["updated"] == 0
        assert aplicado.summary["kept_by_choice"] == 1

        index = json.loads(
            zipfile.ZipFile(aplicado.output).read("modrinth.index.json")
        )
        caminhos = {entry["path"] for entry in index["files"]}

        # ficou a versão que já estava no pack
        assert "mods/sodium-0.5.0.jar" in caminhos
        assert "mods/sodium-0.6.0.jar" not in caminhos


# ------------------------------------------- incluir ou não quem não tem versão
def test_sem_versao_fica_de_fora_ate_o_usuario_dizer_o_contrario(
    tmp_path: Path, source: Path, modrinth: FakeModrinth
):
    """Entrar no pack é a decisão que quebra o jogo: ela é sempre do usuário."""

    outcome = rodar(tmp_path, source, modrinth)
    por_arquivo = {r.mod.file_name: r for r in outcome.results}

    # nem o mod nem o resourcepack entram sozinhos
    assert default_excluded(por_arquivo["velho-9.jar.disabled"]) is True
    assert default_excluded(por_arquivo["visual.zip"]) is True
    # quem tem versão nunca é excluído por padrão
    assert default_excluded(por_arquivo["sodium-0.5.0.jar"]) is False


def test_usuario_decide_quem_entra_mesmo_sem_versao(
    tmp_path: Path, source: Path, modrinth: FakeModrinth
):
    """As duas decisões da revisão: incluir assim mesmo e deixar de fora."""

    with fake_modrinth(modrinth):
        updater = Updater(output_dir=tmp_path / "out")
        outcome = updater.analyze(source, "1.21.1")

        aplicado = updater.apply(
            outcome,
            UpdateDecisions(
                # o mod sem versão vai junto (funciona além da versão marcada)
                include={"mods/velho-9.jar.disabled"},
                # e o resourcepack, que entraria por padrão, fica de fora
                exclude={"resourcepacks/visual.zip"},
            ),
        )

        index = json.loads(
            zipfile.ZipFile(aplicado.output).read("modrinth.index.json")
        )
        caminhos = {entry["path"] for entry in index["files"]}

        assert "mods/velho-9.jar.disabled" in caminhos
        assert "resourcepacks/visual.zip" not in caminhos
        assert aplicado.summary["excluded"] == 1
