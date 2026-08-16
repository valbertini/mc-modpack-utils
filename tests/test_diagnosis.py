"""Testes do diagnóstico dos mods não encontrados (sem rede).

Diferencia "o mod não existe no CurseForge" de "existe, mas essa versão não".
"""

import pytest

from mrpack2curseforge.config import Config
from mrpack2curseforge.domain import MissingReason, ModrinthProject, PackFile
from mrpack2curseforge.services.matcher import CurseForgeMatcher, file_similarity

LOCAL = "litematica-fabric-26.2-0.28.2.jar"


class FakeCurseForge:
    def __init__(self, projects, files_by_id=None):
        self.projects = projects
        self.files_by_id = files_by_id or {}

    def search(self, query=None, slug=None, pages=None):
        term = (slug or query or "").lower()
        return [
            p for p in self.projects if term in p["name"].lower() or term in p["slug"]
        ]

    def get_files(self, mod_id, game_version=None, max_pages=None):
        return self.files_by_id.get(mod_id, [])


class FakeModrinth:
    def __init__(self, names):
        self.names = names
        self.calls = 0

    def recent_file_names(self, project_id, limit=10):
        self.calls += 1
        return self.names[:limit]


def mod(file_name=LOCAL):
    return PackFile(file_name=file_name, file_path=f"mods/{file_name}")


# --------------------------------------------------------------------- score
@pytest.mark.parametrize(
    "a, b",
    [
        (LOCAL, "litematica-fabric-26.2-0.28.3.jar"),
        ("minihud-fabric-26.2-0.40.3.jar", "minihud-fabric-26.2-0.40.2.jar"),
        ("fabric-api-0.154.0+26.2.jar", "fabric-api-0.115.0+1.21.4.jar"),
    ],
)
def test_same_mod_different_version_is_above_threshold(a, b):
    assert file_similarity(a, b) >= Config.VERSION_THRESHOLD


@pytest.mark.parametrize(
    "a, b",
    [
        ("sodium-fabric-0.9.0+mc26.2.jar", "sodium-extra-fabric-0.9.1+mc26.2.jar"),
        ("Axiom-5.5.0-for-MC26.2.jar", "AxiomPaperPlugin-1.5.30.jar"),
        ("appleskin-fabric-mc1.21-3.0.5.jar", "carpet-1.21-1.4.147.jar"),
    ],
)
def test_different_mods_stay_below_threshold(a, b):
    assert file_similarity(a, b) < Config.VERSION_THRESHOLD


def test_similarity_is_symmetric_and_bounded():
    assert file_similarity(LOCAL, LOCAL) == pytest.approx(1.0)
    assert file_similarity(LOCAL, "outra-coisa.jar") == pytest.approx(
        file_similarity("outra-coisa.jar", LOCAL)
    )


# ----------------------------------------------------------------- pipeline
def test_version_unavailable_when_curseforge_has_another_version():
    client = FakeCurseForge(
        projects=[
            {
                "id": 308892,
                "name": "Litematica",
                "slug": "litematica",
                "latestFiles": [
                    {
                        "id": 500,
                        "fileName": "litematica-fabric-26.2-0.28.3.jar",
                        "fileDate": "2026-05-01T00:00:00Z",
                    }
                ],
            }
        ]
    )

    matcher = CurseForgeMatcher(client, modrinth=FakeModrinth([LOCAL]))
    result = matcher.match(
        mod(), ModrinthProject(project_id="abc", slug="litematica", title="Litematica")
    )

    assert not result.matched
    assert result.diagnosis.reason is MissingReason.VERSION_UNAVAILABLE
    assert result.diagnosis.project_name == "Litematica"
    assert result.diagnosis.closest_file_name == "litematica-fabric-26.2-0.28.3.jar"
    assert result.diagnosis.similarity >= Config.VERSION_THRESHOLD
    assert result.diagnosis.curseforge_url.endswith("/litematica")


def test_recent_modrinth_files_are_used_as_references():
    """O arquivo local é antigo; a evidência vem de uma versão recente do Modrinth."""

    recent = "litematica-fabric-1.21.10-0.24.8.jar"

    client = FakeCurseForge(
        projects=[
            {
                "id": 1,
                "name": "Litematica",
                "slug": "litematica",
                "latestFiles": [
                    {"id": 9, "fileName": recent, "fileDate": "2026-01-01T00:00:00Z"}
                ],
            }
        ]
    )

    modrinth = FakeModrinth([recent, "litematica-fabric-26.2-0.28.3.jar"])
    matcher = CurseForgeMatcher(client, modrinth=modrinth)

    result = matcher.match(
        mod("litematica-fabric-1.16.5-0.0.0.jar"),
        ModrinthProject(project_id="abc", slug="litematica", title="Litematica"),
    )

    assert modrinth.calls == 1
    assert result.diagnosis.reason is MissingReason.VERSION_UNAVAILABLE
    assert result.diagnosis.matched_reference == recent
    assert result.diagnosis.similarity == pytest.approx(1.0)
    assert result.diagnosis.modrinth_files_checked == 3  # local + 2 do Modrinth


def test_not_on_curseforge_when_nothing_is_similar():
    client = FakeCurseForge(
        projects=[
            {
                "id": 2,
                "name": "Litematica Printer",
                "slug": "litematica-printer",
                "latestFiles": [
                    {
                        "id": 10,
                        "fileName": "carpet-1.21-1.4.147.jar",
                        "fileDate": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ]
    )

    matcher = CurseForgeMatcher(client, modrinth=FakeModrinth([LOCAL]))
    result = matcher.match(
        mod(), ModrinthProject(project_id="abc", slug="litematica", title="Litematica")
    )

    assert result.diagnosis.reason is MissingReason.NOT_ON_CURSEFORGE
    assert result.diagnosis.similarity < Config.VERSION_THRESHOLD


def test_no_candidates_at_all_means_not_on_curseforge():
    matcher = CurseForgeMatcher(FakeCurseForge(projects=[]))
    result = matcher.match(mod("mod-exclusivo-do-modrinth-1.0.0.jar"), None)

    assert result.diagnosis.reason is MissingReason.NOT_ON_CURSEFORGE
    assert result.diagnosis.similarity == 0.0
    assert result.diagnosis.project_id is None


def test_diagnosis_survives_modrinth_failure():
    class BrokenModrinth:
        def recent_file_names(self, project_id, limit=10):
            raise RuntimeError("API fora do ar")

    client = FakeCurseForge(
        projects=[
            {
                "id": 3,
                "name": "Litematica",
                "slug": "litematica",
                "latestFiles": [
                    {
                        "id": 11,
                        "fileName": "litematica-fabric-26.2-0.28.3.jar",
                        "fileDate": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ]
    )

    matcher = CurseForgeMatcher(client, modrinth=BrokenModrinth())
    result = matcher.match(mod(), ModrinthProject(project_id="abc", slug="litematica"))

    # cai para o nome do arquivo local e ainda assim diagnostica
    assert result.diagnosis.reason is MissingReason.VERSION_UNAVAILABLE
    assert result.diagnosis.modrinth_files_checked == 1


def test_matched_mods_have_no_diagnosis():
    client = FakeCurseForge(
        projects=[
            {
                "id": 4,
                "name": "Litematica",
                "slug": "litematica",
                "latestFiles": [{"id": 12, "fileName": LOCAL}],
            }
        ]
    )

    matcher = CurseForgeMatcher(client, modrinth=FakeModrinth([LOCAL]))
    result = matcher.match(mod(), ModrinthProject(project_id="abc", slug="litematica"))

    assert result.matched
    assert result.diagnosis is None
