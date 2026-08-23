"""Constantes globais do projeto."""

# ---------------------------------------------------------------- manifest
CURSEFORGE_MANIFEST_VERSION = 1
CURSEFORGE_MANIFEST_TYPE = "minecraftModpack"

MODRINTH_INDEX_FILE = "modrinth.index.json"

DEFAULT_OVERRIDES = "overrides"

USER_AGENT = "mrpack2curseforge/0.2 (+https://github.com/local/mrpack2curseforge)"

# ---------------------------------------------------------------- APIs
CURSEFORGE_API = "https://api.curseforge.com/v1"
MODRINTH_API = "https://api.modrinth.com/v2"

CURSEFORGE_GAME_ID = 432  # Minecraft
CURSEFORGE_CLASS_MODS = 6  # classId da categoria "Mods"

# Cada pasta do índice do Modrinth tem uma seção equivalente no CurseForge.
# O `classId` filtra a busca (sem ele, procurar por um resourcepack devolve
# mods) e a seção é o pedaço da URL do site — só os mods ficam em `mc-mods`.
# Ids conferidos em `GET /categories?gameId=432&classesOnly=true`.
CURSEFORGE_CLASSES: dict[str, int] = {
    "mods": CURSEFORGE_CLASS_MODS,
    "resourcepacks": 12,
    "shaderpacks": 6552,
}
CURSEFORGE_SECTIONS: dict[str, str] = {
    "mods": "mc-mods",
    "resourcepacks": "texture-packs",
    "shaderpacks": "shaders",
}
DEFAULT_SECTION = "mc-mods"

# Rascunho da conversão dentro da pasta de saída: montado antes do zip e
# apagado quando o servidor encerra.
WORK_DIRNAME = ".work"

# Tamanho máximo de página aceito pela API do CurseForge
CURSEFORGE_PAGE_SIZE = 50
