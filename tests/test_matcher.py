"""Testes do matcher usando um cliente falso do CurseForge (sem rede)."""

from mrpack2curseforge.domain import MatchStrategy, ModrinthProject, PackFile
from mrpack2curseforge.services.matcher import CurseForgeMatcher

SODIUM_FILE = "sodium-fabric-0.9.0+mc26.2.jar"


class FakeCurseForge:
    """Simula a API: projetos com latestFiles e histórico completo."""

    def __init__(self, projects, files_by_id=None):
        self.projects = projects
        self.files_by_id = files_by_id or {}
        self.search_calls = []
        self.file_calls = []

    def search(self, query=None, slug=None, pages=None):
        """Imita a API: `slug` é lookup exato, `query` é busca textual."""

        self.search_calls.append(("slug" if slug else "query", slug or query))

        if slug:
            return [p for p in self.projects if p["slug"].lower() == slug.lower()]

        term = (query or "").lower()
        return [
            project
            for project in self.projects
            if term in project["name"].lower() or term in project["slug"].lower()
        ]

    def get_files(self, mod_id, game_version=None, max_pages=None):
        self.file_calls.append(mod_id)
        return self.files_by_id.get(mod_id, [])


def mod(file_name=SODIUM_FILE, path=None):
    return PackFile(file_name=file_name, file_path=path or f"mods/{file_name}")


def test_match_via_modrinth_slug_using_latest_files():
    client = FakeCurseForge(
        projects=[
            {
                "id": 394468,
                "name": "Sodium",
                "slug": "sodium",
                "latestFiles": [{"id": 111, "fileName": SODIUM_FILE}],
            }
        ]
    )

    matcher = CurseForgeMatcher(client)
    result = matcher.match(mod(), ModrinthProject(project_id="AANobbMI", slug="sodium"))

    assert result.matched
    assert (result.project_id, result.file_id) == (394468, 111)
    assert result.strategy is MatchStrategy.MODRINTH_SLUG
    assert client.file_calls == []  # latestFiles resolveu sem chamadas extras


def test_match_falls_back_to_full_file_history():
    client = FakeCurseForge(
        projects=[
            {
                "id": 394468,
                "name": "Sodium",
                "slug": "sodium",
                "latestFiles": [{"id": 999, "fileName": "sodium-fabric-0.6.0.jar"}],
            }
        ],
        files_by_id={394468: [{"id": 222, "fileName": SODIUM_FILE}]},
    )

    matcher = CurseForgeMatcher(client)
    result = matcher.match(mod(), ModrinthProject(project_id="x", slug="sodium"))

    assert result.matched
    assert result.file_id == 222
    assert client.file_calls  # precisou listar os arquivos do projeto


def test_no_file_with_same_name_means_unmatched():
    """Projeto certo pelo nome, mas sem o arquivo -> vai para overrides."""

    client = FakeCurseForge(
        projects=[
            {
                "id": 394468,
                "name": "Sodium",
                "slug": "sodium",
                "latestFiles": [{"id": 1, "fileName": "sodium-fabric-0.4.0.jar"}],
            }
        ],
        files_by_id={394468: [{"id": 2, "fileName": "sodium-fabric-0.5.0.jar"}]},
    )

    matcher = CurseForgeMatcher(client)
    result = matcher.match(mod(), ModrinthProject(project_id="x", slug="sodium"))

    assert not result.matched
    assert result.strategy is MatchStrategy.UNMATCHED


def test_falls_back_to_filename_regex_when_modrinth_is_unknown():
    client = FakeCurseForge(
        projects=[
            {
                "id": 42,
                "name": "MiniHUD",
                "slug": "minihud",
                "latestFiles": [
                    {"id": 7, "fileName": "minihud-fabric-26.2-0.40.3.jar"}
                ],
            }
        ]
    )

    matcher = CurseForgeMatcher(client)
    result = matcher.match(mod("minihud-fabric-26.2-0.40.3.jar"), None)

    assert result.matched
    assert result.strategy is MatchStrategy.FILENAME_REGEX


def test_prefers_exact_project_over_similar_fork():
    client = FakeCurseForge(
        projects=[
            {
                "id": 1,
                "name": "Sodium Extra",
                "slug": "sodium-extra",
                "latestFiles": [{"id": 10, "fileName": "sodium-extra-0.6.0.jar"}],
            },
            {
                "id": 2,
                "name": "Sodium",
                "slug": "sodium",
                "latestFiles": [{"id": 20, "fileName": SODIUM_FILE}],
            },
        ]
    )

    matcher = CurseForgeMatcher(client)
    result = matcher.match(mod(), ModrinthProject(project_id="x", slug="sodium"))

    assert (result.project_id, result.file_id) == (2, 20)


def test_bracketed_suffixes_do_not_hide_the_right_project():
    """"Better Combat [Fabric & Forge]" é o "Better Combat" que procuramos.

    Sem isso ele caía na 14ª posição do ranking (e nós só inspecionamos os 8
    primeiros candidatos), então o mod ia parar em overrides.
    """

    from mrpack2curseforge.services.matcher import clean_project_name, rank_projects

    assert clean_project_name("Better Combat [Fabric & Forge]") == "better combat"
    assert clean_project_name("Chunky (Fabric)") == "chunky"
    # nomes que já são o nome do mod continuam intactos
    assert clean_project_name("Fabric API") == "fabric api"
    assert clean_project_name("Sodium Extra") == "sodium extra"

    candidatos = [
        {"id": 1, "name": "Better Combat Mod", "slug": "better-combat-mod"},
        {"id": 2, "name": "New Better Combat", "slug": "new-better-combat"},
        {
            "id": 3,
            "name": "Better Combat [Fabric & Forge]",
            "slug": "better-combat-by-daedelus",
        },
    ]

    assert rank_projects(candidatos, "Better Combat")[0]["id"] == 3


def test_name_variants_cover_other_spellings():
    """"Extended AE" no Modrinth é "ExtendedAE" no CurseForge."""

    from mrpack2curseforge.services.matcher import name_variants

    assert name_variants("Extended AE")[0] == "ExtendedAE"
    assert "extended_ae" in name_variants("Extended AE")

    # nome de uma palavra só não gera variação (não há espaço para tirar)
    assert name_variants("Sodium") == []

    # com três palavras, junta um espaço por vez
    variantes = name_variants("Yet Another Config Lib")
    assert "YetAnotherConfigLib" in variantes
    assert "YetAnother Config Lib" in variantes


def test_slug_derived_from_a_variant_is_tried_first():
    """`Extended AE` no Modrinth vira o slug `extendedae` no CurseForge."""

    client = FakeCurseForge(
        projects=[
            {
                "id": 77,
                "name": "ExtendedAE",
                "slug": "extendedae",
                "latestFiles": [
                    {"id": 5, "fileName": "ExtendedAE-1.20-1.0.2-fabric.jar"}
                ],
            }
        ]
    )

    matcher = CurseForgeMatcher(client)
    result = matcher.match(
        mod("ExtendedAE-1.20-1.0.2-fabric.jar"),
        ModrinthProject(project_id="x", slug="extended-ae", title="Extended AE"),
    )

    assert result.matched
    assert result.strategy is MatchStrategy.MODRINTH_SLUG
    # o slug do Modrinth não existe no CurseForge; o derivado da variação existe
    assert result.queries_tried[:2] == ["slug=extended-ae", "slug=extendedae"]


def test_variant_text_search_when_no_slug_matches():
    client = FakeCurseForge(
        projects=[
            {
                "id": 78,
                "name": "ExtendedAE",
                "slug": "extended-ae-reworked-by-someone",
                "latestFiles": [
                    {"id": 6, "fileName": "ExtendedAE-1.20-1.0.2-fabric.jar"}
                ],
            }
        ]
    )

    matcher = CurseForgeMatcher(client)
    result = matcher.match(
        mod("ExtendedAE-1.20-1.0.2-fabric.jar"),
        ModrinthProject(project_id="x", slug="extended-ae", title="Extended AE"),
    )

    assert result.matched
    assert result.strategy is MatchStrategy.MODRINTH_VARIANT


def test_the_pack_loader_is_added_as_a_last_name_query():
    """"Things" devolve resultado demais; "Things fabric" desempata."""

    client = FakeCurseForge(projects=[])
    matcher = CurseForgeMatcher(client, loader="fabric")

    matcher.match(
        mod("things-0.3.3+1.20.jar"),
        ModrinthProject(project_id="x", slug="things", title="Things"),
    )

    consultas = [termo for tipo, termo in client.search_calls if tipo == "query"]

    assert "Things fabric" in consultas
    # o nome puro é tentado antes da versão com loader
    assert consultas.index("Things") < consultas.index("Things fabric")


def test_disabled_files_still_match():
    client = FakeCurseForge(
        projects=[
            {
                "id": 3,
                "name": "Sodium",
                "slug": "sodium",
                "latestFiles": [{"id": 30, "fileName": SODIUM_FILE}],
            }
        ]
    )

    matcher = CurseForgeMatcher(client)
    result = matcher.match(mod(SODIUM_FILE + ".disabled"), None)

    assert result.matched
