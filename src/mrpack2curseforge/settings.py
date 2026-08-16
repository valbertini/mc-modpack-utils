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
class Campo:
    """Uma configuração editável, do jeito que a interface precisa desenhar."""

    chave: str
    rotulo: str
    ajuda: str
    tipo: str  # "secret" | "texto" | "inteiro" | "decimal"
    padrao: Any = ""
    minimo: float | None = None
    maximo: float | None = None
    passo: float | None = None
    grupo: str = "geral"
    link: str = ""  # página onde o valor é obtido; vira "configurar ↗" na tela

    def normalizar(self, valor: Any) -> tuple[Any, str | None]:
        """Devolve `(valor, erro)`; erro != None significa recusar a gravação."""

        texto = str(valor if valor is not None else "").strip()

        if self.tipo in ("secret", "texto"):
            return texto, None

        if texto == "":
            return "", None

        try:
            numero = int(texto) if self.tipo == "inteiro" else float(texto)
        except ValueError:
            return None, f"{self.rotulo}: “{texto}” não é um número"

        if self.minimo is not None and numero < self.minimo:
            return None, f"{self.rotulo}: mínimo {self.minimo}"
        if self.maximo is not None and numero > self.maximo:
            return None, f"{self.rotulo}: máximo {self.maximo}"

        return numero, None


# A ordem aqui é a ordem na tela. Slider onde existe um intervalo com sentido;
# caixa de texto onde o valor é um caminho ou não tem limite natural.
#
# `M2CF_VERSION_THRESHOLD` fica **de fora** de propósito: é o número que separa
# "o mod existe, falta a versão" de "o mod não existe lá" (§4b), calibrado com
# dados reais. Mexer nele muda o diagnóstico de todos os mods, e um slider
# convida a mexer sem medir. Continua ajustável pelo `.env` à mão.
CAMPOS: list[Campo] = [
    Campo(
        "CURSEFORGE_API_KEY",
        "Chave da API do CurseForge",
        "Sem ela a conversão não roda. Gere uma no console e cole aqui.",
        "secret",
        grupo="acesso",
        # o `#/api-keys` é a rota da página de chaves; se ela mudar de nome, o
        # domínio sozinho ainda cai no console certo
        link="https://console.curseforge.com/#/api-keys",
    ),
    Campo(
        "M2CF_INPUT_DIR",
        "Pasta de entrada",
        "Onde os .mrpack são procurados. Vazio = input_modpacks/ do projeto.",
        "texto",
        grupo="pastas",
    ),
    Campo(
        "M2CF_OUTPUT_DIR",
        "Pasta de saída",
        "Onde os modpacks e registros são gravados.",
        "texto",
        grupo="pastas",
    ),
    Campo(
        "M2CF_CACHE",
        "Arquivo de cache",
        "Banco SQLite com as respostas das APIs.",
        "texto",
        grupo="pastas",
    ),
    Campo(
        "M2CF_WORKERS",
        "Mods em paralelo",
        "Mais rápido, porém mais requisições por segundo às APIs.",
        "inteiro",
        padrao=6,
        minimo=1,
        maximo=24,
        passo=1,
        grupo="desempenho",
    ),
    Campo(
        "M2CF_MAX_CANDIDATES",
        "Candidatos inspecionados",
        "Quantos projetos parecidos têm os arquivos abertos por mod.",
        "inteiro",
        padrao=8,
        minimo=1,
        maximo=20,
        passo=1,
        grupo="desempenho",
    ),
    Campo(
        "M2CF_SEARCH_PAGES",
        "Páginas de busca",
        "Profundidade da busca textual no CurseForge.",
        "inteiro",
        padrao=3,
        minimo=1,
        maximo=10,
        passo=1,
        grupo="desempenho",
    ),
    Campo(
        "M2CF_FILE_PAGES",
        "Páginas de arquivos",
        "Quanto do histórico de versões é varrido nos melhores candidatos.",
        "inteiro",
        padrao=20,
        minimo=1,
        maximo=50,
        passo=1,
        grupo="desempenho",
    ),
    Campo(
        "M2CF_RECENT_FILES",
        "Arquivos recentes comparados",
        "Quantos arquivos de cada lado entram na comparação do diagnóstico.",
        "inteiro",
        padrao=10,
        minimo=1,
        maximo=30,
        passo=1,
        grupo="diagnóstico",
    ),
    Campo(
        "M2CF_DIAGNOSIS_CANDIDATES",
        "Candidatos no diagnóstico",
        "Quantos projetos do CurseForge são investigados quando nada casa.",
        "inteiro",
        padrao=5,
        minimo=1,
        maximo=15,
        passo=1,
        grupo="diagnóstico",
    ),
    Campo(
        "M2CF_HTTP_TIMEOUT",
        "Tempo limite (s)",
        "Quanto esperar por cada resposta das APIs.",
        "inteiro",
        padrao=60,
        minimo=5,
        maximo=300,
        passo=5,
        grupo="rede",
    ),
    Campo(
        "M2CF_HTTP_RETRIES",
        "Tentativas",
        "Quantas vezes repetir uma requisição que falhou.",
        "inteiro",
        padrao=4,
        minimo=0,
        maximo=10,
        passo=1,
        grupo="rede",
    ),
]

POR_CHAVE = {campo.chave: campo for campo in CAMPOS}

SEGREDOS = {campo.chave for campo in CAMPOS if campo.tipo == "secret"}


def env_path() -> Path:
    """O `.env` que a interface edita: sempre o da raiz do projeto."""

    return PROJECT_ROOT / ".env"


def _linhas(caminho: Path) -> list[str]:
    if not caminho.is_file():
        return []
    return caminho.read_text(encoding="utf-8").splitlines()


def ler() -> dict[str, str]:
    """Valores que estão no `.env`, ignorando comentários e linhas soltas."""

    valores: dict[str, str] = {}

    for linha in _linhas(env_path()):
        limpa = linha.strip()
        if not limpa or limpa.startswith("#") or "=" not in limpa:
            continue

        chave, _, valor = limpa.partition("=")
        valores[chave.strip()] = valor.strip().strip('"').strip("'")

    return valores


def mascarar(valor: str) -> str:
    """`abcdef…xyz` -> `••••••xyz`. O bastante para reconhecer, não para usar."""

    if not valor:
        return ""
    if len(valor) <= 4:
        return "•" * len(valor)

    return "•" * 8 + valor[-4:]


def estado() -> dict[str, Any]:
    """Tudo o que a tela de configurações precisa desenhar."""

    atuais = ler()

    return {
        "path": str(env_path()),
        "campos": [
            {
                "chave": campo.chave,
                "rotulo": campo.rotulo,
                "ajuda": campo.ajuda,
                "tipo": campo.tipo,
                "grupo": campo.grupo,
                "padrao": campo.padrao,
                "minimo": campo.minimo,
                "maximo": campo.maximo,
                "passo": campo.passo,
                "link": campo.link,
                # segredo nunca sai inteiro daqui
                "valor": (
                    mascarar(atuais.get(campo.chave, ""))
                    if campo.chave in SEGREDOS
                    else atuais.get(campo.chave, "")
                ),
                "definido": bool(atuais.get(campo.chave)),
            }
            for campo in CAMPOS
        ],
    }


def gravar(mudancas: dict[str, Any]) -> dict[str, Any]:
    """Aplica as mudanças no `.env`, preservando o que já estava lá.

    Valor vazio **remove** a chave do arquivo (volta ao default do código).
    Chaves desconhecidas são ignoradas em vez de gravadas: a tela não é um
    editor de texto livre.
    """

    limpos: dict[str, str] = {}
    erros: list[str] = []

    for chave, bruto in mudancas.items():
        campo = POR_CHAVE.get(chave)
        if campo is None:
            continue

        valor, erro = campo.normalizar(bruto)
        if erro:
            erros.append(erro)
            continue

        limpos[chave] = "" if valor == "" else str(valor)

    if erros:
        return {"ok": False, "erros": erros}

    caminho = env_path()
    linhas = _linhas(caminho)
    vistas: set[str] = set()
    saida: list[str] = []

    for linha in linhas:
        limpa = linha.strip()
        chave = limpa.partition("=")[0].strip().lstrip("#").strip()

        # comentário, linha em branco ou chave que não estamos editando
        if not limpa or "=" not in limpa or chave not in limpos:
            saida.append(linha)
            continue

        vistas.add(chave)
        valor = limpos[chave]

        # remover = comentar, para o usuário ver que a linha existiu
        saida.append(f"{chave}={valor}" if valor else f"# {chave}=")

    novas = [f"{c}={v}" for c, v in limpos.items() if v and c not in vistas]

    if novas:
        if saida and saida[-1].strip():
            saida.append("")
        saida.extend(novas)

    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text("\n".join(saida).rstrip("\n") + "\n", encoding="utf-8")

    aplicar(limpos)
    return {"ok": True, "path": str(caminho), "gravadas": sorted(limpos)}


def aplicar(valores: dict[str, str]) -> None:
    """Reflete as mudanças no `Config` em memória, sem reiniciar o servidor.

    Só os campos que o processo relê a cada uso valem na hora; pastas e cache
    são resolvidos na importação, então a interface avisa que precisa reiniciar.
    """

    for chave, valor in valores.items():
        if valor:
            os.environ[chave] = valor
        else:
            os.environ.pop(chave, None)

    if "CURSEFORGE_API_KEY" in valores:
        Config.CURSEFORGE_API_KEY = valores["CURSEFORGE_API_KEY"] or None

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

    for chave, (atributo, tipo, padrao) in numericos.items():
        if chave not in valores:
            continue

        bruto = valores[chave]

        try:
            setattr(Config, atributo, tipo(bruto) if bruto else padrao)
        except ValueError:
            setattr(Config, atributo, padrao)


# --------------------------------------------------------------------------- #
# Restaurar
# --------------------------------------------------------------------------- #
PRECISA_REINICIAR = {"M2CF_INPUT_DIR", "M2CF_OUTPUT_DIR", "M2CF_CACHE"}


def restaurar_padrao() -> dict[str, Any]:
    """Apaga do `.env` tudo o que a tela edita, **menos** os segredos.

    Perder a chave da API por clicar em "restaurar padrão" seria uma surpresa
    cara: apagá-la é outro botão (`apagar_segredos`).
    """

    alvos = [campo.chave for campo in CAMPOS if campo.chave not in SEGREDOS]
    return gravar({chave: "" for chave in alvos})


def apagar_segredos() -> dict[str, Any]:
    """Apaga só a chave da API — o resto das configurações fica como está."""

    return gravar({chave: "" for chave in SEGREDOS})
