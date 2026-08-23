"""Qual dos arquivos homônimos serve ao loader do pack.

O CurseForge publica o mesmo `cloth-config-26.2.155.jar` duas vezes — um de
Fabric, um de NeoForge — e a diferença mora só na marcação dentro de
`gameVersions`. Enquanto o loader não entrou na conta, qual deles vencia era
acidente da ordem em que a API devolvia os arquivos.
"""

from mrpack2curseforge.domain import PackFile
from mrpack2curseforge.services.matcher import CurseForgeMatcher, file_loaders

NOME = "cloth-config-26.2.155.jar"


class FakeCurseForge:
    """Separa o que a busca já traz (`latestFiles`) do que custa uma listagem."""

    def __init__(self, projects, files_by_id=None):
        self.projects = projects
        self.files_by_id = files_by_id or {}
        self.listagens = 0

    def search(self, query=None, slug=None, pages=None, class_id=None):
        return self.projects

    def get_files(self, mod_id, game_version=None, max_pages=None):
        self.listagens += 1
        return self.files_by_id.get(mod_id, [])


def arquivo_cf(file_id, tags, name=NOME, date="2026-06-18"):
    return {"id": file_id, "fileName": name, "fileDate": date, "gameVersions": tags}


def mod(name=NOME):
    return PackFile(file_name=name, file_path=f"mods/{name}")


# ------------------------------------------------------------------- leitura
def test_file_loaders_reads_the_tags_mixed_into_game_versions():
    assert file_loaders(arquivo_cf(1, ["NeoForge", "26.2"])) == {"neoforge"}
    assert file_loaders(arquivo_cf(1, ["Fabric", "Client", "1.20.1", "Quilt"])) == {
        "fabric",
        "quilt",
    }
    # arquivo antigo e resourcepack não marcam loader nenhum
    assert file_loaders(arquivo_cf(1, ["1.20.1", "Client"])) == set()
    assert file_loaders({}) == set()


# ---------------------------------------------------------------- preferência
def test_between_twins_the_pack_loader_decides():
    """O bug relatado: o jar de NeoForge entrando num pack de Fabric."""

    files = [
        arquivo_cf(8269700, ["NeoForge", "26.2"], date="2026-06-18T12:00:00"),
        arquivo_cf(8269699, ["Fabric", "26.2"], date="2026-06-18T11:00:00"),
    ]

    fabric = CurseForgeMatcher(FakeCurseForge([]), minecraft_version="26.2",
                               loader="fabric")
    neoforge = CurseForgeMatcher(FakeCurseForge([]), minecraft_version="26.2",
                                 loader="neoforge")

    assert fabric._pick_file(files, "cloth-config-26.2.155")["id"] == 8269699
    assert neoforge._pick_file(files, "cloth-config-26.2.155")["id"] == 8269700


def test_quilt_takes_a_fabric_file_but_fabric_does_not_take_quilt():
    fabric_file = arquivo_cf(1, ["Fabric", "1.20.1"])
    quilt_file = arquivo_cf(2, ["Quilt", "1.20.1"])

    quilt = CurseForgeMatcher(FakeCurseForge([]), loader="quilt")
    fabric = CurseForgeMatcher(FakeCurseForge([]), loader="fabric")

    # o Quilt roda mod de Fabric, mas prefere o dele
    escolhido = quilt._pick_file([fabric_file, quilt_file], "cloth-config-26.2.155")
    assert escolhido["id"] == 2
    assert quilt._loader_rank(fabric_file) == 1
    # o contrário não vale
    assert fabric._loader_rank(quilt_file) == 3


def test_a_file_without_loader_tags_never_loses_to_nothing():
    """Arquivo antigo do CurseForge não marca loader — e continua servindo."""

    sem_marca = arquivo_cf(1, ["1.20.1", "Client"])
    matcher = CurseForgeMatcher(FakeCurseForge([]), loader="fabric")

    assert matcher._loader_rank(sem_marca) == 2
    assert matcher._pick_file([sem_marca], "cloth-config-26.2.155")["id"] == 1


# ------------------------------------------------------------- segunda chance
def test_the_wrong_loader_is_kept_as_a_reserve_and_still_used():
    """Num pack Forge com Sinytra Connector, mods de Fabric estão lá de propósito.

    Recusar o único arquivo que existe mandaria para `overrides/` um match certo.
    O arquivo de outro loader perde para qualquer alternativa, mas não é vetado.
    """

    projeto = {
        "id": 1,
        "name": "Armor Chroma",
        "slug": "armor-chroma",
        "latestFiles": [arquivo_cf(10, ["Fabric", "1.20.1"], name="armorchroma.jar")],
    }
    matcher = CurseForgeMatcher(
        FakeCurseForge([projeto]), minecraft_version="1.20.1", loader="forge"
    )

    result = matcher.match(mod("armorchroma.jar"))

    assert result.matched
    assert result.file_id == 10


def test_the_reserve_loses_to_a_compatible_file_found_later():
    """O compatível pode não estar nos `latestFiles`; a busca tem de continuar."""

    projeto = {
        "id": 1,
        "name": "Cloth Config API",
        "slug": "cloth-config",
        # só o de NeoForge vem de graça na busca
        "latestFiles": [arquivo_cf(700, ["NeoForge", "26.2"])],
    }
    client = FakeCurseForge(
        [projeto], files_by_id={1: [arquivo_cf(699, ["Fabric", "26.2"])]}
    )
    matcher = CurseForgeMatcher(client, minecraft_version="26.2", loader="fabric")

    result = matcher.match(mod())

    assert result.file_id == 699, "devia ter procurado além dos latestFiles"
    assert client.listagens > 0


def test_a_compatible_file_stops_the_search_right_away():
    """E quando o de graça já serve, nada de listagem: a economia continua."""

    projeto = {
        "id": 1,
        "name": "Cloth Config API",
        "slug": "cloth-config",
        "latestFiles": [arquivo_cf(699, ["Fabric", "26.2"])],
    }
    client = FakeCurseForge([projeto])
    matcher = CurseForgeMatcher(client, minecraft_version="26.2", loader="fabric")

    assert matcher.match(mod()).file_id == 699
    assert client.listagens == 0
