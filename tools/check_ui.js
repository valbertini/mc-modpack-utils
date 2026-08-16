/* Conferência da interface sem navegador: `node tools/check_ui.js`
 *
 * O `app.js` roda num DOM de mentira e os renderizadores são chamados para cada
 * estado possível de um job. Não substitui abrir a página — pega o que já deu
 * problema aqui antes: aviso com a cor errada, botão que sobra num estado
 * terminal, seção que perde o card, `undefined` vazando para o HTML.
 */

"use strict";

const fs = require("fs");
const vm = require("vm");
const path = require("path");

const STATIC = path.join(__dirname, "..", "src", "mrpack2curseforge", "web", "static");
const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
const js = fs.readFileSync(path.join(STATIC, "app.js"), "utf8");

/* ----------------------------------------------------------------- DOM falso */
const avisos = [];

class El {
  constructor(id) {
    this.id = id || "";
    this._html = "";
    this._text = "";
    this.className = "";
    this.dataset = {};
    this.style = {};
    this.disabled = false;
    this.value = "";
    this.placeholder = "";
    this.title = "";
    this.hidden = false;

    const self = this;

    // `classList` de verdade: opera sobre o `className`, senão o teste não
    // enxerga nada além de `hidden` (foi o que deixou passar as travas)
    const classes = () =>
      new Set(String(self.className || "").split(/\s+/).filter(Boolean));

    const gravar = (conjunto) => {
      self.className = [...conjunto].join(" ");
      self.hidden = conjunto.has("hidden");
    };

    const mexer = (classe, ligado) => {
      const atual = classes();
      if (ligado) atual.add(classe);
      else atual.delete(classe);
      gravar(atual);
    };

    this.classList = {
      add: (c) => mexer(c, true),
      remove: (c) => mexer(c, false),
      toggle: (c, on) => mexer(c, on === undefined ? !classes().has(c) : !!on),
      contains: (c) => classes().has(c),
    };
  }

  // no DOM real um sobrescreve o outro
  get innerHTML() {
    return this._html;
  }
  set innerHTML(v) {
    const texto = String(v);
    if (/\[object Object\]/.test(texto)) {
      avisos.push(`objeto interpolado no HTML de #${this.id}`);
    }
    if (/(^|[>\s])undefined([<\s.,)]|$)/.test(texto)) {
      avisos.push(`"undefined" no HTML de #${this.id}`);
    }
    this._html = texto;
    this._text = texto.replace(/<[^>]+>/g, "");
  }
  get textContent() {
    return this._text;
  }
  set textContent(v) {
    this._text = String(v == null ? "" : v);
    this._html = "";
  }

  // devolve um elemento de mentira em vez de `null`: assim o código que
  // consulta e liga eventos depois de renderizar é realmente exercitado
  querySelector() {
    return new El();
  }
  querySelectorAll() {
    return [];
  }
  addEventListener() {}
  setAttribute(nome, valor) {
    this[nome] = valor;
  }
  getAttribute(nome) {
    return this[nome];
  }
  focus() {}
  appendChild() {}
  remove() {}
  scrollIntoView() {}
  closest() {
    return null;
  }
}

const SETTINGS_FALSO = {
  path: "C:/projeto/.env",
  locked_by: null,
  campos: [
    {
      chave: "CURSEFORGE_API_KEY",
      rotulo: "Chave da API",
      ajuda: "sem ela não roda",
      tipo: "secret",
      grupo: "acesso",
      padrao: "",
      link: "https://console.curseforge.com/#/api-keys",
      minimo: null,
      maximo: null,
      passo: null,
      valor: "••••••••9999",
      definido: true,
    },
    {
      chave: "M2CF_INPUT_DIR",
      rotulo: "Pasta de entrada",
      ajuda: "onde procurar",
      tipo: "texto",
      grupo: "pastas",
      padrao: "",
      minimo: null,
      maximo: null,
      passo: null,
      valor: "",
      definido: false,
    },
    {
      chave: "M2CF_WORKERS",
      rotulo: "Mods em paralelo",
      ajuda: "mais rápido, mais requisições",
      tipo: "inteiro",
      grupo: "desempenho",
      padrao: 6,
      minimo: 1,
      maximo: 24,
      passo: 1,
      valor: "8",
      definido: true,
    },
  ],
};

const elementos = new Map(
  [...html.matchAll(/id="([^"]+)"/g)].map((m) => [m[1], new El(m[1])])
);

const sandbox = {
  document: {
    getElementById: (id) => {
      if (!elementos.has(id)) elementos.set(id, new El(id));
      return elementos.get(id);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => new El(),
    addEventListener: () => {},
    body: new El("body"),
  },
  window: { addEventListener: () => {} },
  console,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  // responde por rota: sem isto os renderizadores que buscam dados quebram
  fetch: async (url) => ({
    ok: true,
    status: 200,
    json: async () => (String(url).includes("/api/settings") ? SETTINGS_FALSO : {}),
  }),
  CSS: { escape: (s) => s },
  FormData: class {},
  XMLHttpRequest: class {
    open() {}
    send() {}
    setRequestHeader() {}
    addEventListener() {}
    get upload() {
      return { addEventListener: () => {} };
    }
  },
};
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(js, sandbox, { filename: "app.js" });

// `const state` e as `function` ficam no escopo lexical do contexto
const pegar = (expr) => vm.runInContext(expr, sandbox);
const app = {
  state: pegar("state"),
  renderJob: pegar("renderJob"),
  renderConflicts: pegar("renderConflicts"),
  renderUpdateJob: pegar("renderUpdateJob"),
  renderUpdateReview: pegar("renderUpdateReview"),
  renderPacks: pegar("renderPacks"),
  renderUpdatePacks: pegar("renderUpdatePacks"),
  renderRecords: pegar("renderRecords"),
  selectPack: pegar("selectPack"),
  packEmCurso: pegar("packEmCurso"),
  abrirSettings: pegar("abrirSettings"),
  renderSettings: pegar("renderSettings"),
  SETTINGS: pegar("SETTINGS"),
  fecharSettings: pegar("fecharSettings"),
};

/* --------------------------------------------------------------- asserções */
let falhas = 0;

function conferir(nome, condicao, detalhe) {
  if (condicao) {
    console.log(`ok   ${nome}`);
    return;
  }
  falhas += 1;
  console.log(`FALHA ${nome}${detalhe ? " — " + detalhe : ""}`);
}

const botoes = (id) =>
  [...elementos.get(id).innerHTML.matchAll(/>([^<>]+)<\/(?:button|a)>/g)].map((m) =>
    m[1].trim()
  );

const aviso = (id) => {
  const el = elementos.get(id);
  return {
    visivel: !el.hidden,
    tom: el.className.replace("notice", "").trim() || "neutro",
    texto: el.textContent.replace(/\s+/g, " ").trim(),
  };
};

/* ------------------------------------------------------------------ dados */
const arquivo = (over = {}) => ({
  file_path: "mods/" + (over.file_name || "a.jar"),
  file_name: "a.jar",
  is_mod: true,
  status: "updated",
  has_version: true,
  project_id: "A",
  title: "Mod A",
  icon: null,
  url: null,
  from_version: "1",
  to_version: "2",
  version_type: "release",
  new_file_name: "a-2.jar",
  manual: false,
  chosen: null,
  skipped: false,
  excluded: false,
  ...over,
});

const jobConversao = (status, over = {}) => ({
  id: "j",
  kind: "conversion",
  source: "pack.mrpack",
  status,
  stage: "…",
  done: 3,
  total: 10,
  logs: [],
  log_count: 0,
  error: status === "error" ? "deu ruim" : null,
  conflicts: 2,
  unresolved: 2,
  dirty: false,
  report: {
    total_mods: 10,
    matched: 8,
    overrides: 2,
    version_unavailable: 1,
    not_on_curseforge: 1,
    failed: 0,
    minecraft_version: "1.21",
    loader: "fabric-0.16",
  },
  plan: { manifest: 8, manual: 0, downloads: 2, extra_files: 0, override_files: 3 },
  output: status === "done" ? { name: "p.zip", size_mb: 12.3 } : null,
  ...over,
});

const jobAtualizacao = (status, over = {}) => ({
  id: "u",
  kind: "update",
  source: "pack.mrpack",
  status,
  stage: "…",
  done: 3,
  total: 10,
  logs: [],
  log_count: 0,
  dirty: false,
  error: status === "error" ? "deu ruim" : null,
  output: status === "done" ? { name: "p.mrpack", size_mb: 6.8 } : null,
  update: {
    packaged: status === "done",
    with_version: [arquivo()],
    without_version: [
      arquivo({
        file_name: "b.jar",
        has_version: false,
        status: "incompatible",
        new_file_name: null,
        to_version: null,
        excluded: true,
      }),
    ],
    from_minecraft: "1.21.8",
    to_minecraft: "1.21.11",
    loader: "fabric-0.19",
    from_loader: "fabric",
    to_loader: "fabric",
    loader_changed: false,
    downgrade: false,
    summary: {
      total: 2,
      updated: 1,
      kept_by_choice: 0,
      unchanged: 0,
      incompatible: 1,
      unknown: 0,
      manual: 0,
      excluded: 1,
      unlisted: 0,
    },
  },
  ...over,
});

const conflitos = () => [
  { file_name: "x.jar", reason: "version-unavailable", resolution: null, modrinth: {} },
  { file_name: "y.jar", reason: "not-on-curseforge", resolution: null, modrinth: {} },
];

function renderConversao(status, preparar) {
  app.state.job = jobConversao(status);
  app.state.jobId = "j";
  app.state.conflicts = conflitos();
  app.state.pending = {};
  app.state.confirming = false;
  if (preparar) preparar(app.state.job);
  app.renderJob();
}

function renderAtualizacao(status, preparar) {
  app.state.updateJob = jobAtualizacao(status);
  app.state.updateJobId = "u";
  app.state.updatePending = {};
  app.state.updateInclude = {};
  app.state.updateKeep = {};
  app.state.updateConfirming = false;
  if (preparar) preparar(app.state.updateJob);
  app.renderUpdateJob();
  app.renderUpdateReview();
}

/* ------------------------------------------------------------------ testes */
async function main() {
console.log("--- conversão ---");

for (const status of ["queued", "running", "finishing"]) {
  renderConversao(status);
  conferir(
    `${status}: só oferece cancelar`,
    botoes("job-actions").length === 1 && /Cancelar/.test(botoes("job-actions")[0]),
    botoes("job-actions").join(" | ")
  );
}

renderConversao("awaiting_conflicts");
conferir(
  "awaiting_conflicts: aviso amarelo",
  aviso("job-notice").tom === "warn",
  aviso("job-notice").tom
);
conferir(
  "awaiting_conflicts: resolver + aplicar + cancelar",
  botoes("job-actions").length === 3,
  botoes("job-actions").join(" | ")
);

renderConversao("done");
conferir("done: aviso verde", aviso("job-notice").tom === "ok", aviso("job-notice").tom);
conferir(
  "done: não oferece cancelar",
  !botoes("job-actions").some((b) => /Cancelar/.test(b)),
  botoes("job-actions").join(" | ")
);

renderConversao("cancelled");
conferir(
  "cancelada é aviso laranja, não sucesso",
  aviso("job-notice").tom === "warn" && /cancelada/i.test(aviso("job-notice").texto),
  `${aviso("job-notice").tom}: ${aviso("job-notice").texto}`
);
conferir(
  "cancelada: só fechar",
  botoes("job-actions").length === 1,
  botoes("job-actions").join(" | ")
);

renderConversao("error");
conferir(
  "erro: aviso vermelho com a mensagem",
  aviso("job-notice").tom === "bad" && aviso("job-notice").texto === "deu ruim",
  `${aviso("job-notice").tom}: ${aviso("job-notice").texto}`
);

app.state.job = null;
app.state.jobId = null;
app.renderJob();
conferir(
  "sem job: painel vazio",
  !elementos.get("conversion-empty").hidden && elementos.get("conversion-body").hidden
);

console.log("\n--- atualização ---");

for (const status of ["queued", "running", "finishing"]) {
  renderAtualizacao(status);
  conferir(
    `${status}: só oferece cancelar`,
    botoes("u-actions").length === 1,
    botoes("u-actions").join(" | ")
  );
}

renderAtualizacao("awaiting_review");
conferir(
  "awaiting_review: aviso amarelo",
  aviso("u-notice").tom === "warn",
  aviso("u-notice").tom
);

renderAtualizacao("cancelled");
conferir(
  "cancelada é aviso laranja, não sucesso",
  aviso("u-notice").tom === "warn" && /cancelada/i.test(aviso("u-notice").texto),
  `${aviso("u-notice").tom}: ${aviso("u-notice").texto}`
);

renderAtualizacao("error");
conferir(
  "erro: aviso vermelho com a mensagem",
  aviso("u-notice").tom === "bad" && aviso("u-notice").texto === "deu ruim",
  `${aviso("u-notice").tom}: ${aviso("u-notice").texto}`
);

renderAtualizacao("done");
conferir("done: aviso verde", aviso("u-notice").tom === "ok", aviso("u-notice").tom);
conferir(
  "done sem mudanças: não oferece aplicar",
  !botoes("u-actions").some((b) => /Aplicar/.test(b)),
  botoes("u-actions").join(" | ")
);

renderAtualizacao("done");
app.renderUpdateReview();
conferir(
  "depois de gerar, a revisão esvazia (como a aba de conflitos)",
  !elementos.get("ur-empty").hidden && elementos.get("ur-summary").hidden,
  "empty=" + !elementos.get("ur-empty").hidden
);
conferir(
  "e não sobra 'aplicar' para um pack que já foi gerado",
  !botoes("u-actions").some((b) => /Aplicar/.test(b)),
  botoes("u-actions").join(" | ")
);

console.log("\n--- as três seções da revisão ---");

const secaoBruta = (i) =>
  elementos.get("ur-groups").innerHTML.split("<section").slice(1)[i] || "";

const secoes = () => {
  const markup = elementos.get("ur-groups").innerHTML;
  return markup.split("<section").slice(1).map((bloco) => ({
    titulo: (bloco.match(/<span class="tag [^"]*">([^<]+)</) || [])[1],
    arquivos: [...bloco.matchAll(/conflict-file mono">([^<]+)</g)].map((m) => m[1]),
  }));
};

renderAtualizacao("awaiting_review");
let atual = secoes();
conferir("três seções", atual.length === 3, `${atual.length}`);
conferir(
  "sem versão começa com o arquivo sem versão",
  atual[0].arquivos.join() === "b.jar",
  atual[0].arquivos.join()
);
conferir("resolvidos começa vazio", atual[1].arquivos.length === 0);
conferir(
  "com versão começa com o mod atualizável",
  atual[2].arquivos.join() === "a.jar",
  atual[2].arquivos.join()
);

renderAtualizacao("awaiting_review", () => {
  app.state.updatePending["mods/a.jar"] = { version_id: "v", file_name: "a-9.jar" };
});
atual = secoes();
conferir(
  "escolher versão move o card de 'com versão' para 'resolvidos'",
  atual[1].arquivos.join() === "a.jar" && atual[2].arquivos.length === 0,
  atual.map((s) => `${s.titulo}:[${s.arquivos}]`).join(" ")
);
conferir(
  "resolvido vindo de 'com versão' é roxo (.from-version)",
  /class="conflict resolved from-version/.test(elementos.get("ur-groups").innerHTML)
);

renderAtualizacao("awaiting_review", () => {
  app.state.updatePending["mods/b.jar"] = { version_id: "v", file_name: "b-9.jar" };
});
conferir(
  "resolvido vindo de 'sem versão' é verde (.from-missing)",
  /class="conflict resolved from-missing/.test(elementos.get("ur-groups").innerHTML)
);

// os dois resolvidos juntos: com versão primeiro
renderAtualizacao("awaiting_review", () => {
  app.state.updatePending["mods/a.jar"] = { version_id: "v", file_name: "a-9.jar" };
  app.state.updatePending["mods/b.jar"] = { version_id: "w", file_name: "b-9.jar" };
});
conferir(
  "resolvidos: quem tinha versão vem antes de quem não tinha",
  secoes()[1].arquivos.join() === "a.jar,b.jar",
  secoes()[1].arquivos.join()
);

console.log("\n--- incluir os que não são mods ---");

renderAtualizacao("awaiting_review", (job) => {
  job.update.without_version.push(
    arquivo({
      file_name: "shader.zip",
      is_mod: false,
      has_version: false,
      status: "unknown",
      new_file_name: null,
      to_version: null,
      excluded: true,
    })
  );
});
conferir(
  "o card não tem mais o par incluir/não incluir",
  !/data-uinclude|data-uexclude/.test(elementos.get("ur-groups").innerHTML)
);
conferir(
  "a seção 'sem versão' oferece incluir os não-mods",
  /data-ubulk="incluir"[\s\S]{0,180}não é mod/.test(
    elementos.get("ur-groups").innerHTML
  ),
  elementos.get("ur-groups").innerHTML.match(/data-ubulk[^<]*/)?.[0]
);

renderAtualizacao("awaiting_review", (job) => {
  job.update.without_version.push(
    arquivo({
      file_name: "shader.zip",
      is_mod: false,
      has_version: false,
      status: "unknown",
      new_file_name: null,
      to_version: null,
      excluded: true,
    })
  );
  app.state.updateInclude["mods/shader.zip"] = true;
});
atual = secoes();
conferir(
  "incluir um não-mod manda ele para 'resolvidos'",
  atual[1].arquivos.join() === "shader.zip" && !atual[0].arquivos.includes("shader.zip"),
  atual.map((s) => `${s.titulo}:[${s.arquivos}]`).join(" ")
);
conferir(
  "o 'tirar' existe, mas na seção do meio (não no 'sem versão')",
  /data-ubulk="tirar"/.test(secaoBruta(1)) && !/data-ubulk/.test(secaoBruta(0)),
  "sem-versão ainda tem botão? " + /data-ubulk/.test(secaoBruta(0))
);
conferir(
  "incluído em massa fica em verde escuro (.as-is)",
  /class="conflict resolved as-is/.test(secaoBruta(1))
);

console.log("\n--- painel de confirmação do atualizador ---");

renderAtualizacao("awaiting_review");
conferir(
  "aplicar não gera direto: abre o painel primeiro",
  elementos.get("u-confirm").hidden
);

renderAtualizacao("awaiting_review", () => {
  app.state.updateConfirming = true;
});
conferir(
  "o painel diz quantos entram e quantos ficam de fora",
  !elementos.get("u-confirm").hidden &&
    /entram no/.test(elementos.get("u-confirm").textContent) &&
    /de fora/.test(elementos.get("u-confirm").textContent),
  elementos.get("u-confirm").textContent.slice(0, 90)
);
conferir(
  "o painel tem continuar e voltar",
  /data-uconfirm="go"/.test(elementos.get("u-confirm").innerHTML) &&
    /data-uconfirm="back"/.test(elementos.get("u-confirm").innerHTML)
);

renderAtualizacao("cancelled", () => {
  app.state.updateConfirming = true;
});
conferir(
  "cancelar fecha o painel de confirmação",
  elementos.get("u-confirm").hidden
);

console.log("\n--- lista de modpacks de entrada ---");
const packs = [
  {
    name: "a.mrpack",
    size_mb: 6.8,
    modified: 1,
    minecraft: "1.21.8",
    loader: "fabric",
    loader_version: "0.19.3",
    mods: 49,
  },
  {
    name: "quebrado.mrpack",
    size_mb: 1,
    modified: 1,
    minecraft: null,
    loader: null,
    loader_version: null,
    mods: null,
  },
];
app.state.packs = packs;
app.renderPacks(packs);
const lista = elementos.get("pack-list").innerHTML;
conferir("mostra Minecraft e loader", /1\.21\.8/.test(lista) && /fabric/.test(lista));
conferir("pack ilegível não quebra a lista", /índice ilegível/.test(lista));

console.log("\n--- travas durante a geração ---");

renderConversao("finishing");
app.renderConflicts();
conferir(
  "conflitos travam enquanto o .zip é gerado",
  elementos.get("conflict-groups").className.includes("locked") &&
    elementos.get("btn-save").disabled,
  elementos.get("conflict-groups").className
);

renderConversao("awaiting_conflicts");
app.renderConflicts();
conferir(
  "e destravam quando volta a esperar você",
  !elementos.get("conflict-groups").className.includes("locked")
);

renderAtualizacao("finishing");
conferir(
  "a revisão da atualização também trava",
  elementos.get("ur-groups").className.includes("locked")
);

conferir(
  "travar não escreve aviso nenhum na tela",
  !/Espere terminar/.test(elementos.get("ur-groups").innerHTML)
);

console.log("\n--- pack em processamento ---");

const packsCurso = [
  { name: "outro.mrpack", size_mb: 1, modified: 1, minecraft: "1.20.1",
     loader: "forge", loader_version: "47.4", mods: 10 },
  { name: "pack.mrpack", size_mb: 6.8, modified: 2, minecraft: "1.21.8",
     loader: "fabric", loader_version: "0.19.3", mods: 49 },
];
app.state.packs = packsCurso;

renderConversao("running");
app.renderPacks(packsCurso);
const listaCurso = elementos.get("pack-list").innerHTML;
conferir(
  "o pack em conversão vai para o topo",
  listaCurso.indexOf("pack.mrpack") < listaCurso.indexOf("outro.mrpack"),
  listaCurso.indexOf("pack.mrpack") + " vs " + listaCurso.indexOf("outro.mrpack")
);
conferir(
  "e fica visualmente distinto (.working + spinner)",
  /class="pack[^"]*working/.test(listaCurso) && /class="spinner"/.test(listaCurso)
);

renderConversao("done");
app.renderPacks(packsCurso);
const listaPronta = elementos.get("pack-list").innerHTML;
conferir(
  "terminado, o spinner some mas o pack do job segue marcado",
  !/working/.test(listaPronta) && /in-job/.test(listaPronta)
);

app.state.job = null;
app.state.jobId = null;
app.renderPacks([
  { name: "velho.mrpack", size_mb: 1, modified: 1, minecraft: "1.20.1",
    loader: "forge", loader_version: "47", mods: 1, last_used: 100 },
  { name: "recente.mrpack", size_mb: 1, modified: 1, minecraft: "1.21.8",
    loader: "fabric", loader_version: "0.19", mods: 1, last_used: 900 },
  { name: "nunca.mrpack", size_mb: 1, modified: 1, minecraft: "1.21.1",
    loader: "fabric", loader_version: "0.16", mods: 1, last_used: null },
]);
const porUso = elementos.get("pack-list").innerHTML;
conferir(
  "sem job, ordena pelo uso mais recente",
  porUso.indexOf("recente.mrpack") < porUso.indexOf("velho.mrpack") &&
    porUso.indexOf("velho.mrpack") < porUso.indexOf("nunca.mrpack"),
  [
    porUso.indexOf("recente.mrpack"),
    porUso.indexOf("velho.mrpack"),
    porUso.indexOf("nunca.mrpack"),
  ].join(" < ")
);

app.state.updatePack = null;
renderAtualizacao("running");
app.renderUpdatePacks(packsCurso);
conferir(
  "o mesmo vale para a atualização",
  /class="pack[^"]*working/.test(elementos.get("update-pack-list").innerHTML)
);

console.log("\n--- o verde acompanha o trabalho inteiro ---");

const emCurso = app.packEmCurso;
for (const estado of ["queued", "running", "finishing", "awaiting_conflicts"]) {
  conferir(
    `${estado}: o pack continua marcado`,
    emCurso({ status: estado, source: "p.mrpack" }) === "p.mrpack"
  );
}
for (const estado of ["done", "cancelled", "error"]) {
  conferir(
    `${estado}: o pack deixa de estar em curso`,
    emCurso({ status: estado, source: "p.mrpack" }) === null
  );
}

// selecionar outro pack no meio não pode tirar o verde do que está rodando
app.state.packs = packsCurso;
renderConversao("running");
app.state.selection = null;
app.selectPack("outro.mrpack");
conferir(
  "selecionar outro pack não apaga o verde do que está rodando",
  /class="pack[^"]*working/.test(elementos.get("pack-list").innerHTML),
  elementos.get("pack-list").innerHTML.match(/class="pack[^"]*"/g)?.join(" | ")
);
conferir(
  "e o outro fica azul de selecionado",
  /class="pack selected/.test(elementos.get("pack-list").innerHTML)
);

// job morto some sozinho em vez de ficar dizendo "cancelada"
renderConversao("cancelled");
app.selectPack("outro.mrpack");
conferir(
  "selecionar um pack descarta o trabalho cancelado",
  app.state.job === null && app.state.jobId === null,
  `job=${app.state.job && app.state.job.status}`
);

renderConversao("done");
app.selectPack("outro.mrpack");
conferir(
  "mas um trabalho concluído continua aberto (tem .zip para baixar)",
  app.state.job !== null
);

console.log("\n--- saída selecionada ---");

app.state.selection = { kind: "record", id: "r1" };
app.renderRecords([
  { id: "r0", pack: { name: "Velho", minecraft: "1.20.1", loader: "forge-47" },
     summary: { matched: 1, total_mods: 2 }, updated_at: 1, source_available: true },
  { id: "r1", pack: { name: "Novo", minecraft: "1.21.8", loader: "fabric-0.19" },
     summary: { matched: 2, total_mods: 2 }, updated_at: 2, source_available: true },
]);
const salvos = elementos.get("record-list").innerHTML.replace(/\s+/g, " ");
conferir(
  "a conversão salva selecionada fica azul",
  /class="pack selected[^"]*" data-record="r1"/.test(salvos),
  salvos.match(/class="pack[^"]*" data-record="\w+"/g)?.join(" | ")
);
conferir(
  "e usa as mesmas tags da entrada",
  /tag mc/.test(salvos) && /tag loader l-fabric/.test(salvos)
);

console.log("\n--- tags de versão e loader ---");

app.renderPacks(packsCurso);
const tags = elementos.get("pack-list").innerHTML;
conferir(
  "cada loader tem a sua cor",
  /tag loader l-fabric/.test(tags) && /tag loader l-forge/.test(tags),
  (tags.match(/l-\w+/g) || []).join(" ")
);
conferir(
  "a versão do Minecraft ganha matiz própria",
  /tag mc" style="color:hsl\(\d+/.test(tags),
  (tags.match(/hsl\([^)]*\)/g) || []).slice(0, 2).join(" ")
);

const hue = pegar("mcHue");
conferir(
  "mais novo = azul, mais antigo = vermelho",
  hue("1.21.8") > hue("1.20.1") && hue("1.20.1") > hue("1.16.5") && hue("1.7.10") === 0,
  `1.21=${hue("1.21.8")} 1.20=${hue("1.20.1")} 1.16=${hue("1.16.5")} 1.7=${hue("1.7.10")}`
);
conferir("versão ilegível não quebra", hue("nada") === null);

conferir(
  "existe um botão para encerrar o servidor",
  html.includes('id="btn-quit"')
);

console.log("\n--- configurações ---");

conferir(
  "há uma engrenagem depois do Encerrar",
  html.indexOf('id="btn-quit"') < html.indexOf('id="btn-settings"')
);
conferir("o painel começa escondido", /id="settings-panel"[^>]*hidden|class="settings hidden"/.test(html));

await app.abrirSettings();

const painel = elementos.get("settings-fields").innerHTML;
conferir("o painel abre", app.SETTINGS.aberto === true);
conferir(
  "a chave da API é campo de senha",
  /type="password"[^>]*|data-set="CURSEFORGE_API_KEY"/.test(painel) &&
    /data-reveal="CURSEFORGE_API_KEY"/.test(painel),
  painel.match(/type="\w+"/g)?.join(" ")
);
conferir(
  "e o valor de verdade não vai para o campo (só o placeholder mascarado)",
  /placeholder="•+9999"/.test(painel) && !/value="•+9999"/.test(painel)
);
conferir(
  "campo numérico tem slider e caixa",
  /data-set-range="M2CF_WORKERS"/.test(painel) &&
    /type="number"[^>]*data-set="M2CF_WORKERS"/.test(painel)
);
conferir(
  "campos agrupados por assunto",
  (painel.match(/setting-group/g) || []).length === 3,
  (painel.match(/setting-group">[^<]*/g) || []).join(" | ")
);
conferir(
  "sem alteração, salvar e descartar ficam desligados",
  elementos.get("btn-settings-save").disabled &&
    elementos.get("btn-settings-discard").disabled
);

app.SETTINGS.rascunho["M2CF_WORKERS"] = "12";
app.renderSettings();
conferir(
  "com alteração, salvar liga e o aviso de não salvo aparece",
  !elementos.get("btn-settings-save").disabled &&
    !elementos.get("settings-dirty").hidden
);

// o limiar de similaridade não pode aparecer na tela
conferir(
  "o limiar de similaridade fica fora das configurações",
  !elementos.get("settings-fields").innerHTML.includes("VERSION_THRESHOLD")
);

// apagar a chave fica junto do campo, não no rodapé
conferir(
  "apagar chave fica dentro do campo da chave",
  /data-set="CURSEFORGE_API_KEY"[\s\S]{0,700}btn-settings-forget/.test(
    elementos.get("settings-fields").innerHTML
  )
);
conferir(
  "e não sobrou no rodapé do painel",
  !html.includes('id="btn-settings-forget"')
);

// atalho para a página onde a chave é gerada: só enquanto ela falta
conferir(
  "com chave salva, nada de atalho",
  !elementos.get("settings-fields").innerHTML.includes("setting-link")
);

app.SETTINGS.campos[0].definido = false;
app.renderSettings();

{
  const campos = elementos.get("settings-fields").innerHTML;
  conferir(
    "sem chave, o atalho aparece como etiqueta",
    /class="tag setting-link" href="https:\/\/console\.curseforge\.com[\s\S]{0,600}data-set="CURSEFORGE_API_KEY"/.test(
      campos
    )
  );
  conferir(
    "o atalho abre fora e sem vazar o referrer",
    /setting-link[\s\S]{0,90}target="_blank" rel="noreferrer noopener"/.test(campos)
  );
  conferir(
    "e, sem texto, se anuncia pelo aria-label",
    /setting-link[\s\S]{0,240}aria-label="[^"]+">↗<\/a>/.test(campos)
  );
  conferir(
    "e só o campo que declara link ganha atalho",
    (campos.match(/setting-link/g) || []).length === 1,
    (campos.match(/setting-link/g) || []).length
  );
}

app.SETTINGS.campos[0].definido = true;
app.renderSettings();

// revelar a chave não pode fechar o painel
app.SETTINGS.rascunho = {};
app.SETTINGS.revelando["CURSEFORGE_API_KEY"] = true;
app.renderSettings();
conferir(
  "revelar mostra o campo como texto",
  /data-set="CURSEFORGE_API_KEY"[^>]*type="text"/.test(
    elementos.get("settings-fields").innerHTML
  )
);
conferir(
  "e o painel continua aberto",
  app.SETTINGS.aberto === true && !elementos.get("settings-panel").hidden
);

/* Estática, e de propósito: o DOM falso não propaga eventos, então o único
   jeito de travar este bug é exigir o `stopPropagation` no handler. Sem ele o
   redesenho tira o botão do DOM, o `closest(".settings-wrap")` do listener
   global dá null, e o painel se fecha sozinho ao revelar a chave. */
const inicioReveal = js.indexOf('querySelectorAll("[data-reveal]")');
const trechoReveal = js.slice(
  inicioReveal,
  // só até o fim do próprio handler, senão o `stopPropagation` do botão
  // seguinte faria a asserção passar por engano
  js.indexOf("renderSettings();", inicioReveal)
);
conferir(
  "o handler do olho impede o clique de virar 'clique fora'",
  /stopPropagation\(\)/.test(trechoReveal),
  trechoReveal.slice(0, 120).replace(/\s+/g, " ")
);

// com trabalho aberto, nada é editável
app.SETTINGS.travado = "pack.mrpack";
app.SETTINGS.rascunho = { M2CF_WORKERS: "12" };
app.renderSettings();
conferir(
  "trabalho aberto trava os campos",
  elementos.get("settings-fields").className.includes("locked") &&
    elementos.get("btn-settings-save").disabled &&
    elementos.get("btn-settings-reset").disabled,
  elementos.get("settings-fields").className
);
conferir(
  "e diz qual trabalho está segurando",
  elementos.get("settings-notice").textContent.includes("pack.mrpack"),
  elementos.get("settings-notice").textContent.slice(0, 70)
);

app.SETTINGS.travado = null;
app.fecharSettings();

console.log("\n--- estrutura do layout ---");

for (const painel of ["panel-convert", "panel-update"]) {
  const bloco = html.split(`id="${painel}"`)[1].split("</section>")[0];

  conferir(
    `${painel}: as três colunas usam .col-card`,
    (bloco.match(/class="card col-card/g) || []).length === 3,
    (bloco.match(/class="card col-card[^"]*"/g) || []).join(" | ")
  );
  conferir(
    `${painel}: aviso e botões ficam acima do log`,
    bloco.indexOf("job-actions") < bloco.indexOf('class="log'),
    `botões em ${bloco.indexOf("job-actions")}, log em ${bloco.indexOf('class="log')}`
  );
  conferir(
    `${painel}: o popup cobre o aviso e os botões`,
    bloco.indexOf("job-head") < bloco.indexOf('class="notice') &&
      bloco.indexOf('class="notice') < bloco.indexOf('class="confirm') &&
      bloco.indexOf('class="confirm') < bloco.indexOf('class="log')
  );
  conferir(
    `${painel}: a lista e o log rolam por dentro`,
    (bloco.match(/scroller/g) || []).length >= 3,
    (bloco.match(/scroller/g) || []).length + " scrollers"
  );
}

// cada botão do seletor tem o painel correspondente, e um só começa ativo
for (const grupo of ["convert", "update"]) {
  const lados = [...html.matchAll(
    new RegExp(`data-side="(${grupo}-\\w+)"`, "g")
  )].map((m) => m[1]);

  conferir(
    `seletor ${grupo}: dois lados`,
    lados.length === 2,
    lados.join(", ")
  );
  conferir(
    `seletor ${grupo}: cada botão tem o seu painel`,
    lados.every((lado) => html.includes(`id="side-${lado}"`)),
    lados.join(", ")
  );
}

conferir(
  "um lado ativo por seletor",
  (html.match(/class="side active"/g) || []).length === 2,
  (html.match(/class="side active"/g) || []).length + " ativos"
);

console.log();
if (avisos.length) {
  console.log("avisos de renderização:");
  [...new Set(avisos)].forEach((a) => console.log("  -", a));
}

if (falhas) {
  console.log(`${falhas} verificação(ões) falharam`);
  process.exit(1);
}
console.log("interface consistente em todos os estados");
}

main().catch((erro) => {
  console.error("o teste explodiu:", erro);
  process.exit(1);
});
