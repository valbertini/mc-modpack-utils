"""Bateria completa: `uv run python tools/check_all.py`

Roda tudo o que substitui CI aqui e devolve código != 0 se algo falhar.
Existe para ser um comando só, sem pipes nem substituição de comando — que no
Git Bash do Windows caem no prompt de aprovação e travam a sessão.
"""

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
APP_JS = RAIZ / "src/mrpack2curseforge/web/static/app.js"

falhas: list[str] = []


def rodar(nome: str, comando: list[str]) -> None:
    print(f"\n=== {nome} ===")

    try:
        saida = subprocess.run(
            comando, cwd=RAIZ, capture_output=True, text=True, encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        print(f"  (pulado: {comando[0]} não encontrado)")
        return

    texto = (saida.stdout + saida.stderr).strip()
    print("\n".join(texto.splitlines()[-6:]) if texto else "  (sem saída)")

    if saida.returncode:
        falhas.append(nome)


rodar("pytest", [sys.executable, "-m", "pytest", "-q"])
rodar("estados da interface", ["node", str(RAIZ / "tools/check_ui.js")])
rodar("sintaxe do app.js", ["node", "--check", str(APP_JS)])

# ------------------------------------------------------------- linhas longas
print("\n=== linhas > 88 colunas ===")
longas = [
    f"{caminho.relative_to(RAIZ).as_posix()}:{numero}"
    for pasta in ("src", "tests", "tools")
    for caminho in sorted((RAIZ / pasta).rglob("*.py"))
    for numero, linha in enumerate(
        caminho.read_text(encoding="utf-8").splitlines(), start=1
    )
    if len(linha) > 88
]

print("  " + ("\n  ".join(longas) if longas else "nenhuma"))
if longas:
    falhas.append("linhas longas")

print()
if falhas:
    print("FALHOU: " + ", ".join(falhas))
    sys.exit(1)

print("tudo passou")
