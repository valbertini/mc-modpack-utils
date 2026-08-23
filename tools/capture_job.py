"""Captura o payload de uma análise de verdade em `tests/ui/job.json`.

    uv run python tools/capture_job.py "meu pack.mrpack"
    node tests/ui/render_real.js

Sobe um servidor numa porta e nas **pastas de teste** (`test_modpacks/`, ou o
que estiver em `M2CF_TEST_INPUT_DIR`/`M2CF_TEST_OUTPUT_DIR`), roda a análise —
que só consulta as APIs — e salva o que a interface receberia. Nada é escrito no
seu `input_modpacks/` nem no seu `output_modpacks/`.

O pack é procurado primeiro em `test_modpacks/input/` e depois em
`input_modpacks/`; um pack pequeno guardado lá deixa o ciclo inteiro rápido.

O par com o `tests/ui/render_real.js` é a resposta para "a tela mostra algo
errado e a bateria está verde": o `check_ui.js` usa dados escritos à mão, e um
fixture escrito à mão sempre tem o campo que o servidor esqueceu de mandar.

Este arquivo fica em `tools/` e não em `tests/` de propósito: ele precisa de
rede e da chave da API, e a regra de `tests/` é que nada lá toca a rede.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mrpack2curseforge.config import Config  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
# o payload é consumido pelo `tests/ui/render_real.js`; fica ao lado dele
DESTINO = Path(__file__).resolve().parent.parent / "tests" / "ui" / "job.json"
PORTA = int(os.getenv("M2CF_CAPTURE_PORT", "9077"))
BASE = f"http://127.0.0.1:{PORTA}"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        for pasta in (Config.TEST_INPUT_DIR, RAIZ / "input_modpacks"):
            packs = sorted(pasta.glob("*.mrpack")) if pasta.is_dir() else []
            if packs:
                print(f"Packs em {pasta}:")
                for p in packs:
                    print("   ", p.name)
        return 2

    nome = sys.argv[1]
    entrada, saida = Config.TEST_INPUT_DIR, Config.TEST_OUTPUT_DIR
    entrada.mkdir(parents=True, exist_ok=True)
    saida.mkdir(parents=True, exist_ok=True)

    origem = entrada / nome
    if not origem.is_file():
        # ainda não está na área de teste: traz uma cópia do que você usa
        de_verdade = RAIZ / "input_modpacks" / nome
        if not de_verdade.is_file():
            print(f"não achei {nome} em {entrada} nem em input_modpacks/")
            return 2
        shutil.copy2(de_verdade, origem)
        print(f"copiado para {entrada}")

    ambiente = {
        **os.environ,
        "M2CF_INPUT_DIR": str(entrada),
        "M2CF_OUTPUT_DIR": str(saida),
    }

    servidor = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from mrpack2curseforge.web.server import serve; serve(port={PORTA})",
        ],
        cwd=RAIZ,
        env=ambiente,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    client = httpx.Client(timeout=180)

    try:
        for _ in range(40):
            try:
                client.get(f"{BASE}/api/state")
                break
            except httpx.HTTPError:
                time.sleep(0.5)

        print(f"analisando {nome}…")
        job_id = client.post(f"{BASE}/api/convert", json={"file": nome}).json()["id"]

        parado = ("awaiting_conflicts", "done", "error", "cancelled")
        for _ in range(600):
            job = client.get(f"{BASE}/api/jobs/{job_id}").json()
            if job["status"] in parado:
                break
            time.sleep(1)

        estado = client.get(f"{BASE}/api/state").json()
        conflitos = client.get(f"{BASE}/api/jobs/{job_id}/conflicts").json()

        DESTINO.write_text(
            json.dumps(
                {
                    "job": job,
                    "conflicts": conflitos["conflicts"],
                    "packs": estado["packs"],
                    "records": estado["records"],
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

        print(f"{DESTINO.name} salvo · status {job['status']}")
        print("agora: node tests/ui/render_real.js")

        client.post(f"{BASE}/api/jobs/{job_id}/cancel")
        client.post(f"{BASE}/api/jobs/{job_id}/close")

    finally:
        client.post(f"{BASE}/api/shutdown")
        try:
            servidor.wait(timeout=30)
        except subprocess.TimeoutExpired:
            servidor.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
