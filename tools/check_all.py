"""Bateria completa: `uv run python tools/check_all.py`

Roda tudo o que substitui CI aqui e devolve código != 0 se algo falhar.
Existe para ser um comando só, sem pipes nem substituição de comando — que no
Git Bash do Windows caem no prompt de aprovação e travam a sessão.

O limite de 88 colunas e os importes sem uso saem do flake8 (`.flake8` na
raiz); antes havia uma varredura de colunas escrita à mão aqui, que via só o
comprimento das linhas.
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
    linhas = texto.splitlines()

    # quando passa, o resumo basta; quando falha, você quer a lista inteira
    print("\n".join(linhas if saida.returncode else linhas[-6:]) or "  (sem saída)")

    if saida.returncode:
        falhas.append(nome)


rodar("pytest", [sys.executable, "-m", "pytest", "-q"])
rodar("flake8", [sys.executable, "-m", "flake8", "src", "tests", "tools"])
rodar("estados da interface", ["node", str(RAIZ / "tools/check_ui.js")])
rodar("sintaxe do app.js", ["node", "--check", str(APP_JS)])

print()
if falhas:
    print("FALHOU: " + ", ".join(falhas))
    sys.exit(1)

print("tudo passou")
