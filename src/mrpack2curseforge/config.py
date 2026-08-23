"""Configuração da aplicação (paths, credenciais e limites)."""

import os
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent  # src/mrpack2curseforge -> src -> raiz

# Procura o .env em vários lugares. O primeiro encontrado vence (override=False).
for _candidate in (
    Path.cwd() / ".env",
    PROJECT_ROOT / ".env",
    PACKAGE_DIR / ".env",
):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)


def _env_path(var: str, default: Path) -> Path:
    raw = os.getenv(var)
    return Path(raw).expanduser().resolve() if raw else default


def _env_int(var: str, default: int) -> int:
    raw = os.getenv(var)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(var: str, default: float) -> float:
    raw = os.getenv(var)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


class Config:
    """Configuração lida do ambiente, com defaults sensatos."""

    CURSEFORGE_API_KEY: str | None = os.getenv("CURSEFORGE_API_KEY")

    INPUT_DIR: Path = _env_path("M2CF_INPUT_DIR", PROJECT_ROOT / "input_modpacks")
    OUTPUT_DIR: Path = _env_path("M2CF_OUTPUT_DIR", PROJECT_ROOT / "output_modpacks")

    # Área do ferramental de teste (`tools/capture_job.py` e afins). Não é
    # configuração de uso: a interface nunca aponta para cá, e é justamente por
    # isso que dá para mexer aqui sem risco de sujar as pastas de verdade. As
    # pastas são criadas por quem as usa, não na importação.
    TEST_INPUT_DIR: Path = _env_path(
        "M2CF_TEST_INPUT_DIR", PROJECT_ROOT / "test_modpacks" / "input"
    )
    TEST_OUTPUT_DIR: Path = _env_path(
        "M2CF_TEST_OUTPUT_DIR", PROJECT_ROOT / "test_modpacks" / "output"
    )
    CACHE_PATH: Path = _env_path(
        "M2CF_CACHE", PROJECT_ROOT / ".cache" / "curseforge.sqlite3"
    )

    # Quantos mods são processados em paralelo.
    WORKERS: int = _env_int("M2CF_WORKERS", 6)

    # Quantos projetos candidatos têm os arquivos inspecionados por consulta.
    MAX_CANDIDATES: int = _env_int("M2CF_MAX_CANDIDATES", 8)

    # Limites de paginação da API do CurseForge.
    SEARCH_PAGES: int = _env_int("M2CF_SEARCH_PAGES", 3)
    FILE_PAGES: int = _env_int("M2CF_FILE_PAGES", 20)

    # --- diagnóstico dos mods não encontrados ---------------------------- #
    # Quantos arquivos recentes de cada lado entram na comparação.
    RECENT_FILES: int = _env_int("M2CF_RECENT_FILES", 10)

    # Quantos candidatos do CurseForge são investigados no diagnóstico.
    DIAGNOSIS_CANDIDATES: int = _env_int("M2CF_DIAGNOSIS_CANDIDATES", 5)

    # Similaridade mínima (0..1) para concluir "o mod existe, a versão é que não".
    VERSION_THRESHOLD: float = _env_float("M2CF_VERSION_THRESHOLD", 0.85)

    HTTP_TIMEOUT: int = _env_int("M2CF_HTTP_TIMEOUT", 60)
    HTTP_RETRIES: int = _env_int("M2CF_HTTP_RETRIES", 4)

    @classmethod
    def require_api_key(cls) -> str:
        if not cls.CURSEFORGE_API_KEY:
            raise RuntimeError(
                "CURSEFORGE_API_KEY não configurada.\n"
                "Crie um arquivo .env na raiz do projeto com:\n"
                "  CURSEFORGE_API_KEY=sua_chave\n"
                "Chaves podem ser obtidas em https://console.curseforge.com/"
            )
        return cls.CURSEFORGE_API_KEY
