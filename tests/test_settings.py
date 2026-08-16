"""Editor do `.env`: preservar o que é do usuário e nunca vazar a chave."""

from pathlib import Path

import pytest

from mrpack2curseforge import settings
from mrpack2curseforge.config import Config


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> Path:
    """Um `.env` isolado — nenhum teste toca o arquivo real do projeto."""

    caminho = tmp_path / ".env"
    monkeypatch.setattr(settings, "env_path", lambda: caminho)
    return caminho


def test_mascarar_nunca_devolve_a_chave(env):
    assert settings.mask("") == ""
    assert settings.mask("abc") == "•••"

    chave = "abcdefghijklmnop1234"
    mascarada = settings.mask(chave)

    assert mascarada.endswith("1234")
    assert chave[:8] not in mascarada
    assert len(mascarada) == 12


def test_estado_nao_expoe_o_segredo(env):
    env.write_text("CURSEFORGE_API_KEY=supersecretokey123\n", encoding="utf-8")

    campos = {c["key"]: c for c in settings.state()["fields"]}
    api = campos["CURSEFORGE_API_KEY"]

    assert api["is_set"] is True
    assert "supersecreto" not in api["value"]
    assert api["value"].endswith("y123")


def test_gravar_preserva_comentarios_e_chaves_alheias(env):
    env.write_text(
        "# um comentário meu\n"
        "CURSEFORGE_API_KEY=chave\n"
        "\n"
        "# outro bloco\n"
        "MINHA_VAR_PESSOAL=nao_mexa\n"
        "M2CF_WORKERS=6\n",
        encoding="utf-8",
    )

    settings.write({"M2CF_WORKERS": "12", "M2CF_HTTP_TIMEOUT": "90"})

    texto = env.read_text(encoding="utf-8")

    assert "# um comentário meu" in texto
    assert "# outro bloco" in texto
    assert "MINHA_VAR_PESSOAL=nao_mexa" in texto
    assert "CURSEFORGE_API_KEY=chave" in texto
    assert "M2CF_WORKERS=12" in texto
    # chave nova entra no fim
    assert "M2CF_HTTP_TIMEOUT=90" in texto


def test_gravar_ignora_chave_desconhecida(env):
    settings.write({"ALGO_INVENTADO": "x", "M2CF_WORKERS": "8"})

    texto = env.read_text(encoding="utf-8")

    assert "ALGO_INVENTADO" not in texto
    assert "M2CF_WORKERS=8" in texto


def test_valor_vazio_comenta_a_linha(env):
    env.write_text("M2CF_WORKERS=12\n", encoding="utf-8")

    settings.write({"M2CF_WORKERS": ""})
    texto = env.read_text(encoding="utf-8")

    # comentada, não apagada: você vê que a linha existiu
    assert "# M2CF_WORKERS=" in texto
    assert settings.read().get("M2CF_WORKERS") is None


def test_numero_invalido_nao_grava_nada(env):
    env.write_text("M2CF_WORKERS=6\n", encoding="utf-8")

    resultado = settings.write({"M2CF_WORKERS": "muitos", "M2CF_HTTP_TIMEOUT": "30"})

    assert resultado["ok"] is False
    assert "não é um número" in resultado["errors"][0]
    # nem o campo válido do mesmo lote foi gravado
    assert env.read_text(encoding="utf-8") == "M2CF_WORKERS=6\n"


def test_limites_sao_respeitados(env):
    assert settings.write({"M2CF_WORKERS": "999"})["ok"] is False
    assert settings.write({"M2CF_WORKERS": "0"})["ok"] is False
    assert settings.write({"M2CF_WORKERS": "12"})["ok"] is True


def test_limiar_de_similaridade_fica_fora_da_tela(env):
    """É o número que decide o diagnóstico de todo mod (§4b): não vai em slider."""

    assert "M2CF_VERSION_THRESHOLD" not in settings.BY_KEY

    # e a tela não o grava nem se alguém mandar
    settings.write({"M2CF_VERSION_THRESHOLD": "0.1"})
    assert "M2CF_VERSION_THRESHOLD" not in env.read_text(encoding="utf-8")


def test_gravar_reflete_no_config_em_memoria(env, monkeypatch):
    monkeypatch.setattr(Config, "WORKERS", 6)
    monkeypatch.setattr(Config, "HTTP_TIMEOUT", 60)

    settings.write({"M2CF_WORKERS": "10", "M2CF_HTTP_TIMEOUT": "90"})

    assert Config.WORKERS == 10
    assert Config.HTTP_TIMEOUT == 90


def test_restaurar_padrao_preserva_a_chave(env):
    env.write_text(
        "CURSEFORGE_API_KEY=minha-chave\nM2CF_WORKERS=12\nM2CF_FILE_PAGES=40\n",
        encoding="utf-8",
    )

    settings.reset_defaults()
    valores = settings.read()

    assert valores["CURSEFORGE_API_KEY"] == "minha-chave"
    assert "M2CF_WORKERS" not in valores
    assert "M2CF_FILE_PAGES" not in valores


def test_apagar_a_chave_nao_mexe_no_resto(env):
    """Apagar a chave não pode desfazer o que você acabou de configurar."""

    env.write_text(
        "CURSEFORGE_API_KEY=minha-chave\nM2CF_WORKERS=12\nM2CF_HTTP_TIMEOUT=90\n",
        encoding="utf-8",
    )

    settings.forget_secrets()
    valores = settings.read()

    assert "CURSEFORGE_API_KEY" not in valores
    assert valores["M2CF_WORKERS"] == "12"
    assert valores["M2CF_HTTP_TIMEOUT"] == "90"


def test_env_inexistente_e_criado(env):
    assert not env.exists()

    settings.write({"M2CF_WORKERS": "7"})

    assert env.is_file()
    assert settings.read()["M2CF_WORKERS"] == "7"


def test_a_chave_traz_o_link_do_console(env):
    """Sem link a tela não teria como mostrar onde gerar a chave."""

    campos = {campo["key"]: campo for campo in settings.state()["fields"]}

    assert campos["CURSEFORGE_API_KEY"]["link"].startswith(
        "https://console.curseforge.com"
    )
    # o resto não inventa link: só faz sentido onde há uma página para abrir
    assert [c["key"] for c in campos.values() if c["link"]] == [
        "CURSEFORGE_API_KEY"
    ]
