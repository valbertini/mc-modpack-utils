/* A interface renderizada com o payload de um job DE VERDADE.
 *
 *   uv run python tools/capture_job.py "meu pack.mrpack"
 *   node tests/ui/render_real.js
 *
 * Por que existe: o `check_ui.js` usa dados escritos à mão, e um fixture
 * escrito à mão sempre tem o campo que o servidor esqueceu de mandar. Foi assim
 * que um `NaN` chegou à tela do usuário com a bateria toda verde.
 *
 * Não entra no `check_all.py`: depende de um `job.json` capturado antes, o que
 * exige rede e chave da API.
 */

"use strict";

const fs = require("fs");
const path = require("path");

const { montar } = require("./fake_dom");

const ARQUIVO = path.join(__dirname, "job.json");

if (!fs.existsSync(ARQUIVO)) {
  console.log(`${ARQUIVO} não existe.`);
  console.log("Rode antes: uv run python tools/capture_job.py <pack.mrpack>");
  process.exit(2);
}

const dados = JSON.parse(fs.readFileSync(ARQUIVO, "utf8"));
const { avisos, elementos, pegar } = montar();

const state = pegar("state");
state.job = dados.job;
state.jobId = dados.job.id;
state.conflicts = dados.conflicts || [];
state.packs = dados.packs || [];
state.pending = {};

console.log(`job ${dados.job.status}`);
console.log(`plano: ${JSON.stringify(dados.job.plan)}`);
console.log(
  `${state.conflicts.length} conflito(s) · ${state.packs.length} pack(s) na entrada`
);

pegar("renderPacks")(state.packs);
pegar("renderUpdatePacks")(state.packs);
pegar("renderRecords")(dados.records || []);
pegar("renderJob")();
pegar("renderConflicts")();

state.confirming = true;
pegar("renderConfirm")(dados.job);

const limpo = (id) =>
  elementos.get(id).textContent.replace(/\s+/g, " ").trim();

console.log("\naviso do job:\n  " + limpo("job-notice"));
console.log("\ncard de confirmação:\n  " + limpo("apply-confirm"));

console.log();
if (avisos.length) {
  console.log("avisos de renderização com o payload real:");
  [...new Set(avisos)].forEach((a) => console.log("  -", a));
  process.exit(1);
}
console.log("nada de NaN, undefined ou objeto interpolado");
