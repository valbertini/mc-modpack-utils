"""Testes da API web (sem rede: nenhum teste dispara conversão real)."""

import json
import threading
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mrpack2curseforge.converter import ConversionOutcome, Converter, Resolution
from mrpack2curseforge.domain import (
    Diagnosis,
    MatchResult,
    MatchStrategy,
    MinecraftInfo,
    MissingReason,
    Modpack,
    ModrinthProject,
    PackFile,
)
from mrpack2curseforge.exceptions import ConversionCancelled
from mrpack2curseforge.reporting import build_report
from mrpack2curseforge.web.jobs import Job
from mrpack2curseforge.web.server import create_app

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


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    # os testes não podem depender do `.env` da máquina: sem isto, comentar a
    # chave no arquivo local fazia `/api/convert` responder 400 em vez de 409
    from mrpack2curseforge.config import Config

    monkeypatch.setattr(Config, "CURSEFORGE_API_KEY", "chave-de-teste")

    app = create_app(input_dir=tmp_path / "in", output_dir=tmp_path / "out")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_job(client, tmp_path: Path) -> Job:
    """Job já concluído, montado à mão (não usa rede)."""

    mod = PackFile(
        file_name="litematica-fabric-26.2-0.28.2.jar",
        file_path="mods/litematica-fabric-26.2-0.28.2.jar",
    )

    pack = Modpack(
        name="Pack Web",
        version="1.0.0",
        minecraft=MinecraftInfo(
            version="1.21", loader="fabric", loader_version="0.16.0"
        ),
        mods=[mod],
    )

    result = MatchResult(
        mod=mod,
        modrinth=ModrinthProject(
            project_id="AAA",
            slug="litematica",
            title="Litematica",
            icon_url="https://cdn.modrinth.com/lite.png",
        ),
        diagnosis=Diagnosis(
            reason=MissingReason.VERSION_UNAVAILABLE,
            similarity=0.98,
            project_id=308892,
            project_name="Litematica",
            project_slug="litematica",
            closest_file_id=777,
            closest_file_name="litematica-fabric-26.2-0.28.3.jar",
        ),
    )

    source = write_mrpack(tmp_path / "pack.mrpack")
    output = tmp_path / "out" / "pack-curseforge.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"zip-de-mentira")

    record_path = tmp_path / "out" / "conversions" / "pack-curseforge.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    report = build_report(pack, [result], source)
    record_path.write_text(json.dumps({"id": "pack-curseforge"}), encoding="utf-8")

    job = Job(id="jobtest", source=source, status="done")
    job.outcome = ConversionOutcome(
        source=source,
        pack=pack,
        results=[result],
        report=report,
        output=output,
        record_path=record_path,
    )

    client.app.state.jobs.jobs[job.id] = job
    return job


def update_job(
    client,
    tmp_path: Path,
    job_id: str,
    status: str = "done",
    results=None,
    **campos,
) -> Job:
    """Job de atualização montado à mão (nenhum teste da API usa rede)."""

    from mrpack2curseforge.domain import UpdateResult, UpdateStatus
    from mrpack2curseforge.updater import UpdateOutcome

    mod = PackFile(file_name="a.jar", file_path="mods/a.jar")
    pack = Modpack(
        name="P",
        version="1",
        minecraft=MinecraftInfo(
            version="1.20.1", loader="fabric", loader_version="0.15"
        ),
        mods=[mod],
    )

    job = Job(id=job_id, source=tmp_path / "p.mrpack", status=status, kind="update")
    job.outcome = UpdateOutcome(
        source=tmp_path / "p.mrpack",
        pack=pack,
        minecraft_version="1.21.1",
        loader_version="0.15",
        results=results if results is not None
        else [UpdateResult(mod=mod, status=UpdateStatus.UPDATED)],
        output=tmp_path / "p.mrpack",
        report_path=tmp_path / "p-update.json",
        **campos,
    )

    client.app.state.jobs.jobs[job.id] = job
    return job


# ------------------------------------------------------------------ básicos
def test_index_and_static_are_served_locally(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "mrpack2curseforge" in page.text

    # a página não depende de CDN: HTML, CSS e JS saem daqui
    assert "http://" not in page.text.replace("http-equiv", "")
    assert "https://" not in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_favicon_is_a_real_response(client):
    """Um 204 com corpo quebra o protocolo: o h11 aborta a conexão.

    Era o que acontecia a cada abertura da página
    ("Too much data for declared Content-Length").
    """

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.content.startswith(b"<svg")


def test_security_headers(client):
    headers = client.get("/").headers
    csp = headers["content-security-policy"]

    # a aplicação em si é servida da máquina local...
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    # ...mas os ícones dos projetos do CurseForge podem ser carregados
    assert "img-src 'self' data: https:" in csp
    assert headers["x-content-type-options"] == "nosniff"


def test_state_lists_input_packs(client, tmp_path: Path):
    assert client.get("/api/state").json()["packs"] == []

    write_mrpack(tmp_path / "in" / "meu.mrpack")

    state = client.get("/api/state").json()
    assert [p["name"] for p in state["packs"]] == ["meu.mrpack"]

    # a lista precisa dizer para qual Minecraft/loader é o pack
    pack = state["packs"][0]
    assert pack["minecraft"] == "1.21"
    assert pack["loader"] == "fabric"
    assert pack["loader_version"] == "0.16.0"
    assert pack["mods"] == 1


def test_state_survives_an_unreadable_pack(client, tmp_path: Path):
    """Um .mrpack quebrado na pasta não pode derrubar a lista inteira."""

    write_mrpack(tmp_path / "in" / "bom.mrpack")
    (tmp_path / "in" / "quebrado.mrpack").write_bytes(b"nao sou zip")

    packs = {p["name"]: p for p in client.get("/api/state").json()["packs"]}

    assert packs["bom.mrpack"]["minecraft"] == "1.21"
    assert packs["quebrado.mrpack"]["minecraft"] is None


def test_state_diz_quando_cada_pack_foi_usado(client, tmp_path: Path):
    """É o que faz a lista mostrar primeiro o que você mexeu por último."""

    import time

    write_mrpack(tmp_path / "in" / "convertido.mrpack")
    write_mrpack(tmp_path / "in" / "atualizado.mrpack")
    write_mrpack(tmp_path / "in" / "intocado.mrpack")

    saida = tmp_path / "out"
    (saida / "conversions").mkdir(parents=True, exist_ok=True)
    (saida / "conversions" / "x.json").write_text(
        json.dumps(
            {"id": "x", "source": "convertido.mrpack", "updated_at": 1000.0}
        ),
        encoding="utf-8",
    )
    (saida / "p-update.json").write_text(
        json.dumps({"source": "atualizado.mrpack", "output": "p.mrpack"}),
        encoding="utf-8",
    )

    packs = {p["name"]: p for p in client.get("/api/state").json()["packs"]}

    assert packs["convertido.mrpack"]["last_used"] == 1000.0
    # a atualização usa o mtime do relatório
    assert packs["atualizado.mrpack"]["last_used"] > time.time() - 60
    assert packs["intocado.mrpack"]["last_used"] is None


def test_shutdown_cancela_o_que_esta_rodando(client, fake_job):
    """Sair no meio de um download deixaria arquivos `.part` para trás."""

    fake_job.status = "running"

    class ServidorFalso:
        should_exit = False

    servidor = ServidorFalso()
    client.app.state.server = servidor

    dados = client.post("/api/shutdown").json()

    assert dados["cancelled"] == [fake_job.source.name]
    assert fake_job.cancel_event.is_set()
    assert servidor.should_exit is True


def test_shutdown_sem_servidor_nao_finge_que_deu_certo(client):
    """Rodando por fora do comando `web` não há o que desligar."""

    if hasattr(client.app.state, "server"):
        del client.app.state.server

    assert client.post("/api/shutdown").status_code == 501


def test_cache_endpoints(client, tmp_path: Path, monkeypatch):
    from mrpack2curseforge.config import Config
    from mrpack2curseforge.services.cache import SimpleCache

    caminho = tmp_path / "cache" / "c.sqlite3"
    monkeypatch.setattr(Config, "CACHE_PATH", caminho)

    with SimpleCache(caminho) as cache:
        cache.set("search", "sodium", [{"id": 1}])

    info = client.get("/api/cache").json()
    assert "c.sqlite3" in info["files"]

    apagado = client.delete("/api/cache").json()
    assert "c.sqlite3" in apagado["removed"]
    assert apagado["locked"] == []

    assert client.get("/api/cache").json()["files"] == []


def test_loaders_endpoint(client):
    loaders = client.get("/api/loaders").json()["loaders"]

    assert "fabric" in loaders
    assert "neoforge" in loaders


# ------------------------------------------------------------------ upload
def test_upload_rejects_non_mrpack(client):
    response = client.post(
        "/api/upload",
        files={"file": ("virus.exe", b"nope", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_upload_rejects_corrupted_mrpack(client):
    response = client.post(
        "/api/upload", files={"file": ("quebrado.mrpack", b"nao sou zip")}
    )
    assert response.status_code == 400
    assert client.get("/api/state").json()["packs"] == []


def test_upload_accepts_valid_mrpack(client, tmp_path: Path):
    payload = write_mrpack(tmp_path / "upload.mrpack").read_bytes()

    response = client.post("/api/upload", files={"file": ("upload.mrpack", payload)})

    assert response.status_code == 200
    assert response.json()["name"] == "upload.mrpack"
    assert [p["name"] for p in client.get("/api/state").json()["packs"]] == [
        "upload.mrpack"
    ]


def test_inspect_pack(client, tmp_path: Path):
    write_mrpack(tmp_path / "in" / "meu.mrpack")

    data = client.get("/api/packs/meu.mrpack/inspect").json()

    assert data["name"] == "Pack Web"
    assert data["mods"] == 1
    assert data["loader"] == "fabric-0.16.0"


def test_missing_pack_returns_404(client):
    assert client.get("/api/packs/nao-existe.mrpack/inspect").status_code == 404
    assert client.post("/api/convert", json={"file": "nada.mrpack"}).status_code == 404


# --------------------------------------------------------------- conflitos
def test_conflicts_expose_diagnosis(client, fake_job):
    data = client.get(f"/api/jobs/{fake_job.id}/conflicts").json()

    assert len(data["conflicts"]) == 1
    conflict = data["conflicts"][0]

    assert conflict["reason"] == "version-unavailable"
    assert conflict["suggestion"]["project_id"] == 308892
    assert conflict["suggestion"]["closest_file_name"].endswith("0.28.3.jar")
    assert conflict["resolution"] is None

    # o mod original vai junto, para comparar com os candidatos do CurseForge
    assert conflict["modrinth"]["title"] == "Litematica"
    assert conflict["modrinth"]["icon"] == "https://cdn.modrinth.com/lite.png"
    assert conflict["modrinth"]["url"] == "https://modrinth.com/mod/litematica"


def test_saving_resolutions_marks_the_job_dirty(client, fake_job):
    """`dirty` é o que faz o botão "Aplicar mudanças" aparecer."""

    assert fake_job.dirty is False

    client.put(
        f"/api/jobs/{fake_job.id}/resolutions",
        json={
            "resolutions": [
                {
                    "file_name": "litematica-fabric-26.2-0.28.2.jar",
                    "project_id": 308892,
                    "file_id": 777,
                }
            ]
        },
    )

    assert fake_job.dirty is True
    escolha = fake_job.resolutions["litematica-fabric-26.2-0.28.2.jar"]
    assert escolha.project_id == 308892


def test_download_endpoints(client, fake_job):
    zip_response = client.get(f"/api/jobs/{fake_job.id}/download")
    assert zip_response.status_code == 200
    assert zip_response.content == b"zip-de-mentira"

    # o middleware de cabeçalhos não pode mexer no corpo de um arquivo
    assert int(zip_response.headers["content-length"]) == len(zip_response.content)
    assert "default-src 'self'" in zip_response.headers["content-security-policy"]

    assert client.get(f"/api/jobs/{fake_job.id}/report").status_code == 200
    assert client.get("/api/jobs/inexistente").status_code == 404


def test_close_survives_a_locked_zip(client, fake_job, monkeypatch):
    """Fechar durante um download não pode explodir (Windows trava o arquivo)."""

    fake_job.outcome.packaged = True

    original = Path.unlink

    def refuse(self, *args, **kwargs):
        if self.suffix == ".zip":
            raise PermissionError("arquivo em uso")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)

    assert client.post(f"/api/jobs/{fake_job.id}/close").json() == {"closed": True}
    assert client.get("/api/state").json()["current_job"] is None


def test_snapshot_says_which_kind_of_job_it_is(client, fake_job, tmp_path: Path):
    """Sem isso a interface não sabe qual painel usar e joga tudo na conversão."""

    from mrpack2curseforge.domain import UpdateResult, UpdateStatus

    assert client.get(f"/api/jobs/{fake_job.id}").json()["kind"] == "conversion"
    assert client.get("/api/state").json()["current_job"]["kind"] == "conversion"

    mod = PackFile(file_name="a.jar", file_path="mods/a.jar")
    job = update_job(
        client,
        tmp_path,
        "upd",
        results=[
            UpdateResult(
                mod=mod,
                status=UpdateStatus.INCOMPATIBLE,
                modrinth=ModrinthProject(project_id="AAA", title="Sodium"),
            )
        ],
    )

    snapshot = client.get(f"/api/jobs/{job.id}").json()

    assert snapshot["kind"] == "update"
    assert "report" not in snapshot  # é o payload da conversão, não deste job
    assert snapshot["update"]["to_minecraft"] == "1.21.1"

    # o que ficou sem versão vai para a aba própria, já marcado para ficar de
    # fora (é um mod: um .jar de outra versão costuma quebrar o jogo)
    assert snapshot["update"]["with_version"] == []

    pendente = snapshot["update"]["without_version"][0]
    assert pendente["file_path"] == "mods/a.jar"
    assert pendente["project_id"] == "AAA"
    assert pendente["chosen"] is None
    assert pendente["excluded"] is True


def test_update_resolutions_move_o_arquivo_entre_as_abas(client, tmp_path: Path):
    """Incluir/excluir é decisão do usuário: o snapshot precisa refletir na hora."""

    from mrpack2curseforge.domain import UpdateResult, UpdateStatus

    mod = PackFile(file_name="a.jar", file_path="mods/a.jar")
    job = update_job(
        client,
        tmp_path,
        "upd2",
        status="awaiting_review",
        results=[UpdateResult(mod=mod, status=UpdateStatus.INCOMPATIBLE)],
    )

    salvo = client.put(
        f"/api/jobs/{job.id}/update-resolutions",
        json={"choices": [], "keep": [], "exclude": [], "include": ["mods/a.jar"]},
    )
    assert salvo.status_code == 200
    assert salvo.json()["include"] == 1

    pendente = client.get(f"/api/jobs/{job.id}").json()["update"]["without_version"][0]
    assert pendente["excluded"] is False  # o padrão era ficar de fora

    # caminho que não existe no pack é descartado em silêncio nas listas…
    salvo = client.put(
        f"/api/jobs/{job.id}/update-resolutions",
        json={
            "choices": [],
            "keep": [],
            "exclude": ["mods/fantasma.jar"],
            "include": [],
        },
    )
    assert salvo.json()["exclude"] == 0

    # …mas uma escolha de versão para um arquivo inexistente é erro
    recusado = client.put(
        f"/api/jobs/{job.id}/update-resolutions",
        json={"choices": [{"file_path": "mods/fantasma.jar", "version_id": "x"}]},
    )
    assert recusado.status_code == 404


def test_relatorio_json_serve_as_duas_ferramentas(client, fake_job, tmp_path: Path):
    """A conversão guarda em `record_path`, a atualização em `report_path`."""

    assert client.get(f"/api/jobs/{fake_job.id}/report").status_code == 200

    job = update_job(client, tmp_path, "upd-rel", packaged=True)
    assert client.get(f"/api/jobs/{job.id}/report").status_code == 404

    job.outcome.report_path.write_text("{}", encoding="utf-8")
    resposta = client.get(f"/api/jobs/{job.id}/report")

    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("application/json")


def test_reapply_recusa_estado_invalido(client, tmp_path: Path):
    """Cancelar e mandar aplicar de novo geraria um pack que ninguém pediu."""

    job = update_job(client, tmp_path, "upd3", status="cancelled")

    assert client.post(f"/api/jobs/{job.id}/reapply").status_code == 409

    # já pronta, dá para regerar com decisões novas
    job.status = "done"
    assert client.post(f"/api/jobs/{job.id}/reapply").status_code == 200


def test_salvar_decisoes_marca_a_atualizacao_como_desatualizada(
    client, tmp_path: Path
):
    """Sem isso a interface não sabe que o .mrpack em disco ficou para trás."""

    job = update_job(client, tmp_path, "upd4", packaged=True)

    assert client.get(f"/api/jobs/{job.id}").json()["dirty"] is False

    client.put(
        f"/api/jobs/{job.id}/update-resolutions",
        json={"choices": [], "keep": ["mods/a.jar"], "exclude": [], "include": []},
    )

    assert client.get(f"/api/jobs/{job.id}").json()["dirty"] is True


def test_job_snapshot_has_report_and_conflict_count(client, fake_job):
    snapshot = client.get(f"/api/jobs/{fake_job.id}").json()

    assert snapshot["status"] == "done"
    assert snapshot["report"]["total_mods"] == 1
    assert snapshot["report"]["version_unavailable"] == 1
    assert snapshot["conflicts"] == 1
    assert snapshot["unresolved"] == 1

    # o snapshot é buscado a cada 600 ms: nada de listar todos os mods aqui
    assert "mods" not in snapshot["report"]


def test_conflicts_disappear_after_the_modpack_is_packaged(client, fake_job):
    """Depois de aplicar não há mais o que decidir: a aba fica vazia."""

    assert client.get(f"/api/jobs/{fake_job.id}/conflicts").json()["conflicts"]

    fake_job.outcome.packaged = True

    assert client.get(f"/api/jobs/{fake_job.id}/conflicts").json()["conflicts"] == []
    snapshot = client.get(f"/api/jobs/{fake_job.id}").json()
    assert snapshot["conflicts"] == 0
    assert snapshot["unresolved"] == 0


def test_unresolved_shrinks_as_conflicts_are_resolved(client, fake_job):
    assert client.get(f"/api/jobs/{fake_job.id}").json()["unresolved"] == 1

    client.put(
        f"/api/jobs/{fake_job.id}/resolutions",
        json={
            "resolutions": [
                {
                    "file_name": "litematica-fabric-26.2-0.28.2.jar",
                    "project_id": 308892,
                    "file_id": 777,
                }
            ]
        },
    )

    snapshot = client.get(f"/api/jobs/{fake_job.id}").json()

    # o total continua (para mostrar a escolha feita), o pendente zera
    assert snapshot["conflicts"] == 1
    assert snapshot["unresolved"] == 0
    # e o plano já reflete a escolha ainda não aplicada
    assert snapshot["plan"] == {
        "manifest": 1,
        "manual": 1,
        "downloads": 0,
        "extra_files": 0,
        "override_files": 0,
    }


def test_bulk_resolutions_replace_previous_ones(client, fake_job):
    payload = {
        "resolutions": [
            {
                "file_name": "litematica-fabric-26.2-0.28.2.jar",
                "project_id": 308892,
                "file_id": 777,
                "curseforge_file_name": "litematica-fabric-26.2-0.28.3.jar",
            }
        ]
    }

    saved = client.put(f"/api/jobs/{fake_job.id}/resolutions", json=payload).json()
    assert saved["saved"] == 1
    assert saved["conflicts"][0]["resolution"]["file_id"] == 777

    # salvar uma lista vazia limpa tudo
    cleared = client.put(
        f"/api/jobs/{fake_job.id}/resolutions", json={"resolutions": []}
    ).json()
    assert cleared["saved"] == 0
    assert fake_job.resolutions == {}


def test_bulk_resolutions_reject_unknown_mod(client, fake_job):
    response = client.put(
        f"/api/jobs/{fake_job.id}/resolutions",
        json={"resolutions": [{"file_name": "x.jar", "project_id": 1, "file_id": 2}]},
    )
    assert response.status_code == 404


# ------------------------------------------------------- um job por vez
def test_only_one_conversion_at_a_time(client, fake_job, tmp_path: Path):
    write_mrpack(tmp_path / "in" / "outro.mrpack")

    response = client.post("/api/convert", json={"file": "outro.mrpack"})

    assert response.status_code == 409
    assert "Feche-a antes" in response.json()["detail"]


def test_close_keeps_a_zip_this_job_did_not_create(client, fake_job):
    """`analyze()` já calcula o caminho do .zip; um job que não empacotou nada
    não pode apagar o arquivo de uma conversão anterior (ou do CLI)."""

    assert fake_job.outcome.packaged is False
    assert fake_job.outcome.output.exists()

    client.post(f"/api/jobs/{fake_job.id}/close")

    assert fake_job.outcome.output.exists()


def test_close_frees_the_slot_and_cleans_the_work_dir(client, fake_job, tmp_path: Path):
    work = tmp_path / "out" / ".work" / "pack"
    work.mkdir(parents=True)
    (work / "manifest.json").write_text("{}")
    fake_job.outcome.work_dir = work
    fake_job.outcome.packaged = True  # este job gerou o .zip

    assert client.get("/api/state").json()["current_job"]["id"] == fake_job.id

    assert client.post(f"/api/jobs/{fake_job.id}/close").json() == {"closed": True}

    assert not work.exists()
    # o .zip é regenerável a partir do registro, então não fica ocupando disco
    assert not fake_job.outcome.output.exists()
    assert client.get("/api/state").json()["current_job"] is None
    assert client.get(f"/api/jobs/{fake_job.id}").status_code == 404


def test_cancel_sets_the_event(client, fake_job):
    assert not fake_job.cancel_event.is_set()
    client.post(f"/api/jobs/{fake_job.id}/cancel")
    assert fake_job.cancel_event.is_set()


def test_cancel_while_paused_finishes_the_job(client, fake_job):
    """Pausado em conflitos não há thread rodando: o cancelamento é imediato."""

    fake_job.status = "awaiting_conflicts"

    client.post(f"/api/jobs/{fake_job.id}/cancel")

    assert fake_job.status == "cancelled"
    # e não dá mais para aplicar
    assert client.post(f"/api/jobs/{fake_job.id}/apply").status_code == 409


def test_cancelled_converter_raises_before_working(tmp_path: Path):
    event = threading.Event()
    event.set()

    converter = Converter(output_dir=tmp_path, cancel_event=event)

    assert converter.cancelled
    with pytest.raises(ConversionCancelled):
        converter._check_cancel()


def test_apply_requires_analysis(client, tmp_path: Path):
    from mrpack2curseforge.web.jobs import Job

    empty = Job(id="vazio", source=write_mrpack(tmp_path / "vazio.mrpack"))
    client.app.state.jobs.jobs[empty.id] = empty

    assert client.post(f"/api/jobs/{empty.id}/apply").status_code == 400


# -------------------------------------------------------- detalhes/saídas
def test_records_endpoints(client, tmp_path: Path):
    """Registros persistem os metadados; o .zip não fica guardado."""

    records = tmp_path / "out" / "conversions"
    records.mkdir(parents=True, exist_ok=True)
    (records / "meu-pack.json").write_text(
        json.dumps(
            {
                "id": "meu-pack",
                "source": "meu.mrpack",
                "pack": {"name": "Meu Pack", "version": "1.0"},
                "summary": {"total_mods": 3, "matched": 2},
                "mods": [],
                "updated_at": 1,
            }
        ),
        encoding="utf-8",
    )

    listed = client.get("/api/records").json()["records"]
    assert [r["id"] for r in listed] == ["meu-pack"]
    assert listed[0]["source_available"] is False  # o .mrpack não está lá

    detail = client.get("/api/records/meu-pack").json()
    assert detail["pack"]["name"] == "Meu Pack"

    # sem o arquivo de origem não dá para regerar
    failed = client.post("/api/records/meu-pack/generate")
    assert failed.status_code == 400
    assert "não está mais em input_modpacks" in failed.json()["detail"]

    assert client.delete("/api/records/meu-pack").json() == {"deleted": True}
    assert client.get("/api/records").json()["records"] == []
    assert client.get("/api/records/meu-pack").status_code == 404


def test_inspect_lists_mod_files_and_extras(client, tmp_path: Path):
    write_mrpack(tmp_path / "in" / "meu.mrpack")

    data = client.get("/api/packs/meu.mrpack/inspect").json()

    assert data["mod_files"] == ["litematica-fabric-26.2-0.28.2.jar"]
    assert data["file"] == "meu.mrpack"
    assert "size_mb" in data


# --------------------------------------------------------------- finish
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
    mods = [
        PackFile(file_name="a.jar", file_path="mods/a.jar"),
        PackFile(file_name="b.jar", file_path="mods/b.jar"),
    ]
    pack = Modpack(
        name="P",
        version="1",
        minecraft=MinecraftInfo(version="1.21", loader="fabric", loader_version="0.1"),
        mods=mods,
        extra_files=[PackFile(file_name="rp.zip", file_path="resourcepacks/rp.zip")],
        override_paths=[Path("config/a.json")],
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
        "downloads": 1,
        "extra_files": 1,
        "override_files": 1,
    }


def test_drop_resolved_overrides_keeps_files_from_the_mrpack(tmp_path: Path):
    """Jars que já vinham no overrides/ do mrpack não podem ser apagados."""

    overrides = tmp_path / "overrides"
    (overrides / "mods").mkdir(parents=True)

    do_pack = overrides / "mods" / "do-pack.jar"
    do_matcher = overrides / "mods" / "resolvido.jar"
    do_pack.write_text("a")
    do_matcher.write_text("b")

    pack = Modpack(
        name="P",
        version="1",
        minecraft=MinecraftInfo(version="1.21", loader="fabric", loader_version="0.1"),
        override_paths=[Path("mods/do-pack.jar")],
    )

    results = [
        MatchResult(
            mod=PackFile(file_name="do-pack.jar", file_path="mods/do-pack.jar"),
            project_id=1,
            file_id=2,
        ),
        MatchResult(
            mod=PackFile(file_name="resolvido.jar", file_path="mods/resolvido.jar"),
            project_id=3,
            file_id=4,
        ),
    ]

    Converter._drop_resolved_overrides(pack, results, overrides)

    assert do_pack.exists()
    assert not do_matcher.exists()


# ------------------------------------------------------------ log do terminal
def test_log_formatting():
    from mrpack2curseforge.web.jobs import _plain

    # o nível vem da PRIMEIRA tag: a linha de resumo tem verde, amarelo e vermelho
    assert _plain(
        "[bold]Resumo:[/bold] [green]45 ok[/green] · [red]0 sem projeto[/red]"
    ) == ("Resumo: 45 ok · 0 sem projeto", "info")

    assert _plain("[green]++[/green] achado")[1] == "ok"
    assert _plain("[yellow]--[/yellow] overrides")[1] == "warn"
    assert _plain("[red]--[/red] erro")[1] == "error"

    # a indentação do resumo é preservada
    assert _plain("     [yellow]--[/yellow] litematica.jar")[0] == (
        "     -- litematica.jar"
    )

    # colchetes que fazem parte do nome do arquivo não são comidos
    assert _plain("[red]--[/red] mod[1.20].jar")[0] == "-- mod[1.20].jar"

    # linha vazia é espaçador, não é descartada
    assert _plain("") == ("", "info")


def test_summary_line_is_coloured_number_by_number():
    """No terminal da interface cada número do resumo sai na sua cor."""

    from mrpack2curseforge.web.jobs import _segments

    parts = _segments(
        "[bold]Resumo:[/bold] [green]45[/green] no manifest · "
        "[yellow]4[/yellow] sem a versão · [red]0[/red] sem projeto"
    )

    coloured = [(p["text"], p["level"]) for p in parts if p["level"] != "info"]

    assert coloured == [("45", "ok"), ("4", "warn"), ("0", "error")]
    assert "".join(p["text"] for p in parts) == (
        "Resumo: 45 no manifest · 4 sem a versão · 0 sem projeto"
    )


def test_streamed_line_per_mod():
    """Durante a busca cada mod gera uma linha, no estilo do terraform."""

    from mrpack2curseforge.converter import Converter

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


def test_endpoints_das_configuracoes(client, tmp_path: Path, monkeypatch):
    """A tela de configurações é um editor do .env — e a chave não vaza nela."""

    from mrpack2curseforge import settings

    env = tmp_path / ".env"
    monkeypatch.setattr(settings, "env_path", lambda: env)
    env.write_text("CURSEFORGE_API_KEY=chave-secreta-1234\n", encoding="utf-8")

    estado = client.get("/api/settings").json()
    api = next(c for c in estado["campos"] if c["chave"] == "CURSEFORGE_API_KEY")

    assert "chave-secreta" not in api["valor"]
    assert api["valor"].endswith("1234")

    salvo = client.put("/api/settings", json={"values": {"M2CF_WORKERS": "9"}}).json()
    assert salvo["ok"] is True
    assert salvo["restart_needed"] == []

    # trocar de pasta exige reiniciar: a interface precisa saber disso
    salvo = client.put(
        "/api/settings", json={"values": {"M2CF_INPUT_DIR": "/tmp/x"}}
    ).json()
    assert salvo["restart_needed"] == ["M2CF_INPUT_DIR"]

    recusado = client.put(
        "/api/settings", json={"values": {"M2CF_WORKERS": "-3"}}
    )
    assert recusado.status_code == 400

    depois = client.post("/api/settings/reset").json()
    assert depois["ok"] is True
    assert settings.ler()["CURSEFORGE_API_KEY"] == "chave-secreta-1234"


def test_configuracoes_travam_com_trabalho_aberto(
    client, fake_job, tmp_path: Path, monkeypatch
):
    """Metade das configurações é lida enquanto o trabalho roda: trocar no meio
    daria um resultado que não corresponde nem ao valor antigo nem ao novo."""

    from mrpack2curseforge import settings

    monkeypatch.setattr(settings, "env_path", lambda: tmp_path / ".env")

    # `fake_job` está aberto (done, mas não fechado)
    estado = client.get("/api/settings").json()
    assert estado["locked_by"] == fake_job.source.name

    for rota in ("/api/settings/reset", "/api/settings/forget-key"):
        assert client.post(rota).status_code == 409

    recusado = client.put("/api/settings", json={"values": {"M2CF_WORKERS": "9"}})
    assert recusado.status_code == 409
    assert "Feche-o antes" in recusado.json()["detail"]

    # fechado o trabalho, volta a funcionar
    client.post(f"/api/jobs/{fake_job.id}/close")

    assert client.get("/api/settings").json()["locked_by"] is None
    assert client.put(
        "/api/settings", json={"values": {"M2CF_WORKERS": "9"}}
    ).status_code == 200


def test_apagar_a_chave_pela_api_preserva_o_resto(
    client, tmp_path: Path, monkeypatch
):
    from mrpack2curseforge import settings

    env = tmp_path / ".env"
    monkeypatch.setattr(settings, "env_path", lambda: env)

    env.write_text(
        "CURSEFORGE_API_KEY=chave-1234\nM2CF_WORKERS=11\n", encoding="utf-8"
    )

    assert client.post("/api/settings/forget-key").json()["ok"] is True

    valores = settings.ler()
    assert "CURSEFORGE_API_KEY" not in valores
    assert valores["M2CF_WORKERS"] == "11"
