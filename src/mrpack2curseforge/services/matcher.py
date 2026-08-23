"""Coração do projeto: descobrir o equivalente de cada mod no CurseForge.

Regra de ouro: **o match só é aceito quando o CurseForge oferece um arquivo com
exatamente o mesmo nome do `.jar` usado no modpack original.** O nome do projeto
serve apenas para *encontrar candidatos*; quem confirma é o arquivo.

Vale para mod, resourcepack e shader: o que muda entre eles é só o `classId` da
busca (a seção do site), tirado da pasta do arquivo no índice.

Ordem das tentativas (para cada mod):

    1. slug do Modrinth      -> lookup exato no CurseForge
    2. título do Modrinth    -> busca textual
    3. regex sobre o arquivo -> busca textual
    4. primeiro token        -> busca textual (último recurso)

Se nenhuma encontrar um arquivo com o mesmo nome, o mod é marcado como não
convertido e vai para `overrides/mods`.
"""

import re
from difflib import SequenceMatcher
from typing import Any

from mrpack2curseforge.config import Config
from mrpack2curseforge.constants import (
    CURSEFORGE_CLASS_MODS,
    CURSEFORGE_CLASSES,
    CURSEFORGE_SECTIONS,
    DEFAULT_SECTION,
)
from mrpack2curseforge.domain import (
    Diagnosis,
    MatchResult,
    MatchStrategy,
    MissingReason,
    ModrinthProject,
    PackFile,
)
from mrpack2curseforge.services.curseforge import CurseForgeClient
from mrpack2curseforge.services.modrinth import ModrinthClient

# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

LOADER_TOKENS = ("neoforge", "forge", "fabric", "quilt", "iris", "mc")

# O CurseForge mistura o loader dentro de `gameVersions` ("Fabric", "NeoForge").
# Que loaders servem para um pack de cada tipo: o Quilt roda mod de Fabric, o
# contrário não vale, e os outros três só aceitam a si mesmos.
LOADER_ACCEPTS = {
    "fabric": ("fabric",),
    "quilt": ("quilt", "fabric"),
    "forge": ("forge",),
    "neoforge": ("neoforge",),
}


def file_loaders(file: dict[str, Any]) -> set[str]:
    """Loaders que o arquivo do CurseForge declara. Vazio = não declarou nenhum.

    Arquivo antigo costuma vir sem essa marcação, e resourcepack nunca a tem —
    por isso "não declarou" é um caso à parte de "declarou outro".
    """

    tags = {str(tag).lower() for tag in (file.get("gameVersions") or [])}
    return tags & set(LOADER_ACCEPTS)


# Simplificações para famílias de mods cujo nome de arquivo é muito ruidoso.
SIMPLE_HINTS = (
    ("sodium", "sodium"),
    ("carpet", "carpet"),
    ("xaero", "xaero"),
    ("fabric-api", "fabric api"),
    ("fabric_api", "fabric api"),
)


def strip_extension(name: str) -> str:
    name = name.removesuffix(".disabled")
    return re.sub(r"\.(jar|zip)$", "", name, flags=re.IGNORECASE)


def normalize_file_name(name: str) -> str:
    """Forma canônica para comparar nomes de arquivos entre plataformas."""

    name = strip_extension(name).lower()
    name = name.replace("%2b", "+").replace("%20", " ")
    return re.sub(r"[\s_]+", " ", name).strip()


def normalize_mod_name(file_name: str) -> str:
    """Transforma o nome do arquivo numa consulta de busca.

    ImmediatelyFast-Fabric-1.16.1+1.21.jar -> "immediately fast"
    """

    name = strip_extension(file_name)

    # separa CamelCase antes de baixar para minúsculas. Só depois de letra: a
    # fronteira dígito→maiúscula parte "3D Default" em "3 D", e os dois pedaços
    # somem no filtro de tokens logo abaixo
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    name = name.lower()

    # versões e canais de release
    name = re.sub(r"\bv(?=\d)", "", name)
    name = re.sub(r"\b(alpha|beta|release|rc|snapshot|build)\b\.?\d*", " ", name)
    name = re.sub(r"mc\d+(\.\d+)*", " ", name)
    name = re.sub(r"\d+(\.\d+)+", " ", name)

    # loaders
    for token in LOADER_TOKENS:
        name = re.sub(rf"\b{token}\b", " ", name)

    # símbolos viram espaço
    name = re.sub(r"[^a-z0-9]+", " ", name)

    tokens = [t for t in name.split() if len(t) > 1 and not t.isdigit()]
    return " ".join(tokens)


def simple_mod_name(file_name: str) -> str:
    """Consulta de último recurso: primeiro token relevante."""

    lowered = strip_extension(file_name).lower()

    for hint, value in SIMPLE_HINTS:
        if hint in lowered:
            return value

    normalized = normalize_mod_name(file_name)
    tokens = normalized.split()
    return tokens[0] if tokens else normalized


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def symmetric_similarity(a: str, b: str) -> float:
    """`SequenceMatcher` não é simétrico; a média das duas ordens é.

    Sem isso o diagnóstico dependeria de qual nome entra como primeiro argumento
    (a diferença chega a 0.10 em nomes curtos).
    """

    return (similarity(a, b) + similarity(b, a)) / 2


def file_similarity(a: str, b: str) -> float:
    """Semelhança entre dois nomes de arquivo `.jar` (0..1).

    Combina a comparação direta dos nomes completos com a comparação dos nomes
    sem versão/loader. A segunda metade é o que separa "mesma família de mods,
    versão diferente" (`litematica-...-0.28.2` x `litematica-...-0.28.3`) de
    "mods parecidos, mas diferentes" (`sodium-...` x `sodium-extra-...`).
    """

    full = symmetric_similarity(normalize_file_name(a), normalize_file_name(b))

    stem_a = normalize_mod_name(a)
    stem_b = normalize_mod_name(b)

    if not stem_a or not stem_b:
        return full

    stem = 1.0 if stem_a == stem_b else symmetric_similarity(stem_a, stem_b)

    return (full + stem) / 2


# "Better Combat [Fabric & Forge]" e "Chunky (Fabric)" são o mesmo mod que
# "Better Combat" e "Chunky" — o sufixo só atrapalha a comparação
BRACKETED = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
TRAILING_LOADER = re.compile(
    r"\s*[-–—:]\s*(fabric|forge|neoforge|quilt)"
    r"(\s*(&|\+|/|e|and)\s*(fabric|forge|neoforge|quilt))*\s*$",
    re.IGNORECASE,
)


def clean_project_name(name: str | None) -> str:
    """Nome do projeto sem sufixos decorativos, para comparação."""

    cleaned = BRACKETED.sub(" ", name or "")
    cleaned = TRAILING_LOADER.sub("", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned.lower())

    return " ".join(cleaned.split())


def slugify(text: str | None) -> str:
    """`Essential Mod` -> `essential-mod` (o formato de slug do CurseForge)."""

    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def split_words(base: str | None) -> list[str]:
    """Quebra o nome em palavras, entendendo CamelCase e snake_case.

    `VitalityFix` -> ["Vitality", "Fix"] · `extended_ae` -> ["extended", "ae"]
    """

    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", base or "")
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)

    return [word for word in re.split(r"[\s\-_]+", spaced) if word]


def name_variants(base: str | None) -> list[str]:
    """Outras grafias do mesmo nome, nos dois sentidos.

    `Extended AE` -> `ExtendedAE` · `VitalityFix` -> `Vitality Fix`

    A busca do CurseForge é sensível a isso: `Extended AE` devolve "Extended
    Slabs", "Extended Food"… e nunca o projeto certo, cadastrado como `ExtendedAE`.
    """

    words = split_words(base)

    if len(words) < 2:
        return []

    variants = [
        "".join(words),                                  # ExtendedAE
        " ".join(words),                                 # Vitality Fix
        "_".join(word.lower() for word in words),        # extended_ae
    ]

    # junta um espaço por vez (útil em nomes de três ou mais palavras)
    for index in range(len(words) - 1):
        merged = words[:index] + [words[index] + words[index + 1]] + words[index + 2 :]
        variants.append(" ".join(merged))

    original = (base or "").strip().lower()
    unique: list[str] = []
    vistos = {original}

    for variant in variants:
        lowered = variant.lower()
        if variant and lowered not in vistos:
            vistos.add(lowered)
            unique.append(variant)

    return unique[:4]


def _first_author(project: dict[str, Any]) -> str | None:
    """Autor principal do projeto — o `modlist.html` do CurseForge mostra ele."""

    for author in project.get("authors") or []:
        if author.get("name"):
            return author["name"]

    return None


def rank_projects(
    candidates: list[dict[str, Any]], reference: str
) -> list[dict[str, Any]]:
    """Ordena projetos do CurseForge por semelhança com `reference`.

    A busca da API do CurseForge devolve resultados fracamente ordenados (uma
    consulta por "Just Enough Items" pode trazer plugins aleatórios antes do
    projeto certo). Usada tanto pelo matcher quanto pela busca da interface web.
    """

    reference = reference.lower().strip()
    clean_reference = clean_project_name(reference)
    candidates = [c for c in candidates if c.get("id")]

    def score(mod: dict[str, Any]) -> float:
        name = (mod.get("name") or "").lower()
        slug = (mod.get("slug") or "").lower()

        value = max(similarity(reference, name), similarity(reference, slug))

        if reference in (name, slug):
            value += 1.0
        elif clean_reference and clean_reference == clean_project_name(name):
            # "Better Combat [Fabric & Forge]" == "Better Combat"
            value += 1.0
        elif reference in name or reference in slug:
            value += 0.3

        # projetos populares tendem a ser o alvo correto
        value += min((mod.get("downloadCount") or 0) / 1e9, 0.05)

        return value

    return sorted(candidates, key=score, reverse=True)


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


class CurseForgeMatcher:
    def __init__(
        self,
        client: CurseForgeClient,
        minecraft_version: str | None = None,
        loader: str | None = None,
        modrinth: ModrinthClient | None = None,
    ):
        self.client = client
        self.minecraft_version = minecraft_version
        self.loader = loader
        self.modrinth = modrinth

    # ------------------------------------------------------------ público
    def match(
        self,
        mod: PackFile,
        modrinth: ModrinthProject | None = None,
        diagnose: bool = True,
    ) -> MatchResult:
        """Procura o arquivo no CurseForge.

        `diagnose=False` desliga a investigação do "por quê" quando ela não teria
        para quem servir — é o caso dos arquivos que já estavam em `overrides/`:
        não achar é o estado normal deles, e não vira conflito na tela.
        """

        result = MatchResult(mod=mod, modrinth=modrinth)

        target = normalize_file_name(mod.file_name)
        if not target:
            return result

        class_id = CURSEFORGE_CLASSES.get(mod.folder, CURSEFORGE_CLASS_MODS)
        seen_projects: set[int] = set()
        # candidatos vistos em todas as consultas, na ordem de relevância
        pool: dict[int, dict[str, Any]] = {}

        for strategy, query, use_slug in self._query_plan(mod, modrinth):
            if not query:
                continue

            label = f"{'slug=' if use_slug else ''}{query}"
            if label in result.queries_tried:
                continue
            result.queries_tried.append(label)

            candidates = self.client.search(
                slug=query if use_slug else None,
                query=None if use_slug else query,
                class_id=class_id,
            )

            ranked = self._rank(candidates, query)

            for candidate in ranked[: Config.DIAGNOSIS_CANDIDATES]:
                pool.setdefault(candidate["id"], candidate)

            hit = self._find_file_in_candidates(
                ranked=ranked,
                target=target,
                seen_projects=seen_projects,
            )

            if hit:
                project, file = hit
                result.strategy = strategy
                result.project_id = project["id"]
                result.file_id = file["id"]
                result.project_name = project.get("name")
                result.project_slug = project.get("slug")
                result.project_author = _first_author(project)
                return result

        # nada é logado por mod: a busca roda em paralelo e sairia fora de ordem.
        # O resumo organizado é impresso de uma vez em `Converter._log_analysis`.
        if diagnose:
            result.diagnosis = self.diagnose(mod, modrinth, list(pool.values()))

        return result

    # ---------------------------------------------------------- diagnóstico
    def diagnose(
        self,
        mod: PackFile,
        modrinth: ModrinthProject | None,
        candidates: list[dict[str, Any]],
    ) -> Diagnosis:
        """Descobre *por que* o mod não foi convertido.

        Pega os N arquivos mais recentes do projeto no Modrinth e compara,
        diretamente, com os arquivos mais recentes de cada candidato do
        CurseForge. Se a maior similaridade passar do limiar, o projeto existe
        no CurseForge e o que falta é apenas aquela versão específica.
        """

        references = self._modrinth_reference_names(mod, modrinth)

        best = Diagnosis(
            reason=MissingReason.NOT_ON_CURSEFORGE,
            modrinth_files_checked=len(references),
            section=CURSEFORGE_SECTIONS.get(mod.folder, DEFAULT_SECTION),
        )

        for candidate in candidates[: Config.DIAGNOSIS_CANDIDATES]:
            for remote in self._recent_curseforge_files(candidate):
                remote_name = remote.get("fileName") or ""
                if not remote_name:
                    continue

                for reference in references:
                    score = file_similarity(reference, remote_name)

                    if score > best.similarity:
                        best.similarity = score
                        best.project_id = candidate.get("id")
                        best.project_name = candidate.get("name")
                        best.project_slug = candidate.get("slug")
                        best.closest_file_id = remote.get("id")
                        best.closest_file_name = remote_name
                        best.matched_reference = reference

        if best.similarity >= Config.VERSION_THRESHOLD:
            best.reason = MissingReason.VERSION_UNAVAILABLE
        else:
            # abaixo do limiar o "melhor candidato" não significa nada
            best.reason = MissingReason.NOT_ON_CURSEFORGE

        return best

    def _modrinth_reference_names(
        self, mod: PackFile, modrinth: ModrinthProject | None
    ) -> list[str]:
        """Arquivo local + os arquivos mais recentes do projeto no Modrinth."""

        names = [mod.clean_file_name]

        if self.modrinth and modrinth and modrinth.project_id:
            try:
                recent = self.modrinth.recent_file_names(
                    modrinth.project_id, Config.RECENT_FILES
                )
            except Exception:  # noqa: BLE001 - diagnóstico nunca derruba a conversão
                recent = []

            for name in recent:
                if name not in names:
                    names.append(name)

        return names

    def _recent_curseforge_files(
        self, candidate: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Arquivos mais recentes de um projeto do CurseForge."""

        files: dict[int, dict[str, Any]] = {}

        for file in candidate.get("latestFiles") or []:
            if file.get("id"):
                files[file["id"]] = file

        try:
            for file in self.client.get_files(candidate["id"], max_pages=1):
                if file.get("id"):
                    files[file["id"]] = file
        except Exception:  # noqa: BLE001
            pass

        ordered = sorted(
            files.values(),
            key=lambda f: f.get("fileDate") or "",
            reverse=True,
        )

        return ordered[: Config.RECENT_FILES]

    # ------------------------------------------------------------- consultas
    def _query_plan(
        self, mod: PackFile, modrinth: ModrinthProject | None
    ) -> list[tuple[MatchStrategy, str | None, bool]]:
        plan: list[tuple[MatchStrategy, str | None, bool]] = []

        base = ""
        variants: list[str] = []

        if modrinth:
            base = modrinth.title or (modrinth.slug or "").replace("-", " ")
            variants = name_variants(base)

        # 1. lookups por slug: exatos e baratos (0 ou 1 resultado), então vêm antes
        #    de qualquer busca textual. O slug do CurseForge costuma ser o título
        #    slugificado — é assim que o "Essential Mod" (slug `essential` no
        #    Modrinth, `essential-mod` no CurseForge) é encontrado.
        slugs: list[str] = []

        for candidate in [modrinth.slug if modrinth else None, base, *variants]:
            slug = slugify(candidate)
            if slug and slug not in slugs:
                slugs.append(slug)

        # "3D Default" é `3d-default` no Modrinth e `minecraft-3d-default` no
        # CurseForge, e a busca textual por texture pack nunca devolve o projeto
        # (150 resultados sem ele). O lookup por slug é 0-ou-1: sai barato tentar
        if not mod.is_mod:
            slugs += [f"minecraft-{slug}" for slug in list(slugs)]

        for slug in slugs:
            plan.append((MatchStrategy.MODRINTH_SLUG, slug, True))

        # 2. busca textual pelo nome e por outras grafias dele
        if base:
            plan.append((MatchStrategy.MODRINTH_TITLE, base, False))

        for variant in variants:
            plan.append((MatchStrategy.MODRINTH_VARIANT, variant, False))

        # 3. o loader costuma desempatar buscas genéricas ("Things" -> 100
        #    resultados; "Things fabric" -> um punhado). Só vale para mods:
        #    resourcepack e shader não têm loader, e o termo só atrapalharia
        if self.loader and mod.is_mod:
            for name in filter(None, [base, normalize_mod_name(mod.file_name)]):
                plan.append(
                    (MatchStrategy.MODRINTH_LOADER, f"{name} {self.loader}", False)
                )

        regex_query = normalize_mod_name(mod.file_name)
        plan.append((MatchStrategy.FILENAME_REGEX, regex_query, False))

        simple_query = simple_mod_name(mod.file_name)
        if simple_query and simple_query != regex_query:
            plan.append((MatchStrategy.FILENAME_SIMPLE, simple_query, False))

        return plan

    # -------------------------------------------------------------- ranking
    def _rank(
        self, candidates: list[dict[str, Any]], reference: str
    ) -> list[dict[str, Any]]:
        return rank_projects(candidates, reference)

    # -------------------------------------------------- verificação de arquivo
    def _find_file_in_candidates(
        self,
        ranked: list[dict[str, Any]],
        target: str,
        seen_projects: set[int],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        # achou o arquivo, mas ele é de outro loader: guarda e continua olhando
        reserva: tuple[dict[str, Any], dict[str, Any]] | None = None

        def considerar(
            candidate: dict[str, Any], file: dict[str, Any] | None
        ) -> tuple[dict[str, Any], dict[str, Any]] | None:
            nonlocal reserva

            if file is None:
                return None
            if self._loader_rank(file) < 3:
                return candidate, file

            reserva = reserva or (candidate, file)
            return None

        # 1. varredura grátis: os `latestFiles` já vieram na resposta da busca
        for candidate in ranked:
            hit = considerar(
                candidate, self._pick_file(candidate.get("latestFiles") or [], target)
            )
            if hit:
                return hit

        # 2. lista completa de arquivos dos melhores candidatos
        for position, candidate in enumerate(ranked[: Config.MAX_CANDIDATES]):
            mod_id = candidate["id"]

            if mod_id in seen_projects:
                continue
            seen_projects.add(mod_id)

            hit = considerar(
                candidate,
                self._pick_file(
                    self.client.get_files(
                        mod_id, game_version=self.minecraft_version, max_pages=2
                    ),
                    target,
                ),
            )
            if hit:
                return hit

            # só os 3 melhores candidatos justificam varrer todo o histórico
            if position < 3:
                hit = considerar(
                    candidate, self._pick_file(self.client.get_files(mod_id), target)
                )
                if hit:
                    return hit

        return reserva

    def _pick_file(
        self, files: list[dict[str, Any]], target: str
    ) -> dict[str, Any] | None:
        """O melhor arquivo com o nome procurado, dentro de um projeto.

        O nome do arquivo nem sempre identifica uma release. Um resourcepack
        publica as 40 versões como "Low Shield.zip"; e o Cloth Config publica o
        jar de Fabric e o de NeoForge **com o mesmo nome**, diferentes só pela
        marcação de loader. Então, entre os homônimos:

        1. quem serve ao loader do pack (`_loader_rank`);
        2. quem declara a versão do Minecraft do pack;
        3. o mais recente.
        """

        iguais = [f for f in files if self._same_file(f, target)]
        if not iguais:
            return None

        # dois `sort` estáveis: o segundo agrupa sem desfazer a ordem do primeiro
        iguais.sort(key=lambda f: f.get("fileDate") or "", reverse=True)
        iguais.sort(
            key=lambda f: (
                self._loader_rank(f),
                self.minecraft_version not in (f.get("gameVersions") or []),
            )
        )

        return iguais[0]

    def _loader_rank(self, file: dict[str, Any]) -> int:
        """Quatro degraus, do melhor para o pior:

        0. declara o loader do pack;
        1. declara outro que o pack aceita (Quilt roda mod de Fabric);
        2. não declara loader nenhum — arquivo antigo, ou resourcepack;
        3. declara só loader incompatível.

        O 3 **não** elimina, e essa foi uma lição medida: num pack Forge com
        Sinytra Connector, 6 mods de Fabric estão lá de propósito e o CurseForge
        publica exatamente aqueles jars. Recusá-los mandaria para `overrides/`
        um match certo. O 3 perde para qualquer alternativa e faz a busca
        continuar procurando (`_find_file_in_candidates`), mas serve de reserva.
        """

        declarados = file_loaders(file)
        if not declarados:
            return 2

        loader = (self.loader or "").lower()
        aceitos = LOADER_ACCEPTS.get(loader)
        if not aceitos:
            return 2

        if loader in declarados:
            return 0

        return 1 if declarados & set(aceitos) else 3

    def _same_file(self, file: dict[str, Any], target: str) -> bool:
        remote = file.get("fileName") or ""
        if not remote or not file.get("id"):
            return False
        return normalize_file_name(remote) == target
