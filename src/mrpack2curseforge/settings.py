"""Leitura e escrita do `.env` pela interface.

O arquivo continua sendo a fonte da verdade — a tela de configurações é só um
editor dele. Duas regras que valem para tudo aqui:

* **o que o usuário escreveu à mão não se perde**: comentários, ordem das linhas
  e chaves que este módulo não conhece são preservados;
* **a chave da API nunca sai daqui inteira** (`masked`): a interface recebe só
  os últimos caracteres, o bastante para você reconhecer qual chave está lá.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mrpack2curseforge.config import PROJECT_ROOT, Config


@dataclass(frozen=True)
class Field:
    """Uma configuração editável, do jeito que a interface precisa desenhar."""

    key: str
    label: str
    help: str
    type: str  # "secret" | "text" | "int" | "float"
    default: Any = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    group: str = "geral"
    link: str = ""  # página onde o valor é obtido; vira "configurar ↗" na tela

    def normalize(self, value: Any) -> tuple[Any, str | None]:
        """Devolve `(valor, erro)`; erro != None significa recusar a gravação."""

        text = str(value if value is not None else "").strip()

        if self.type in ("secret", "text"):
            return text, None

        if text == "":
            return "", None

        try:
            number = int(text) if self.type == "int" else float(text)
        except ValueError:
            return None, f"{self.label}: “{text}” não é um número"

        if self.minimum is not None and number < self.minimum:
            return None, f"{self.label}: mínimo {self.minimum}"
        if self.maximum is not None and number > self.maximum:
            return None, f"{self.label}: máximo {self.maximum}"

        return number, None


# A ordem aqui é a ordem na tela. Slider onde existe um intervalo com sentido;
# caixa de texto onde o valor é um caminho ou não tem limite natural.
#
# `M2CF_VERSION_THRESHOLD` fica **de fora** de propósito: é o número que separa
# "o mod existe, falta a versão" de "o mod não existe lá" (§4b), calibrado com
# dados reais. Mexer nele muda o diagnóstico de todos os mods, e um slider
# convida a mexer sem medir. Continua ajustável pelo `.env` à mão.
FIELDS: list[Field] = [
    Field(
        "CURSEFORGE_API_KEY",
        "Chave da API do CurseForge",
        "Sem ela a conversão não roda. Gere uma no console e cole aqui.",
        "secret",
        group="acesso",
        # o `#/api-keys` é a rota da página de chaves; se ela mudar de nome, o
        # domínio sozinho ainda cai no console certo
        link="https://console.curseforge.com/#/api-keys",
    ),
    Field(
        "M2CF_INPUT_DIR",
        "Pasta de entrada",
        "Onde os .mrpack são procurados. Vazio = input_modpacks/ do projeto.",
        "text",
        group="pastas",
    ),
    Field(
        "M2CF_OUTPUT_DIR",
        "Pasta de saída",
        "Onde os modpacks e registros são gravados.",
        "text",
        group="pastas",
    ),
    Field(
        "M2CF_CACHE",
        "Arquivo de cache",
        "Banco SQLite com as respostas das APIs.",
        "text",
        group="pastas",
    ),
    Field(
        "M2CF_WORKERS",
        "Mods em paralelo",
        "Mais rápido, porém mais requisições por segundo às APIs.",
        "int",
        default=6,
        minimum=1,
        maximum=24,
        step=1,
        group="desempenho",
    ),
    Field(
        "M2CF_MAX_CANDIDATES",
        "Candidatos inspecionados",
        "Quantos projetos parecidos têm os arquivos abertos por mod.",
        "int",
        default=8,
        minimum=1,
        maximum=20,
        step=1,
        group="desempenho",
    ),
    Field(
        "M2CF_SEARCH_PAGES",
        "Páginas de busca",
        "Profundidade da busca textual no CurseForge.",
        "int",
        default=3,
        minimum=1,
        maximum=10,
        step=1,
        group="desempenho",
    ),
    Field(
        "M2CF_FILE_PAGES",
        "Páginas de arquivos",
        "Quanto do histórico de versões é varrido nos melhores candidatos.",
        "int",
        default=20,
        minimum=1,
        maximum=50,
        step=1,
        group="desempenho",
    ),
    Field(
        "M2CF_RECENT_FILES",
        "Arquivos recentes comparados",
        "Quantos arquivos de cada lado entram na comparação do diagnóstico.",
        "int",
        default=10,
        minimum=1,
        maximum=30,
        step=1,
        group="diagnóstico",
    ),
    Field(
        "M2CF_DIAGNOSIS_CANDIDATES",
        "Candidatos no diagnóstico",
        "Quantos projetos do CurseForge são investigados quando nada casa.",
        "int",
        default=5,
        minimum=1,
        maximum=15,
        step=1,
        group="diagnóstico",
    ),
    Field(
        "M2CF_HTTP_TIMEOUT",
        "Tempo limite (s)",
        "Quanto esperar por cada resposta das APIs.",
        "int",
        default=60,
        minimum=5,
        maximum=300,
        step=5,
        group="rede",
    ),
    Field(
        "M2CF_HTTP_RETRIES",
        "Tentativas",
        "Quantas vezes repetir uma requisição que falhou.",
        "int",
        default=4,
        minimum=0,
        maximum=10,
        step=1,
        group="rede",
    ),
]

BY_KEY = {field.key: field for field in FIELDS}

SECRETS = {field.key for field in FIELDS if field.type == "secret"}


def env_path() -> Path:
    """O `.env` que a interface edita: sempre o da raiz do projeto."""

    return PROJECT_ROOT / ".env"


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def read() -> dict[str, str]:
    """Valores que estão no `.env`, ignorando comentários e linhas soltas."""

    values: dict[str, str] = {}

    for line in _lines(env_path()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")

    return values


def mask(value: str) -> str:
    """`abcdef…xyz` -> `••••••xyz`. O bastante para reconhecer, não para usar."""

    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)

    return "•" * 8 + value[-4:]


def state() -> dict[str, Any]:
    """Tudo o que a tela de configurações precisa desenhar."""

    current = read()

    return {
        "path": str(env_path()),
        "fields": [
            {
                "key": field.key,
                "label": field.label,
                "help": field.help,
                "type": field.type,
                "group": field.group,
                "default": field.default,
                "minimum": field.minimum,
                "maximum": field.maximum,
                "step": field.step,
                "link": field.link,
                # segredo nunca sai inteiro daqui
                "value": (
                    mask(current.get(field.key, ""))
                    if field.key in SECRETS
                    else current.get(field.key, "")
                ),
                "is_set": bool(current.get(field.key)),
            }
            for field in FIELDS
        ],
    }


def write(changes: dict[str, Any]) -> dict[str, Any]:
    """Aplica as mudanças no `.env`, preservando o que já estava lá.

    Valor vazio **remove** a chave do arquivo (volta ao default do código).
    Chaves desconhecidas são ignoradas em vez de gravadas: a tela não é um
    editor de texto livre.
    """

    clean: dict[str, str] = {}
    errors: list[str] = []

    for key, raw in changes.items():
        field = BY_KEY.get(key)
        if field is None:
            continue

        value, error = field.normalize(raw)
        if error:
            errors.append(error)
            continue

        clean[key] = "" if value == "" else str(value)

    if errors:
        return {"ok": False, "errors": errors}

    path = env_path()
    lines = _lines(path)
    seen: set[str] = set()
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip().lstrip("#").strip()

        # comentário, linha em branco ou chave que não estamos editando
        if not stripped or "=" not in stripped or key not in clean:
            out.append(line)
            continue

        seen.add(key)
        value = clean[key]

        # remover = comentar, para o usuário ver que a linha existiu
        out.append(f"{key}={value}" if value else f"# {key}=")

    new = [f"{c}={v}" for c, v in clean.items() if v and c not in seen]

    if new:
        if out and out[-1].strip():
            out.append("")
        out.extend(new)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")

    apply(clean)
    return {"ok": True, "path": str(path), "saved": sorted(clean)}


def apply(values: dict[str, str]) -> None:
    """Reflete as mudanças no `Config` em memória, sem reiniciar o servidor.

    Só os campos que o processo relê a cada uso valem na hora; pastas e cache
    são resolvidos na importação, então a interface avisa que precisa reiniciar.
    """

    for key, value in values.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)

    if "CURSEFORGE_API_KEY" in values:
        Config.CURSEFORGE_API_KEY = values["CURSEFORGE_API_KEY"] or None

    numericos = {
        "M2CF_WORKERS": ("WORKERS", int, 6),
        "M2CF_MAX_CANDIDATES": ("MAX_CANDIDATES", int, 8),
        "M2CF_SEARCH_PAGES": ("SEARCH_PAGES", int, 3),
        "M2CF_FILE_PAGES": ("FILE_PAGES", int, 20),
        "M2CF_RECENT_FILES": ("RECENT_FILES", int, 10),
        "M2CF_DIAGNOSIS_CANDIDATES": ("DIAGNOSIS_CANDIDATES", int, 5),
        "M2CF_VERSION_THRESHOLD": ("VERSION_THRESHOLD", float, 0.85),
        "M2CF_HTTP_TIMEOUT": ("HTTP_TIMEOUT", int, 60),
        "M2CF_HTTP_RETRIES": ("HTTP_RETRIES", int, 4),
    }

    for key, (atributo, type, default) in numericos.items():
        if key not in values:
            continue

        raw = values[key]

        try:
            setattr(Config, atributo, type(raw) if raw else default)
        except ValueError:
            setattr(Config, atributo, default)


# --------------------------------------------------------------------------- #
# Restaurar
# --------------------------------------------------------------------------- #
NEEDS_RESTART = {"M2CF_INPUT_DIR", "M2CF_OUTPUT_DIR", "M2CF_CACHE"}


def reset_defaults() -> dict[str, Any]:
    """Apaga do `.env` tudo o que a tela edita, **menos** os segredos.

    Perder a chave da API por clicar em "restaurar padrão" seria uma surpresa
    cara: apagá-la é outro botão (`apagar_segredos`).
    """

    targets = [field.key for field in FIELDS if field.key not in SECRETS]
    return write({key: "" for key in targets})


def forget_secrets() -> dict[str, Any]:
    """Apaga só a chave da API — o resto das configurações fica como está."""

    return write({key: "" for key in SECRETS})
