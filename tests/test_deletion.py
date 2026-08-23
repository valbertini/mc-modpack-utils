"""Apagar o que está guardado, um item por vez.

Isto aqui apaga arquivo do usuário: os testes existem tanto para provar que o
que devia sumir sumiu quanto para provar que o resto ficou.

Não há mais faxina de pasta inteira — o botão existia e foi retirado: um clique
errado nele levava horas de trabalho, e o ✕ de cada card faz o mesmo serviço
sem esse risco.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mrpack2curseforge.web.server import create_app


def povoar(entrada: Path, saida: Path) -> None:
    """Uma pasta com um pouco de tudo: o que sai, o que fica e o que engana."""

    entrada.mkdir(parents=True, exist_ok=True)
    saida.mkdir(parents=True, exist_ok=True)
    (saida / "conversions").mkdir(exist_ok=True)

    (entrada / "a.mrpack").write_bytes(b"x" * 1024)
    (entrada / "b.mrpack").write_bytes(b"y" * 2048)
    (entrada / ".gitkeep").write_text("")
    (entrada / "README.md").write_text("não é modpack")

    (saida / "a-[convertido].zip").write_bytes(b"z" * 4096)
    (saida / ".gitkeep").write_text("")


@pytest.fixture
def app_dirs(tmp_path: Path):
    entrada, saida = tmp_path / "in", tmp_path / "out"
    povoar(entrada, saida)

    app = create_app(input_dir=entrada, output_dir=saida)

    with TestClient(app) as client:
        yield client, entrada, saida, tmp_path


def test_deleting_one_pack_leaves_the_others(app_dirs):
    client, entrada, _, _ = app_dirs

    resposta = client.delete("/api/packs/a.mrpack")
    assert resposta.status_code == 200
    assert resposta.json()["deleted"] is True

    assert not (entrada / "a.mrpack").exists()
    assert (entrada / "b.mrpack").is_file()

    assert client.delete("/api/packs/a.mrpack").status_code == 404
    # o nome vem da URL: não pode escapar da pasta
    assert client.delete("/api/packs/..%2F..%2Fsegredo.mrpack").status_code == 404


def test_the_pack_of_an_open_job_cannot_be_deleted(app_dirs, monkeypatch):
    client, entrada, _, _ = app_dirs
    ctx = client.app.state.ctx

    class JobFalso:
        source = entrada / "a.mrpack"

    monkeypatch.setattr(
        ctx.jobs, "current", lambda kind=None: JobFalso() if kind == "update" else None
    )

    resposta = client.delete("/api/packs/a.mrpack")
    assert resposta.status_code == 409
    assert (entrada / "a.mrpack").is_file()


def test_deleting_a_record_takes_the_zip_with_it(app_dirs):
    client, _, saida, _ = app_dirs

    (saida / "conversions" / "meu.json").write_text(
        json.dumps({"id": "meu", "zip_name": "meu.zip", "updated_at": 1}),
        encoding="utf-8",
    )
    zip_path = saida / "meu.zip"
    zip_path.write_bytes(b"k" * 3000)

    apagado = client.delete("/api/records/meu").json()

    assert apagado["deleted"] is True
    assert not zip_path.exists(), "o .zip órfão ficava ocupando disco em silêncio"
    assert not (saida / "conversions" / "meu.json").exists()


def test_there_is_no_route_that_empties_a_whole_folder(app_dirs):
    """A rota saiu junto com o botão: nada mais apaga a pasta de uma vez."""

    client, entrada, saida, _ = app_dirs

    assert client.delete("/api/storage/input").status_code == 404
    assert client.delete("/api/storage/output").status_code == 404

    assert (entrada / "a.mrpack").is_file()
    assert (saida / "a-[convertido].zip").is_file()
