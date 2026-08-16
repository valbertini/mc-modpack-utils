"use strict";

/* ------------------------------------------------------------------ estado */
const state = {
  tool: "converter",      // ferramenta ativa: "converter" | "atualizador"
  lastTab: {},            // ferramenta -> última aba aberta nela
  selectedPack: null,     // .mrpack escolhido para converter
  updatePack: null,       // .mrpack escolhido para atualizar
  // decisões da revisão ainda não salvas (o mesmo modelo da aba de conflitos)
  updatePending: {},      // file_path -> escolha de versão | null (desfazer)
  updateInclude: {},      // file_path -> entra no pack mesmo sem versão
  updateKeep: {},         // file_path -> fica na versão atual
  updateVersions: {},     // project_id|loader -> versões do Modrinth
  updateProjects: {},     // project_id -> metadados do projeto
  updateSearch: {},       // file_path -> resultados da busca no Modrinth
  loaderVersions: {},     // loader|minecraft -> versões publicadas do loader
  loaderVersion: "",      // versão escolhida (vazio = a mais recente)
  updateConfirming: false, // painel "o que vai acontecer" aberto
  openUpdateFile: null,
  // a atualização tem job próprio: as duas ferramentas rodam ao mesmo tempo
  updateJob: null,
  updateJobId: null,
  updateLogCount: 0,
  updatePolling: null,
  selection: null,        // conversor: {kind: "input"|"record", id}
  selectedUpdate: null,   // atualizador: nome do pack atualizado em foco
  freshOutput: null,      // saída recém-gerada (fica verde até você sair dela)
  job: null,              // snapshot do job aberto
  jobId: null,
  logCount: 0,
  conflicts: [],          // como está no servidor
  pending: {},            // escolhas ainda não salvas (file_name -> resolution|null)
  polling: null,
  openConflict: null,
  confirming: false,      // painel "o que vai acontecer" aberto
  fileCache: {},          // project_id -> arquivos
  projectCache: {},       // project_id -> metadados
  searchCache: {},        // file_name -> resultados da busca
  detailCache: {},
};

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

const post = (path) => api(path, { method: "POST" });

const json = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/**
 * Dois cliques para ações que jogam trabalho fora.
 *
 * Devolve `false` no primeiro clique (e arma o botão por 4 s), `true` no
 * segundo. O rótulo volta sozinho — nada de diálogo modal para isso.
 */
const armados = {};

function armarBotao(chave, aviso) {
  if (armados[chave]) {
    clearTimeout(armados[chave].timer);
    delete armados[chave];
    return true;
  }

  armados[chave] = {
    aviso,
    timer: setTimeout(() => {
      delete armados[chave];
      renderJob();
      renderUpdateJob();
    }, 4000),
  };

  renderJob();
  renderUpdateJob();
  return false;
}

const rotuloArmado = (chave, padrao) =>
  armados[chave] ? armados[chave].aviso : padrao;

let toastTimer = null;

function toast(message, kind) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast " + (kind || "");

  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 5200);
}

const fmtDate = (s) => (s ? new Date(s * 1000).toLocaleString("pt-BR") : "");

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const cssEscape = (value) => String(value).replace(/["\\]/g, "\\$&");

/* Escreve no DOM só quando o conteúdo muda de verdade.
   O polling roda a cada 600 ms; reescrever o mesmo HTML fazia os botões
   piscarem e derrubava qualquer texto selecionado. */
const htmlCache = new WeakMap();

function setHTML(element, html) {
  if (htmlCache.get(element) === html) return false;
  htmlCache.set(element, html);
  element.innerHTML = html;
  return true;
}

function setText(element, text) {
  if (element.textContent === text) return false;
  element.textContent = text;
  return true;
}

function setClass(element, className) {
  if (element.className === className) return false;
  element.className = className;
  return true;
}

/* ------------------------------------------------ ferramentas e suas abas */
function selectTool(tool, aba) {
  state.tool = tool;

  document.querySelectorAll(".tool").forEach((el) => {
    el.classList.toggle("active", el.dataset.tool === tool);
  });

  document.querySelectorAll(".tab").forEach((el) => {
    el.classList.toggle("hidden", el.dataset.tool !== tool);
  });

  // a aba pedida; senão a última em que você estava nesta ferramenta
  const escolha = aba || state.lastTab[tool];
  const alvo =
    (escolha &&
      document.querySelector(`.tab[data-tab="${escolha}"][data-tool="${tool}"]`)) ||
    document.querySelector(`.tab[data-tool="${tool}"]`);

  if (alvo) goToTab(alvo.dataset.tab);
}

$("tools").addEventListener("click", (event) => {
  const tool = event.target.closest(".tool");
  if (tool) selectTool(tool.dataset.tool);
});

$("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (tab) goToTab(tab.dataset.tab);
});

function goToTab(name) {
  const tab = document.querySelector(`.tab[data-tab="${name}"]`);
  if (!tab) return;

  // trocar de aba pode significar trocar de ferramenta — mas continua sendo
  // *esta* aba que o usuário pediu, não a primeira da outra ferramenta
  if (tab.dataset.tool !== state.tool) {
    selectTool(tab.dataset.tool, name);
    return;
  }

  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));

  tab.classList.add("active");
  $("panel-" + name).classList.add("active");

  // trocar de ferramenta volta para onde você estava, não para o começo
  state.lastTab[state.tool] = name;
}

/* -------------------------------------------- entrada × saída na coluna 1 */
function mostrarLado(grupo, lado) {
  const nav = document.querySelector(`.switch[data-switch="${grupo}"]`);
  if (!nav) return;

  nav.querySelectorAll("button").forEach((el) => {
    el.classList.toggle("active", el.dataset.side === lado);
  });

  document.querySelectorAll(`[id^="side-${grupo}-"]`).forEach((el) => {
    el.classList.toggle("active", el.id === `side-${lado}`);
  });
}

document.querySelectorAll(".switch").forEach((nav) => {
  nav.addEventListener("click", (event) => {
    const botao = event.target.closest("button");
    if (botao) mostrarLado(nav.dataset.switch, botao.dataset.side);
  });
});

/* ------------------------------------------------------------ estado geral */
async function loadState() {
  const data = await api("/api/state");

  for (const [id, caminho] of [
    ["input-dir", data.input_dir],
    ["update-input-dir", data.input_dir],
    ["output-dir", data.output_dir],
    ["update-output-dir", data.output_dir],
  ]) {
    setText($(id), caminho);
    $(id).title = caminho;
  }

  // o cache cresce a cada conversão: relido junto com o resto do estado
  loadCacheSize();

  $("btn-quit").classList.toggle("hidden", !data.can_quit);

  const pill = $("api-pill");
  if (data.api_key_configured) {
    pill.textContent = "API do CurseForge configurada";
    pill.className = "pill pill-ok";
  } else {
    pill.textContent = "CURSEFORGE_API_KEY ausente no .env";
    pill.className = "pill pill-bad";
  }

  state.packs = data.packs;

  renderPacks(data.packs);
  renderUpdatePacks(data.packs);
  renderRecords(data.records);
  syncLoaderChoice();

  // reconecta a um job aberto (ex.: recarregou a página no meio da conversão)
  if (data.current_job && data.current_job.id !== state.jobId) {
    state.jobId = data.current_job.id;
    state.logCount = 0;
    $("log").innerHTML = "";
    startPolling();
  } else if (!data.current_job && state.jobId) {
    resetJob();
  }

  if (data.current_update && data.current_update.id !== state.updateJobId) {
    state.updateJobId = data.current_update.id;
    state.updateLogCount = 0;
    $("u-log").innerHTML = "";
    startUpdatePolling();
  } else if (!data.current_update && state.updateJobId) {
    resetUpdateJob();
  }

  updateStartButton();
  updateUpdateButton();
  loadSavedUpdates();
}

/** Os números do resumo, cada um com a sua cor (verde muda, roxo é escolha). */
function resumoAtualizacao(summary) {
  const partes = [
    summary.updated
      ? `<span class="num ok">${summary.updated}</span> atualizados`
      : "",
    summary.manual
      ? `<span class="num picked">${summary.manual}</span> escolhidos`
      : "",
    summary.excluded
      ? `<span class="num warn">${summary.excluded}</span> fora`
      : "",
  ].filter(Boolean);

  return partes.join(" · ") || "<span>sem mudanças</span>";
}

async function loadSavedUpdates() {
  const list = $("update-output-list");

  try {
    const data = await api("/api/updates");

    if (!data.updates.length) {
      setHTML(list, `<p class="hint">Nenhum pack atualizado ainda.</p>`);
      return;
    }

    setHTML(list, data.updates.map((item) => {
      const [loader, versaoLoader] = String(item.loader || "").split("-");
      const trocou = item.from_loader && item.from_loader !== loader;

      return `
        <div class="pack ${state.selectedUpdate === item.name ? "selected" : ""}
                    ${state.freshOutput === item.name ? "fresh" : ""}"
             data-update-file="${esc(item.name)}">
          <div>
            <div class="name">${esc(item.pack.name || item.name)}</div>
            <div class="meta">
              ${mcTag(item.from_minecraft)} <span class="arrow-to">→</span>
              ${mcTag(item.to_minecraft)}
              ${trocou
                ? `${loaderTag(item.from_loader)} <span class="arrow-to">→</span>`
                : ""}
              ${loaderTag(loader, versaoLoader)}
              ${modsTag(item.summary.total)}
            </div>
            <div class="meta">${resumoAtualizacao(item.summary)}</div>
            <div class="meta">
              ${item.available
                ? linhaArquivo(item.size_mb, item.modified)
                : '<span class="warn-text">arquivo ausente</span>'}
            </div>
          </div>
          <span class="hint">›</span>
        </div>`;
    }).join(""));

    list.querySelectorAll("[data-update-file]").forEach((el) => {
      el.addEventListener("click", () => showSavedUpdate(el.dataset.updateFile));
    });
  } catch (_) {
    setHTML(list, `<p class="hint">não foi possível listar</p>`);
  }
}

async function showSavedUpdate(name) {
  const box = $("u-detail");

  // seleção própria: `state.selection` é do conversor, e escolher um pack lá
  // não pode desmarcar o que está aberto aqui
  if (state.freshOutput && state.freshOutput !== name) state.freshOutput = null;

  state.selectedUpdate = name;
  loadSavedUpdates();
  renderUpdateOutputActions(state.detailCache["update:" + name]);

  try {
    const dados = await api(`/api/updates/${encodeURIComponent(name)}`);
    state.detailCache["update:" + name] = dados;
    renderSavedUpdate(box, name, dados);
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderSavedUpdate(box, name, dados) {
  // "fora" é uma decisão, não um status: vem antes do agrupamento por status
  const secoes = [
    { chave: "excluded", rotulo: "deixados de fora do pack", tom: "missing" },
    { chave: "manual", rotulo: "escolhidos por você", tom: "done" },
    { chave: "updated", rotulo: "atualizados", tom: "done" },
    { chave: "incompatible", rotulo: "sem versão, mas incluídos", tom: "version" },
    { chave: "unknown", rotulo: "não identificados, mas incluídos", tom: "version" },
  ];

  const grupos = secoes.map(({ chave, rotulo, tom }) => {
    const itens = (dados.files || [])
      .filter((f) =>
        chave === "excluded" ? f.excluded : !f.excluded && f.status === chave
      )
      .sort((a, b) => a.file_name.localeCompare(b.file_name));

    if (!itens.length) return "";

    return `
      <div class="decision-group">
        <h3>
          <span class="tag ${tom}">${esc(rotulo)}</span>
          <span class="count">${itens.length}</span>
        </h3>
        <div class="decision-list">
          ${itens.map((file) => `
            <div class="decision">
              <div class="line">
                <span class="label">mod</span>
                <span class="value">${esc(file.modrinth_title || file.file_name)}</span>
              </div>
              ${file.to_version ? `
                <div class="line">
                  <span class="label">versão</span>
                  <span class="value mono ok">${esc(file.from_version || "?")} → ${esc(file.to_version)}</span>
                </div>` : `
                <div class="line">
                  <span class="label">arquivo</span>
                  <span class="value mono dim">${esc(file.file_name)}</span>
                </div>`}
            </div>
          `).join("")}
        </div>
      </div>`;
  }).join("");

  setHTML(box, `
    <h2>${esc(dados.pack?.name || name)}</h2>
    <p class="detail-title mono">${esc(name)}</p>

    <div class="kv-list">
      ${statRow("Minecraft", esc(`${dados.from_minecraft} → ${dados.to_minecraft}`))}
      ${statRow("Loader", esc(dados.loader || "?"))}
      ${statRow("Arquivos", dados.summary?.total ?? "?")}
      ${statRow("Atualizados", dados.summary?.updated ?? 0, "ok")}
      ${statRow("Escolhas manuais", dados.summary?.manual ?? 0)}
      ${statRow("Sem versão", dados.summary?.incompatible ?? 0, "warn")}
      ${statRow("Não identificados", dados.summary?.unknown ?? 0, "bad")}
    </div>

    <p class="section-label">decisões</p>
    ${grupos || `<p class="hint">Nada fora do automático.</p>`}
  `);

  // as ações ficam no rodapé da coluna da esquerda, não aqui
  renderUpdateOutputActions(dados);
}

function updateStartButton() {
  const busy = !!state.jobId;
  $("btn-convert").disabled = !state.selectedPack || busy;
  $("selected-label").textContent = busy
    ? "feche o trabalho aberto para iniciar outro"
    : state.selectedPack || "nenhum modpack selecionado";
}

/** Minecraft, loader e nº de mods — o que decide se o pack serve para o que
    você quer fazer, sem precisar abrir o detalhe. */
/* ------------------------------------------------- tags de versão e loader */
/* Cada loader tem a sua cor, e as versões do Minecraft formam um gradiente do
   mais novo (azul) ao mais antigo (vermelho). São as mesmas tags na lista de
   entrada e nos conflitos: bater o olho e reconhecer vale mais que ler. */

const LOADER_TONE = {
  fabric: "l-fabric",
  quilt: "l-quilt",
  forge: "l-forge",
  neoforge: "l-neoforge",
};

/** `1.21.11` -> matiz; mais novo puxa para o azul, mais antigo para o vermelho. */
function mcHue(version) {
  const menor = Number(String(version || "").split(".")[1]);
  if (!Number.isFinite(menor)) return null;

  // 1.21 e acima = 210 (azul); cada versão menor desce 18°, até 0 (vermelho)
  return Math.max(0, Math.min(210, 210 - (21 - menor) * 18));
}

function mcTag(version, extra) {
  if (!version) return "";

  const matiz = mcHue(version);
  const estilo =
    matiz === null
      ? ""
      : ` style="color:hsl(${matiz} 75% 68%);border-color:hsl(${matiz} 40% 30%);` +
        `background:hsl(${matiz} 40% 12%)"`;

  return `<span class="tag mc${extra ? " " + extra : ""}"${estilo}>${esc(version)}</span>`;
}

function loaderTag(loader, version) {
  if (!loader) return "";

  const tom = LOADER_TONE[String(loader).toLowerCase()] || "";
  const texto = [loader, version].filter(Boolean).join(" ");

  return `<span class="tag loader ${tom}">${esc(texto)}</span>`;
}

/** As versões do Minecraft de um arquivo, como tags (as mais novas primeiro). */
function mcTags(versions, alvo, limite = 4) {
  const lista = (versions || []).filter((v) => /^\d+\.\d+/.test(v));
  const loaders = (versions || []).filter((v) => LOADER_TONE[v.toLowerCase()]);

  // a que interessa vem primeiro, mesmo que não esteja entre as mais novas
  const ordenadas = [
    ...lista.filter((v) => v === alvo),
    ...lista.filter((v) => v !== alvo),
  ];

  const extras = ordenadas.length - limite;

  return [
    ...ordenadas.slice(0, limite).map((v) => mcTag(v, v === alvo ? "is-target" : "")),
    extras > 0 ? `<span class="tag more">+${extras}</span>` : "",
    ...loaders.map((l) => loaderTag(l)),
  ]
    .filter(Boolean)
    .join(" ");
}

/** A linha de tags de um card: Minecraft, loader e quantos mods. */
function packMeta(pack) {
  if (!pack.minecraft) return `<span class="hint">índice ilegível</span>`;

  return [
    mcTag(pack.minecraft),
    loaderTag(pack.loader, pack.loader_version),
    modsTag(pack.mods),
  ]
    .filter(Boolean)
    .join(" ");
}

const modsTag = (quantos) =>
  quantos == null ? "" : `<span class="tag mods">${quantos} mods</span>`;

/**
 * A última linha de todo card de lista: tamanho e data.
 *
 * É a mesma nos três (entrada, conversões salvas, packs atualizados) — foi o
 * que fez as colunas pararem de ter formas diferentes conforme o lado.
 */
const linhaArquivo = (sizeMb, quando) =>
  [sizeMb ? `${sizeMb} MB` : null, quando ? fmtDate(quando) : null]
    .filter(Boolean)
    .join(" · ") || "gerado sob demanda";

/**
 * O `.mrpack` que está sendo processado agora.
 *
 * `awaiting_conflicts`/`awaiting_review` contam: o trabalho não terminou, só
 * está esperando você. O verde só sai quando o arquivo fica pronto (ou o
 * trabalho morre).
 */
function packEmCurso(job) {
  const andando = [
    "queued",
    "running",
    "finishing",
    "awaiting_conflicts",
    "awaiting_review",
  ];

  return job && andando.includes(job.status) ? job.source : null;
}

/**
 * Ordem da lista de entrada: o que está rodando agora, depois o que você
 * converteu/atualizou mais recentemente, e por fim o resto por nome.
 */
function ordenarPacks(packs, emCurso, doJob) {
  const peso = (pack) => {
    if (pack.name === emCurso) return 0;
    if (pack.name === doJob) return 1;
    return 2;
  };

  return [...packs].sort((a, b) => {
    const diff = peso(a) - peso(b);
    if (diff) return diff;

    const usoA = a.last_used || 0;
    const usoB = b.last_used || 0;
    if (usoA !== usoB) return usoB - usoA;

    return a.name.localeCompare(b.name);
  });
}

function renderPacks(packs) {
  const list = $("pack-list");
  packs = packs || [];

  if (!packs.length) {
    list.innerHTML = `<p class="hint">Nenhum .mrpack na pasta ainda.</p>`;
    return;
  }

  const emCurso = packEmCurso(state.job);
  const doJob = state.job ? state.job.source : null;

  list.innerHTML = ordenarPacks(packs, emCurso, doJob).map((pack) => `
    <div class="pack ${state.selectedPack === pack.name ? "selected" : ""}
                ${pack.name === emCurso ? "working" : ""}
                ${pack.name === doJob ? "in-job" : ""}"
         data-pack="${esc(pack.name)}">
      <div>
        <div class="name">
          ${pack.name === emCurso ? `<span class="spinner"></span>` : ""}
          ${esc(pack.name)}
          ${pack.name === doJob && !emCurso
            ? `<span class="tag in-job-tag">na conversão aberta</span>`
            : ""}
        </div>
        <div class="meta">${packMeta(pack)}</div>
        <div class="meta">${pack.size_mb} MB · ${fmtDate(pack.modified)}</div>
      </div>
      <span class="hint">›</span>
    </div>
  `).join("");

  list.querySelectorAll(".pack").forEach((el) => {
    el.addEventListener("click", () => selectPack(el.dataset.pack));
  });
}

function renderRecords(records) {
  const list = $("record-list");
  state.records = records;
  renderRecordActions();

  if (!records || !records.length) {
    list.innerHTML = `<p class="hint">Nenhuma conversão salva ainda.</p>`;
    return;
  }

  list.innerHTML = records.map((record) => {
    const pack = record.pack || {};
    const summary = record.summary || {};

    return `
      <div class="pack ${isSelected("record", record.id) ? "selected" : ""}
                  ${state.freshOutput === record.id ? "fresh" : ""}"
           data-record="${esc(record.id)}">
        <div>
          <div class="name">${esc(pack.name || record.id)} ${esc(pack.version || "")}</div>
          <div class="meta">
            ${mcTag(pack.minecraft)}
            ${loaderTag(...String(pack.loader || "").split("-"))}
            ${modsTag(summary.total_mods)}
            <span><span class="num ok">${summary.matched}</span> no manifest</span>
          </div>
          <div class="meta">
            ${linhaArquivo(record.size_mb, record.updated_at)}
            ${record.source_available ? "" : ' · <span class="warn-text">origem ausente</span>'}
          </div>
        </div>
        <span class="hint">›</span>
      </div>
    `;
  }).join("");

  list.querySelectorAll("[data-record]").forEach((el) => {
    el.addEventListener("click", () => selectDetail("record", el.dataset.record));
  });
}

function isSelected(kind, id) {
  return state.selection && state.selection.kind === kind && state.selection.id === id;
}

function selectPack(name) {
  state.selectedPack = name;
  state.selectedUpdate = null;

  // um trabalho cancelado ou com erro não produziu nada: mantê-lo aberto só
  // deixava o painel dizendo "cancelada" enquanto você já tinha seguido adiante
  fecharJobMorto();

  // `renderPacks` já redesenha com a classe certa
  renderPacks(state.packs || []);
  updateStartButton();
  selectDetail("input", name);
}

/**
 * Fecha, sem perguntar, um trabalho terminal que não gerou arquivo nenhum.
 *
 * Cancelado ou com erro não deixa nada para trás, mas continua ocupando a vaga
 * até alguém clicar em Fechar — e o painel segue mostrando "cancelada" enquanto
 * o usuário já escolheu outro pack.
 */
async function fecharJobMorto() {
  const job = state.job;
  if (!job || !["cancelled", "error"].includes(job.status)) return;

  const id = state.jobId;
  resetJob();

  try {
    await post(`/api/jobs/${id}/close`);
  } catch (_) {}

  await loadState();
}

async function fecharUpdateMorto() {
  const job = state.updateJob;
  if (!job || !["cancelled", "error"].includes(job.status)) return;

  const id = state.updateJobId;
  resetUpdateJob();

  try {
    await post(`/api/jobs/${id}/close`);
  } catch (_) {}

  await loadState();
}

/* ------------------------------------------------------------------ upload */
const dropzone = $("dropzone");

["dragenter", "dragover"].forEach((type) =>
  dropzone.addEventListener(type, (e) => { e.preventDefault(); dropzone.classList.add("drag"); })
);

["dragleave", "drop"].forEach((type) =>
  dropzone.addEventListener(type, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); })
);

dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});

$("file-input").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) uploadFile(file);
});

function uploadFile(file) {
  if (!file.name.toLowerCase().endsWith(".mrpack")) {
    toast("Envie um arquivo .mrpack", "error");
    return;
  }

  const form = new FormData();
  form.append("file", file);

  const request = new XMLHttpRequest();
  request.open("POST", "/api/upload");
  $("upload-progress").classList.remove("hidden");

  request.upload.onprogress = (event) => {
    if (!event.lengthComputable) return;
    $("upload-fill").style.width = ((event.loaded / event.total) * 100).toFixed(1) + "%";
    $("upload-text").textContent =
      `${(event.loaded / 1048576).toFixed(0)} / ${(event.total / 1048576).toFixed(0)} MB`;
  };

  request.onload = async () => {
    $("upload-progress").classList.add("hidden");
    $("upload-fill").style.width = "0%";

    if (request.status >= 400) {
      let detail = "falha no upload";
      try { detail = JSON.parse(request.responseText).detail; } catch (_) {}
      toast(detail, "error");
      return;
    }

    const data = JSON.parse(request.responseText);
    await loadState();
    selectPack(data.name);
    toast(`${data.name} enviado`, "ok");
  };

  request.onerror = () => {
    $("upload-progress").classList.add("hidden");
    toast("falha no upload", "error");
  };

  request.send(form);
}

/* ---------------------------------------------------------------- conversão */
$("btn-convert").addEventListener("click", async () => {
  if (!state.selectedPack) return;

  // um trabalho cancelado ainda ocupa a vaga: sem isto a próxima conversão
  // levaria 409 e o painel continuaria mostrando o cancelamento
  await fecharJobMorto();

  $("btn-convert").disabled = true;
  clearJobState();

  try {
    const job = await api("/api/convert", json({ file: state.selectedPack }));
    state.jobId = job.id;
    startPolling();
  } catch (error) {
    toast(error.message, "error");
    updateStartButton();
  }
});

function clearJobState() {
  $("log").innerHTML = "";
  state.logCount = 0;
  state.conflicts = [];
  state.pending = {};
  state.fileCache = {};
  state.projectCache = {};
  state.searchCache = {};
  state.openConflict = null;
  state.confirming = false;
  renderConflicts();
}

function startPolling() {
  stopPolling();
  state.polling = setInterval(pollJob, 600);
  pollJob();
}

function stopPolling() {
  if (state.polling) clearInterval(state.polling);
  state.polling = null;
}

async function pollJob() {
  if (!state.jobId) return;

  let job;
  try {
    job = await api(`/api/jobs/${state.jobId}?log_offset=${state.logCount}`);
  } catch (error) {
    stopPolling();
    resetJob();
    return;
  }

  const previous = state.job && state.job.id === job.id ? state.job.status : undefined;
  state.job = job;

  appendLogs(job.logs, $("log"));
  state.logCount = job.log_count;

  renderJob();

  // sem isto o destaque do pack em processamento só aparecia no próximo
  // clique, e a aba de conflitos só ficava cinza depois de você mexer nela
  if (job.status !== previous) {
    renderPacks(state.packs || []);
    renderConflicts();
  }

  if (job.status !== previous) {
    if (job.status === "awaiting_conflicts") {
      await loadConflicts();
      toast(`${job.unresolved} conflito(s) aguardando você`, "ok");
    }

    if (job.status === "done") {
      await loadConflicts();
      await loadState();

      // acabou: a coluna passa a mostrar o resultado, já selecionado
      if (job.record_id) {
        // fica verde até você escolher outra coisa: é onde o resultado apareceu
        state.freshOutput = job.record_id;
        mostrarLado("convert", "convert-out");
        await selectDetail("record", job.record_id);
      }
    }
  }

  // pausado ou terminado: nada muda sozinho, então para de consultar
  // (o polling contínuo era o que fazia os botões piscarem)
  if (["done", "error", "cancelled", "awaiting_conflicts"].includes(job.status)) {
    stopPolling();
  }
}

function resetJob() {
  stopPolling();
  state.jobId = null;
  state.job = null;
  state.conflicts = [];
  state.pending = {};
  state.confirming = false;
  renderJob();
  renderConflicts();
  updateStartButton();
  leaveEmptyTab("conflicts", "convert");
}

/** Sem job não há o que revisar: a aba fica vazia e o usuário preso nela. */
function leaveEmptyTab(aba, destino) {
  const atual = document.querySelector(".tab.active");
  if (atual && atual.dataset.tab === aba) goToTab(destino);
}

function appendLogs(logs, target) {
  if (!logs || !logs.length) return;

  const log = target || $("log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;

  logs.forEach((entry) => {
    const div = document.createElement("div");

    if (entry.parts && entry.parts.length > 1) {
      // linha com mais de uma cor (o resumo): cada trecho no seu tom
      entry.parts.forEach((part) => {
        const span = document.createElement("span");
        span.className = part.level;
        span.textContent = part.text;
        div.appendChild(span);
      });
    } else {
      div.className = entry.level;
      div.textContent = entry.text;
    }

    log.appendChild(div);
  });

  if (atBottom) log.scrollTop = log.scrollHeight;
}

const STATUS_LABEL = {
  queued: "na fila",
  running: "procurando mods",
  awaiting_conflicts: "aguardando conflitos",
  finishing: "baixando e empacotando",
  done: "concluída",
  cancelled: "cancelada",
  error: "erro",
};

const STATUS_PILL = {
  running: "pill-warn",
  queued: "pill-warn",
  finishing: "pill-warn",
  awaiting_conflicts: "pill-warn",
  done: "pill-ok",
  cancelled: "pill-muted",
  error: "pill-bad",
};

/** Conflitos ainda sem escolha, considerando o que está pendente na tela. */
function unresolvedCount() {
  if (state.conflicts.length) {
    return state.conflicts.filter((c) => !effectiveResolution(c)).length;
  }
  return state.job ? state.job.unresolved || 0 : 0;
}

function renderJob() {
  const job = state.job;

  if (!job) {
    $("conversion-body").classList.add("hidden");
    $("conversion-empty").classList.remove("hidden");
    $("job-status").textContent = "nenhuma aberta";
    $("job-status").className = "pill pill-muted";
    $("tab-conflicts-badge").textContent = "0";
    $("tab-conflicts-badge").className = "badge";
    return;
  }

  $("conversion-empty").classList.add("hidden");
  $("conversion-body").classList.remove("hidden");
  setText($("job-source"), job.source);
  setText($("job-status"), STATUS_LABEL[job.status] || job.status);
  setClass($("job-status"), "pill " + (STATUS_PILL[job.status] || "pill-muted"));

  setText($("stage-label"), job.stage || STATUS_LABEL[job.status] || "");

  const fill = $("progress-fill");
  const running = ["running", "finishing", "queued"].includes(job.status);

  if (job.total > 0) {
    fill.classList.remove("indeterminate");
    fill.style.width = ((job.done / job.total) * 100).toFixed(1) + "%";
    setText($("counter-label"), `${job.done} / ${job.total}`);
  } else if (running) {
    fill.classList.add("indeterminate");
    setText($("counter-label"), "");
  } else {
    fill.classList.remove("indeterminate");
    fill.style.width = job.status === "done" ? "100%" : "0%";
    setText($("counter-label"), "");
  }

  renderNotice(job);
  renderActions(job);
  renderConfirm(job);
}

function renderNotice(job) {
  const notice = $("job-notice");
  const unsaved = Object.keys(state.pending).length;
  const unresolved = unresolvedCount();

  if (job.status === "awaiting_conflicts") {
    // enquanto houver escolha não salva o aviso continua amarelo: ainda falta
    // uma ação sua, mesmo que todos os conflitos já tenham sido decididos
    const pendente = unresolved > 0 || unsaved > 0;
    const aviso = unsaved
      ? `<div class="unsaved">⚠ ${unsaved} alteração(ões) ainda não salva(s) — clique em <strong>Salvar mudanças</strong> na aba Conflitos.</div>`
      : "";

    setClass(notice, "notice " + (pendente ? "warn" : "ok"));

    if (unresolved > 0) {
      setHTML(notice, `
        <strong>Pausado antes dos downloads.</strong>
        ${unresolved} mod(s) sem escolha seriam baixados do Modrinth para
        <span class="mono">overrides/mods</span>.
        Resolva o que quiser na aba <strong>Conflitos</strong>, salve, e volte aqui
        para <strong>Aplicar mudanças</strong> — seguir sem resolver também funciona.
        ${aviso}
      `);
    } else {
      setHTML(notice, `
        <strong>Todos os conflitos foram decididos.</strong>
        ${unsaved
          ? "Salve as mudanças para poder aplicar."
          : "Clique em <strong>Aplicar mudanças</strong> para baixar o que falta e gerar o modpack."}
        ${aviso}
      `);
    }

    notice.classList.remove("hidden");
    return;
  }

  if (job.status === "error") {
    setClass(notice, "notice bad");
    setText(notice, job.error || "erro desconhecido");
    notice.classList.remove("hidden");
    return;
  }

  if (job.status === "cancelled") {
    setClass(notice, "notice warn");
    setText(notice, "Conversão cancelada. Nada foi gerado.");
    notice.classList.remove("hidden");
    return;
  }

  if (job.status === "done" && job.report) {
    setClass(notice, "notice ok");
    setHTML(notice, `
      <strong>${job.report.matched}</strong> mods no manifest ·
      <strong>${job.report.overrides}</strong> em overrides
      ${job.output ? `· ${esc(job.output.name)} (${job.output.size_mb} MB)` : ""}
      <div class="hint">
        Ao fechar, o <span class="mono">.zip</span> é apagado — o registro fica salvo
        e dá para gerar o modpack de novo quando quiser.
      </div>
    `);
    notice.classList.remove("hidden");
    return;
  }

  notice.classList.add("hidden");
}

function renderActions(job) {
  const actions = $("job-actions");
  const buttons = [];
  const unresolved = unresolvedCount();

  if (["queued", "running", "finishing"].includes(job.status)) {
    buttons.push(
      `<button class="btn ${armados.cancel ? "btn-danger" : ""}" data-act="cancel">
         ${rotuloArmado("cancel", "Cancelar conversão")}</button>`
    );
  }

  if (job.status === "awaiting_conflicts") {
    buttons.push(
      `<button class="btn" data-act="conflicts">Resolver conflitos (${unresolved})</button>`,
      `<button class="btn btn-primary" data-act="apply">Aplicar mudanças</button>`,
      `<button class="btn ${armados.cancel ? "btn-danger" : ""}" data-act="cancel">
         ${rotuloArmado("cancel", "Cancelar conversão")}</button>`
    );
  }

  if (job.status === "done") {
    if (job.output) {
      buttons.push(
        `<a class="btn btn-primary" href="/api/jobs/${job.id}/download">⬇ Baixar modpack</a>`,
        `<a class="btn btn-ghost" href="/api/jobs/${job.id}/report">Baixar registro (JSON)</a>`
      );
    }
    buttons.push(`<button class="btn btn-ghost" data-act="close">Fechar conversão</button>`);
  }

  if (["cancelled", "error"].includes(job.status)) {
    buttons.push(`<button class="btn btn-ghost" data-act="close">Fechar conversão</button>`);
  }

  // só reescreve (e reassina os eventos) se os botões realmente mudaram
  if (setHTML(actions, buttons.join(""))) {
    actions.querySelectorAll("[data-act]").forEach((el) => {
      el.addEventListener("click", () => runAction(el.dataset.act));
    });
  }
}

function renderConfirm(job) {
  const box = $("apply-confirm");

  // o painel só existe enquanto dá para aplicar: cancelar ou terminar fecha ele
  if (!state.confirming || !job.plan || job.status !== "awaiting_conflicts") {
    state.confirming = false;
    box.classList.add("hidden");
    return;
  }

  // linhas curtas de propósito: o popup tem de caber sem rolar
  const plan = job.plan;
  const baixar = plan.downloads + plan.extra_files;

  const lines = [
    `<li><strong>${plan.manifest}</strong> no manifest${
      plan.manual ? ` (${plan.manual} escolhidos por você)` : ""
    }</li>`,
    `<li><strong>${baixar}</strong> baixados para <span class="mono">overrides/</span></li>`,
  ];

  if (plan.override_files) {
    lines.push(
      `<li><strong>${plan.override_files}</strong> copiados do
       <span class="mono">overrides/</span> original</li>`
    );
  }

  const changed = setHTML(box, `
    <h3>O que vai acontecer</h3>
    <ul>${lines.join("")}</ul>
    <div class="row">
      <button class="btn btn-primary" data-confirm="go">Continuar</button>
      <button class="btn btn-ghost" data-confirm="back">Voltar</button>
    </div>
  `);
  box.classList.remove("hidden");

  if (!changed) return;

  box.querySelectorAll("[data-confirm]").forEach((el) => {
    el.addEventListener("click", async () => {
      state.confirming = false;
      box.classList.add("hidden");

      if (el.dataset.confirm === "back") {
        renderJob();
        return;
      }

      try {
        await post(`/api/jobs/${state.jobId}/apply`);
        startPolling();
      } catch (error) {
        toast(error.message, "error");
        renderJob();
      }
    });
  });
}

async function runAction(action) {
  if (action === "conflicts") return goToTab("conflicts");

  if (action === "apply") {
    // o que está na tela conta: aplicar sem salvar perderia as escolhas
    if (Object.keys(state.pending).length) {
      if ((await saveConflictResolutions()) === null) return;
    }

    state.confirming = true;
    renderJob();
    return;
  }

  if (action === "cancel") {
    // cancelar joga fora o trabalho já feito: pede confirmação como o Encerrar
    if (!armarBotao("cancel", "Cancelar mesmo?")) return;

    state.confirming = false;
    try {
      await post(`/api/jobs/${state.jobId}/cancel`);
      startPolling();
    } catch (error) {
      toast(error.message, "error");
    }
    return;
  }

  if (action === "close") {
    try {
      await post(`/api/jobs/${state.jobId}/close`);
    } catch (_) {}

    resetJob();
    await loadState();
  }
}

/**
 * Está gerando o arquivo? Mexer nas decisões agora não teria efeito: o backend
 * já recebeu as que valem, e o que fosse clicado se perderia em silêncio.
 */
function jobGerando(job) {
  return !!job && job.status === "finishing";
}

/** Deixa a aba de revisão só de leitura enquanto o pack é gerado. */
function travarRevisao(painel, gerando) {
  painel.classList.toggle("locked", gerando);
}

/* ---------------------------------------------------------------- conflitos */
async function loadConflicts() {
  if (!state.jobId) return;

  const data = await api(`/api/jobs/${state.jobId}/conflicts`);
  state.conflicts = data.conflicts;
  renderConflicts();
}

/** Estado efetivo de um conflito: escolha pendente > salva. */
function effectiveResolution(conflict) {
  if (Object.prototype.hasOwnProperty.call(state.pending, conflict.file_name)) {
    return state.pending[conflict.file_name];
  }
  return conflict.resolution;
}

const SECTIONS = [
  {
    key: "missing",
    title: "Sem equivalente",
    tag: "missing",
    hint: "nenhum projeto parecido no CurseForge — busque à mão ou deixe ir para overrides",
    match: (c) => !effectiveResolution(c) && c.reason !== "version-unavailable",
  },
  {
    key: "version",
    title: "Versão indisponível",
    tag: "version",
    hint: "o projeto está no CurseForge, mas sem a versão exata do pack — escolha outra ou deixe ir para overrides",
    match: (c) => !effectiveResolution(c) && c.reason === "version-unavailable",
  },
  {
    key: "resolvidos",
    title: "Resolvidos",
    tag: "done",
    hint: "vão para o manifest quando você aplicar as mudanças",
    match: (c) => !!effectiveResolution(c),
  },
];

function renderConflicts() {
  const container = $("conflict-groups");
  const total = state.conflicts.length;
  const unresolved = unresolvedCount();
  const unsaved = Object.keys(state.pending).length;

  $("tab-conflicts-badge").textContent = unresolved;
  $("tab-conflicts-badge").className = "badge" + (unresolved ? " hot" : "");

  if (!total) {
    $("conflicts-empty").classList.remove("hidden");
    $("conflicts-summary").classList.add("hidden");
    // por setHTML, senão o cache dele fica achando que o markup antigo está lá
    setHTML(container, "");
    return;
  }

  $("conflicts-empty").classList.add("hidden");
  $("conflicts-summary").classList.remove("hidden");

  // o alvo do pack fica à vista: só serve versão do mesmo Minecraft e loader
  // o alvo do pack, com as mesmas tags da lista de entrada
  const target = packTarget();
  const pill = $("mc-pill");
  const report = state.job && state.job.report;

  setHTML(
    pill,
    mcTag(target.mc) +
      " " +
      loaderTag(target.loader, (report ? report.loader : "").split("-")[1])
  );
  pill.classList.toggle("hidden", !target.mc && !target.loader);

  $("conflicts-hint").innerHTML =
    `${total - unresolved} de ${total} resolvidos. O que ficar sem escolha vai ` +
    `para <span class="mono">overrides/mods</span> — o modpack funciona igual.` +
    (unsaved
      ? ` <span class="unsaved-inline">${unsaved} alteração(ões) não salva(s)</span>`
      : "");

  const gerando = jobGerando(state.job);

  $("btn-save").disabled = unsaved === 0 || gerando;
  $("btn-discard").disabled = unsaved === 0 || gerando;
  travarRevisao(container, gerando);

  // reescrever às cegas a cada poll destruiria a busca aberta num card
  const markup = SECTIONS.map((section) => {
    const items = state.conflicts.filter(section.match);

    return `
      <section class="conflict-section ${items.length ? "" : "empty"}">
        <header>
          <span class="tag ${section.tag}">${esc(section.title)}</span>
          <span class="badge">${items.length}</span>
          <p class="hint">${esc(section.hint)}</p>
        </header>
        ${items.length
          ? items.map(renderConflictCard).join("")
          : `<p class="hint">nenhum</p>`}
      </section>
    `;
  }).join("");

  if (setHTML(container, markup)) bindConflictEvents();
  autoLoadOpenConflict();
}

function renderConflictCard(conflict) {
  const open = state.openConflict === conflict.file_name;
  const resolution = effectiveResolution(conflict);
  const dirty = Object.prototype.hasOwnProperty.call(state.pending, conflict.file_name);

  const similarity = conflict.similarity != null && !resolution
    ? `<span class="hint">${Math.round(conflict.similarity * 100)}% de semelhança</span>`
    : "";

  const chosen = resolution
    ? `<span class="conflict-chosen hint">
         <span class="arrow-to">→</span>
         <span class="value mono">${esc(resolution.file_name || "arquivo #" + resolution.file_id)}${dirty ? " (não salvo)" : ""}</span>
       </span>`
    : "";

  let body = modrinthReference(conflict);

  if (resolution) {
    body += `
      <p class="section-label">escolha atual</p>
      <div class="file-row match">
        <div>
          <div class="name mono">${esc(resolution.file_name || "arquivo #" + resolution.file_id)}</div>
          <div class="file-meta"><span>${esc(resolution.project_name || "projeto #" + resolution.project_id)}</span></div>
        </div>
      </div>
      <p class="section-label">trocar</p>
      ${searchBoxHtml(conflict)}
      <div data-panel="${esc(conflict.file_name)}"></div>
    `;
  } else if (conflict.reason === "version-unavailable" && conflict.suggestion) {
    body += `
      <p class="hint">
        Detectado como <strong>${esc(conflict.suggestion.project_name)}</strong>.
        Mais próxima lá: <span class="mono">${esc(conflict.suggestion.closest_file_name)}</span>.
      </p>
      <div class="row">
        <button class="btn btn-sm" data-search-toggle="${esc(conflict.file_name)}">Procurar outro projeto</button>
      </div>
      <div class="search-box hidden" data-searchbox="${esc(conflict.file_name)}">
        ${searchBoxHtml(conflict)}
      </div>
      <div data-panel="${esc(conflict.file_name)}"></div>
    `;
  } else {
    body += `
      <p class="hint">
        Buscas automáticas tentadas:
        <span class="mono">${esc((conflict.queries_tried || []).join(" · "))}</span>
      </p>
      ${searchBoxHtml(conflict)}
      <div data-panel="${esc(conflict.file_name)}"></div>
    `;
  }

  return `
    <div class="conflict ${resolution ? "resolved" : ""} ${conflict.reason === "not-on-curseforge" && !resolution ? "missing" : ""}">
      <div class="conflict-head" data-toggle="${esc(conflict.file_name)}">
        <div class="conflict-title">
          <span class="conflict-file mono">${esc(conflict.file_name)}</span>
          ${similarity}
          ${chosen}
        </div>
        <div class="row">
          ${resolution ? `<button class="btn btn-sm" data-unresolve="${esc(conflict.file_name)}">desfazer</button>` : ""}
          <span class="hint">${open ? "▲" : "▼"}</span>
        </div>
      </div>
      <div class="conflict-body ${open ? "" : "collapsed"}">${body}</div>
    </div>
  `;
}

/** O mod original, do Modrinth — a referência para comparar com o CurseForge. */
function modrinthReference(conflict) {
  const modrinth = conflict.modrinth;

  const icon = modrinth && modrinth.icon
    ? `<img class="logo-thumb" src="${esc(modrinth.icon)}" alt="" loading="lazy">`
    : `<span class="logo-thumb placeholder">${esc(
        (modrinth && modrinth.title ? modrinth.title : conflict.file_name).trim()[0]
      )}</span>`;

  const titulo = modrinth && modrinth.title ? modrinth.title : "(não identificado)";

  return `
    <div class="mod-reference">
      ${icon}
      <div class="info">
        <div class="name">${esc(titulo)}</div>
        <div class="file-meta mono">${esc(conflict.file_name)}</div>
        ${modrinth && modrinth.url
          ? `<a class="hint" href="${esc(modrinth.url)}" target="_blank" rel="noreferrer noopener">ver no Modrinth ↗</a>`
          : ""}
      </div>
      <span class="tag">no seu pack</span>
    </div>
  `;
}

function searchBoxHtml(conflict) {
  const suggestion = conflict.modrinth_title || conflict.file_name.replace(/\.jar.*/i, "");
  return `
    <div class="row">
      <input class="input" data-searchinput="${esc(conflict.file_name)}" value="${esc(suggestion)}" placeholder="nome do mod">
      <button class="btn btn-sm" data-search="${esc(conflict.file_name)}">Buscar de novo</button>
    </div>
  `;
}

function bindConflictEvents() {
  const list = $("conflict-groups");

  // o cabeçalho inteiro abre/fecha o card (menos os botões dentro dele)
  list.querySelectorAll("[data-toggle]").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;

      const name = el.dataset.toggle;
      state.openConflict = state.openConflict === name ? null : name;
      renderConflicts();
    });
  });

  list.querySelectorAll("[data-search]").forEach((el) => {
    el.addEventListener("click", () => runSearch(el.dataset.search, true));
  });

  list.querySelectorAll("[data-searchinput]").forEach((el) => {
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter") runSearch(el.dataset.searchinput, true);
    });
  });

  list.querySelectorAll("[data-search-toggle]").forEach((el) => {
    el.addEventListener("click", () => {
      const box = list.querySelector(`[data-searchbox="${cssEscape(el.dataset.searchToggle)}"]`);
      if (box) box.classList.toggle("hidden");
    });
  });

  list.querySelectorAll("[data-unresolve]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.stopPropagation();
      state.pending[el.dataset.unresolve] = null;
      renderConflicts();
      renderJob();
    });
  });
}

/** Abrir um conflito já traz as opções, sem precisar clicar em "buscar". */
function autoLoadOpenConflict() {
  const name = state.openConflict;
  if (!name) return;

  const panel = panelFor(name);
  if (!panel || panel.innerHTML.trim()) return;

  const conflict = state.conflicts.find((c) => c.file_name === name);
  if (!conflict) return;

  if (
    !effectiveResolution(conflict) &&
    conflict.reason === "version-unavailable" &&
    conflict.suggestion
  ) {
    showFiles(name, conflict.suggestion.project_id);
  } else {
    runSearch(name);
  }
}

const panelFor = (fileName) =>
  $("conflict-groups").querySelector(`[data-panel="${cssEscape(fileName)}"]`);

/* -------------------------------------------- compatibilidade e ordenação */
const LOADERS = ["fabric", "forge", "neoforge", "quilt"];
const RELEASE_ORDER = { release: 0, beta: 1, alpha: 2 };

/** Minecraft e loader que o modpack exige. */
function packTarget() {
  const report = state.job && state.job.report;
  if (!report) return { mc: null, loader: null };

  return {
    mc: (report.minecraft_version || "").toLowerCase() || null,
    loader: (report.loader || "").split("-")[0].toLowerCase() || null,
  };
}

const fileTags = (file) => (file.game_versions || []).map((v) => v.toLowerCase());

/** Compatível = mesma versão do Minecraft **e** mesmo loader do modpack. */
function isCompatible(file, target) {
  const tags = fileTags(file);

  if (target.mc && !tags.includes(target.mc)) return false;

  if (target.loader) {
    const declared = LOADERS.filter((loader) => tags.includes(loader));
    // arquivo que não declara loader (datapack, resourcepack) não é descartado
    if (declared.length && !declared.includes(target.loader)) return false;
  }

  return true;
}

/** Mesmo arquivo, ignorando `.disabled` e diferenças de escrita. */
function mesmoArquivo(a, b) {
  return !!a && !!b && normalizeFileName(a) === normalizeFileName(b);
}

function normalizeFileName(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/\.disabled$/, "")
    .replace(/\.(jar|zip)$/, "")
    .replace(/%2b/g, "+")
    .replace(/%20/g, " ")
    .replace(/[\s_]+/g, " ")
    .trim();
}

/** Maior versão do Minecraft declarada no arquivo, como número comparável. */
function versionScore(file) {
  let best = 0;

  fileTags(file).forEach((tag) => {
    const match = tag.match(/^(\d+)\.(\d+)(?:\.(\d+))?$/);
    if (!match) return;

    const value =
      Number(match[1]) * 1e6 + Number(match[2]) * 1e3 + Number(match[3] || 0);
    if (value > best) best = value;
  });

  return best;
}

/**
 * Ordena: arquivo idêntico ao procurado, depois compatíveis, depois versão do
 * Minecraft (mais nova primeiro), loader do pack e por fim release > beta > alpha.
 */
function compareFiles(a, b, target, wanted) {
  const key = (file) => [
    normalizeFileName(file.file_name) === wanted ? 0 : 1,
    isCompatible(file, target) ? 0 : 1,
    -versionScore(file),
    target.loader && fileTags(file).includes(target.loader) ? 0 : 1,
    RELEASE_ORDER[file.release_type] ?? 3,
  ];

  const left = key(a);
  const right = key(b);

  for (let i = 0; i < left.length; i += 1) {
    if (left[i] !== right[i]) return left[i] - right[i];
  }

  return 0;
}

function logoHtml(project) {
  if (project && project.logo) {
    return `<img class="logo-thumb" src="${esc(project.logo)}" alt="" loading="lazy">`;
  }
  const initial = project && project.name ? project.name.trim()[0] : "?";
  return `<span class="logo-thumb placeholder">${esc(initial)}</span>`;
}

async function runSearch(fileName, force) {
  const list = $("conflict-groups");
  const input = list.querySelector(`[data-searchinput="${cssEscape(fileName)}"]`);
  const panel = panelFor(fileName);
  if (!input || !panel) return;

  const query = input.value.trim();
  if (!query) return;

  const cached = state.searchCache[fileName];

  if (!force && cached && cached.query === query) {
    renderSearchResults(panel, fileName, cached.results);
    return;
  }

  panel.innerHTML = `<p class="hint">buscando no CurseForge…</p>`;

  try {
    const data = await api(`/api/curseforge/search?q=${encodeURIComponent(query)}`);
    state.searchCache[fileName] = { query, results: data.results };

    if (!data.results.length) {
      panel.innerHTML = `<p class="hint">Nada encontrado para essa busca.</p>`;
      return;
    }

    renderSearchResults(panel, fileName, data.results);
  } catch (error) {
    panel.innerHTML = `<p class="hint">erro: ${esc(error.message)}</p>`;
  }
}

function renderSearchResults(panel, fileName, results) {
  panel.innerHTML = `
    <p class="section-label">${results.length} projeto(s) — clique para ver as versões</p>
    <div class="search-results">
      ${results.map((project) => `
        <div class="search-row">
          <div class="project-info">
            ${logoHtml(project)}
            <div>
              <div class="name">${esc(project.name)}</div>
              <div class="file-meta">
                <span class="mono">${esc(project.slug)}</span>
                <span>${(project.downloads || 0).toLocaleString("pt-BR")} downloads</span>
                ${project.authors && project.authors.length ? `<span>por ${esc(project.authors[0])}</span>` : ""}
              </div>
              ${project.summary ? `<div class="summary">${esc(project.summary)}</div>` : ""}
            </div>
          </div>
          <button class="btn btn-sm btn-primary"
                  data-pick-project="${project.id}"
                  data-pick-name="${esc(project.name)}"
                  data-pick-slug="${esc(project.slug)}"
                  data-file="${esc(fileName)}">ver versões</button>
        </div>
      `).join("")}
    </div>
  `;

  panel.querySelectorAll("[data-pick-project]").forEach((el) => {
    el.addEventListener("click", () =>
      showFiles(el.dataset.file, Number(el.dataset.pickProject), el)
    );
  });
}

async function showFiles(fileName, projectId, sourceEl) {
  const panel = panelFor(fileName);
  if (!panel) return;

  let projectName = sourceEl && sourceEl.dataset.pickName ? sourceEl.dataset.pickName : null;
  let projectSlug = sourceEl && sourceEl.dataset.pickSlug ? sourceEl.dataset.pickSlug : null;

  panel.innerHTML = `<p class="hint">carregando versões…</p>`;

  try {
    let project = state.projectCache[projectId];
    if (!project) {
      try {
        project = await api(`/api/curseforge/projects/${projectId}`);
        state.projectCache[projectId] = project;
      } catch (_) {
        project = null;
      }
    }

    if (project) {
      projectName = projectName || project.name;
      projectSlug = projectSlug || project.slug;
    }

    let files = state.fileCache[projectId];
    if (!files) {
      files = (await api(`/api/curseforge/projects/${projectId}/files`)).files;
      state.fileCache[projectId] = files;
    }

    if (!files.length) {
      panel.innerHTML = `<p class="hint">Esse projeto não tem arquivos listados.</p>`;
      return;
    }

    const target = packTarget();
    const wanted = normalizeFileName(fileName);

    const ordered = [...files].sort((a, b) => compareFiles(a, b, target, wanted));

    panel.innerHTML = `
      ${project ? `
        <div class="project-header">
          ${logoHtml(project)}
          <div>
            <div class="name">${esc(project.name)}</div>
            <div class="file-meta">
              ${project.url ? `<a href="${esc(project.url)}" target="_blank" rel="noreferrer noopener">abrir no CurseForge</a>` : ""}
              <span>${(project.downloads || 0).toLocaleString("pt-BR")} downloads</span>
            </div>
          </div>
        </div>` : ""}
      <p class="section-label">${files.length} arquivo(s)</p>
      <div class="row">
        <input class="input" data-filefilter="${esc(fileName)}" placeholder="filtrar versão…">
      </div>
      <div class="file-list">
        ${ordered.map((file) =>
          fileRowHtml(fileName, projectId, projectName, projectSlug, file, {
            compatible: isCompatible(file, target),
            exact: normalizeFileName(file.file_name) === wanted,
          })
        ).join("")}
      </div>
    `;

    bindFileEvents(panel, fileName);
  } catch (error) {
    panel.innerHTML = `<p class="hint">erro: ${esc(error.message)}</p>`;
  }
}

function fileRowHtml(modFile, projectId, projectName, projectSlug, file, flags) {
  const versions = mcTags(file.game_versions, packTarget().mc);

  return `
    <div class="file-row ${flags.exact ? "exact" : ""} ${flags.compatible ? "compatible" : ""}"
         data-name="${esc(file.file_name.toLowerCase())}">
      <div>
        <div class="name mono">${esc(file.file_name)}</div>
        <div class="file-meta">
          ${flags.exact ? `<span class="exact-tag">mesmo arquivo do pack</span>` : ""}
          ${flags.compatible ? `<span class="compat">compatível</span>` : ""}
          ${versions}
          <span>${esc(file.release_type)}</span>
          <span>${file.size_mb} MB</span>
        </div>
      </div>
      <button class="btn btn-sm btn-primary"
              data-use-file="${file.id}"
              data-use-project="${projectId}"
              data-use-name="${esc(projectName || "")}"
              data-use-slug="${esc(projectSlug || "")}"
              data-use-filename="${esc(file.file_name)}"
              data-mod="${esc(modFile)}">usar esta</button>
    </div>
  `;
}

function bindFileEvents(panel, fileName) {
  const filter = panel.querySelector(`[data-filefilter="${cssEscape(fileName)}"]`);

  if (filter) {
    filter.addEventListener("input", () => {
      const term = filter.value.toLowerCase();
      panel.querySelectorAll(".file-row").forEach((row) => {
        row.classList.toggle("hidden", !row.dataset.name.includes(term));
      });
    });
  }

  panel.querySelectorAll("[data-use-file]").forEach((el) => {
    el.addEventListener("click", () => {
      state.pending[el.dataset.mod] = {
        file_name: el.dataset.useFilename,
        file_id: Number(el.dataset.useFile),
        project_id: Number(el.dataset.useProject),
        project_name: el.dataset.useName || null,
        project_slug: el.dataset.useSlug || null,
      };
      state.openConflict = null;
      renderConflicts();
      renderJob();
      toast("Escolha registrada — clique em Salvar mudanças", "ok");
    });
  });
}

$("btn-discard").addEventListener("click", () => {
  state.pending = {};
  renderConflicts();
  renderJob();
});

/** Manda para o servidor todas as escolhas da aba de conflitos. */
async function saveConflictResolutions() {
  const merged = {};

  state.conflicts.forEach((conflict) => {
    const resolution = effectiveResolution(conflict);
    if (resolution) merged[conflict.file_name] = resolution;
  });

  const payload = Object.entries(merged).map(([file_name, resolution]) => ({
    file_name,
    project_id: resolution.project_id,
    file_id: resolution.file_id,
    project_name: resolution.project_name,
    project_slug: resolution.project_slug,
    curseforge_file_name: resolution.file_name,
  }));

  try {
    const data = await api(`/api/jobs/${state.jobId}/resolutions`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolutions: payload }),
    });

    state.pending = {};
    state.conflicts = data.conflicts;

    // o polling está parado em awaiting_conflicts: sem reler o job, o painel
    // "o que vai acontecer" mostraria o plano de antes das escolhas
    await pollJob();

    renderConflicts();
    return data.saved;
  } catch (error) {
    toast(error.message, "error");
    return null;
  }
}

$("btn-save").addEventListener("click", async () => {
  const salvas = await saveConflictResolutions();

  if (salvas !== null) {
    toast(
      `${salvas} escolha(s) salva(s). Volte em Converter e clique em "Aplicar mudanças".`,
      "ok"
    );
  }
});

/* ------------------------------------------------------------------ detalhe */
async function selectDetail(kind, id) {
  if (state.freshOutput && state.freshOutput !== id) state.freshOutput = null;

  state.selection = { kind, id };

  document.querySelectorAll("[data-record]").forEach((el) => {
    el.classList.toggle("selected", kind === "record" && el.dataset.record === id);
  });

  renderDetail();
  renderRecordActions();

  if (kind === "input") await loadInputDetail(id);
  if (kind === "record") await loadRecordDetail(id);
}

function renderDetail() {
  const box = $("detail");
  const selection = state.selection;

  if (!selection) {
    box.innerHTML = `
      <h2>Detalhes</h2>
      <p class="hint">
        Clique em um modpack de entrada ou em uma conversão salva para ver os
        detalhes aqui.
      </p>`;
    return;
  }

  if (selection.kind === "input") return renderInputDetail();
  if (selection.kind === "record") return renderRecordDetail();
}

function statRow(label, value, kind) {
  return `<div class="kv ${kind || ""}"><span>${label}</span><strong>${value}</strong></div>`;
}

/**
 * Lê o conteúdo de um `.mrpack` e redesenha o painel que pediu.
 *
 * `redesenhar` é passado porque as duas ferramentas mostram a mesma coisa em
 * painéis diferentes (`#detail` e `#u-detail`) — e a resposta do Modrinth chega
 * depois, então o painel é desenhado duas vezes.
 */
async function loadInputDetail(name, redesenhar) {
  const desenhar = redesenhar || (() => isSelected("input", name) && renderDetail());

  try {
    const key = "input:" + name;
    if (!state.detailCache[key]) {
      state.detailCache[key] = await api(`/api/packs/${encodeURIComponent(name)}/inspect`);
    }
    desenhar();

    const modrinthKey = "modrinth:" + name;
    if (!state.detailCache[modrinthKey]) {
      state.detailCache[modrinthKey] = await api(
        `/api/packs/${encodeURIComponent(name)}/modrinth`
      );
    }
    desenhar();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderInputDetail(alvo, nome) {
  const box = alvo || $("detail");
  const name = nome || state.selection.id;
  const info = state.detailCache["input:" + name];

  if (!info) {
    setHTML(box, `<h2>${esc(name)}</h2><p class="hint">lendo o arquivo…</p>`);
    return;
  }

  const modrinth = state.detailCache["modrinth:" + name];
  const byFile = {};
  if (modrinth) modrinth.mods.forEach((m) => (byFile[m.file_name] = m));

  const extras = Object.entries(info.extra_by_folder || {})
    .map(([folder, count]) => `${folder} (${count})`)
    .join(" · ");

  setHTML(box, `
    <h2>${esc(info.name)}</h2>
    <p class="detail-title mono">${esc(info.file)}</p>

    <div class="kv-list">
      ${statRow("Versão", esc(info.version))}
      ${statRow("Minecraft", esc(info.minecraft))}
      ${statRow("Loader", esc(info.loader))}
      ${statRow("Mods", info.mods)}
      ${statRow("Arquivos extras", info.extra_files)}
      ${statRow("Arquivos em overrides/", info.override_files)}
      ${statRow("Tamanho", info.size_mb + " MB")}
    </div>

    ${info.summary ? `<p class="hint summary-box">${esc(info.summary)}</p>` : ""}
    ${extras ? `<p class="section-label">extras</p><p class="hint">${esc(extras)}</p>` : ""}

    <p class="section-label">
      mods ${modrinth ? `· ${modrinth.identified}/${info.mods} identificados no Modrinth`
                      : "· consultando o Modrinth…"}
    </p>
    <div class="mod-list">
      ${info.mod_files.map((file) => {
        const meta = byFile[file];
        return `
          <div class="mod-row">
            <div class="name">${esc(meta && meta.title ? meta.title : file)}</div>
            ${meta && meta.title ? `<div class="file-meta mono">${esc(file)}</div>` : ""}
          </div>`;
      }).join("")}
    </div>
  `);
}

async function loadRecordDetail(id) {
  const key = "record:" + id;

  try {
    // um registro recém-gerado mudou: não reaproveita cache velho
    state.detailCache[key] = await api(`/api/records/${encodeURIComponent(id)}`);
    if (isSelected("record", id)) renderRecordDetail();
  } catch (error) {
    if (isSelected("record", id)) {
      $("detail").innerHTML = `<h2>${esc(id)}</h2><p class="hint">${esc(error.message)}</p>`;
    }
  }
}

/** Um bloco de decisões do mesmo tipo, no formato "esperado × encontrado". */
function decisionGroup(title, mods, kind, lines) {
  if (!mods.length) return "";

  const marker = kind === "manual" ? "±" : "--";

  return `
    <div class="decision-group">
      <h3>
        <span class="tag ${kind === "manual" ? "done" : kind === "version" ? "version" : "missing"}">
          ${marker} ${esc(title)}
        </span>
        <span class="count">${mods.length}</span>
      </h3>
      <div class="decision-list">
        ${mods.map((mod) => `
          <div class="decision ${kind}">
            ${lines(mod).map(([label, value, tone]) => `
              <div class="line">
                <span class="label">${esc(label)}</span>
                <span class="value mono ${tone || ""}">${value}</span>
              </div>
            `).join("")}
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function renderRecordDetail() {
  const box = $("detail");
  const id = state.selection.id;
  const record = state.detailCache["record:" + id];

  if (!record) {
    box.innerHTML = `<h2>${esc(id)}</h2><p class="hint">lendo o registro…</p>`;
    return;
  }

  const pack = record.pack || {};
  const summary = record.summary || {};
  const resolutions = record.resolutions || {};
  const mods = record.mods || [];

  const byName = (a, b) => a.file_name.localeCompare(b.file_name);

  const manual = mods.filter((m) => m.strategy === "manual").sort(byName);
  const automatic = mods.filter(
    (m) => m.status === "curseforge" && m.strategy !== "manual"
  );
  const versionUnavailable = mods
    .filter((m) => m.status === "version-unavailable")
    .sort(byName);
  const missing = mods
    .filter((m) => m.status === "not-on-curseforge" || m.status === "unknown")
    .sort(byName);
  const failed = mods.filter((m) => m.status === "failed").sort(byName);

  box.innerHTML = `
    <h2>${esc(pack.name || id)}</h2>
    <p class="detail-title mono">origem: ${esc(record.source || "?")}</p>

    <div class="kv-list">
      ${statRow("Versão", esc(pack.version || "?"))}
      ${statRow("Minecraft", esc((pack.minecraft || "?") + " · " + (pack.loader || "?")))}
      ${statRow("Mods", summary.total_mods)}
      ${statRow("No CurseForge", summary.matched, "ok")}
      ${statRow("Versão indisponível", summary.version_unavailable, "warn")}
      ${statRow("Projeto não existe", summary.not_on_curseforge, "bad")}
      ${summary.failed ? statRow("Falhas", summary.failed, "bad") : ""}
      ${statRow("Escolhas manuais", Object.keys(resolutions).length)}
      ${statRow("Conversão automática", summary.success_rate + "%")}
      ${statRow("Convertido em", fmtDate(record.updated_at))}
    </div>

    ${record.source_available === false ? `
      <div class="notice bad">
        O arquivo de origem <span class="mono">${esc(record.source)}</span> não está
        mais em input_modpacks/ — sem ele não dá para gerar o modpack de novo.
      </div>` : ""}

    <p class="section-label">decisões</p>

    ${automatic.length ? `
      <div class="summary-line">
        <span class="value ok">++ ${automatic.length}</span> mod(s) encontrados
        automaticamente no CurseForge (não listados)
      </div>` : ""}

    ${decisionGroup("escolhidos por você", manual, "manual", (mod) => {
      const choice = resolutions[mod.file_name] || {};
      return [
        ["esperado", esc(mod.file_name), ""],
        ["usado", esc(choice.file_name || "arquivo #" + (mod.file_id ?? "?")), "ok"],
        ["projeto", esc(mod.project_name || choice.project_name || "-"), ""],
      ];
    })}

    ${decisionGroup("versão indisponível", versionUnavailable, "version", (mod) => {
      const d = mod.diagnosis || {};
      return [
        ["esperado", esc(mod.file_name), ""],
        ["no CF", esc(d.project_name || "-"), "cyan"],
        [
          "versão CF",
          d.closest_file_name
            ? `${esc(d.closest_file_name)} <span class="hint">(${Math.round((d.similarity || 0) * 100)}%)</span>`
            : "-",
          "warn",
        ],
        ["resultado", "foi para overrides/mods", "dim"],
      ];
    })}

    ${decisionGroup("projeto não encontrado", missing, "missing", (mod) => [
      ["esperado", esc(mod.file_name), ""],
      ["no Modrinth", esc(mod.modrinth_title || "-"), "cyan"],
      ["no CF", "nada parecido o suficiente", "bad"],
      ["resultado", "foi para overrides/mods", "dim"],
    ])}

    ${decisionGroup("falhas", failed, "missing", (mod) => [
      ["arquivo", esc(mod.file_name), ""],
      ["erro", esc(mod.error || "-"), "bad"],
    ])}

    ${!manual.length && !versionUnavailable.length && !missing.length && !failed.length
      ? `<p class="hint">Todos os mods entraram no manifest automaticamente.</p>`
      : ""}
  `;

}

/* ------------------------------------------- ações da saída (rodapé do card) */
/* Ficam embaixo da lista, no lugar onde a entrada tem o "Iniciar conversão" —
   a coluna passa a ter a mesma forma dos dois lados. */
function renderUpdateOutputActions(dados) {
  const nome = state.selectedUpdate;

  setText(
    $("update-output-selected"),
    !nome
      ? "nenhum pack selecionado"
      : dados && dados.available === false
        ? "o .mrpack não está mais na pasta"
        : nome
  );

  if (!nome) return setHTML($("update-output-actions"), "");

  const existe = !dados || dados.available !== false;
  const mudou = setHTML($("update-output-actions"), `
    ${existe
      ? `<a class="btn btn-primary"
            href="/api/updates/${encodeURIComponent(nome)}/download">⬇ Baixar</a>
         <button class="btn" data-saved-input>Adicionar ao input</button>`
      : ""}
    <button class="btn btn-ghost" data-saved-delete>Excluir</button>
  `);

  if (!mudou) return;

  const enviar = $("update-output-actions").querySelector("[data-saved-input]");
  if (enviar) {
    enviar.addEventListener("click", async () => {
      try {
        const data = await post(`/api/updates/${encodeURIComponent(nome)}/to-input`);
        await loadState();
        toast(`${data.name} está em input_modpacks`, "ok");
      } catch (error) {
        toast(error.message, "error");
      }
    });
  }

  $("update-output-actions").querySelector("[data-saved-delete]")
    .addEventListener("click", async () => {
      if (!armarBotao("del-update", "Excluir mesmo?")) {
        return renderUpdateOutputActions(dados);
      }

      try {
        await api(`/api/updates/${encodeURIComponent(nome)}`, { method: "DELETE" });
        state.selectedUpdate = null;
        renderUpdateDetail(state.updateJob && state.updateJob.update);
        await loadSavedUpdates();
        toast("Pack atualizado excluído", "ok");
      } catch (error) {
        toast(error.message, "error");
      }
    });
}

function renderRecordActions() {
  const escolhido =
    state.selection && state.selection.kind === "record" ? state.selection.id : null;

  const registro = (state.records || []).find((r) => r.id === escolhido);
  const ocupado = !!state.jobId;

  setText(
    $("record-selected"),
    !registro
      ? "nenhuma conversão selecionada"
      : ocupado
        ? "feche o trabalho aberto para gerar este modpack"
        : registro.source_available
          ? registro.id
          : "o .mrpack de origem não está mais em input_modpacks/"
  );

  if (!registro) return setHTML($("record-actions"), "");

  const podeGerar = registro.source_available && !ocupado;
  const mudou = setHTML($("record-actions"), `
    <button class="btn btn-primary" data-generate ${podeGerar ? "" : "disabled"}>
      Gerar modpack
    </button>
    <button class="btn btn-ghost" data-delete>Excluir registro</button>
  `);

  if (!mudou) return;

  $("record-actions").querySelector("[data-generate]")
    .addEventListener("click", async () => {
      await fecharJobMorto();
      clearJobState();

      try {
        const job = await api(
          `/api/records/${encodeURIComponent(registro.id)}/generate`,
          { method: "POST" }
        );
        state.jobId = job.id;
        startPolling();
      } catch (error) {
        toast(error.message, "error");
      }
    });

  $("record-actions").querySelector("[data-delete]")
    .addEventListener("click", async () => {
      if (!armarBotao("del-record", "Excluir mesmo?")) return renderRecordActions();

      try {
        await api(`/api/records/${encodeURIComponent(registro.id)}`, {
          method: "DELETE",
        });
        delete state.detailCache["record:" + registro.id];
        state.selection = null;
        renderDetail();
        await loadState();
        toast("Registro excluído", "ok");
      } catch (error) {
        toast(error.message, "error");
      }
    });
}

/* =========================================================================
   ATUALIZAR MODS — outro trabalho sobre o mesmo pack: procura no Modrinth a
   versão mais recente de cada mod para a versão do Minecraft escolhida.
   ========================================================================= */

function renderUpdatePacks(packs) {
  const list = $("update-pack-list");
  packs = packs || [];

  if (!packs.length) {
    setHTML(list, `<p class="hint">Nenhum .mrpack na pasta de entrada.</p>`);
    return;
  }

  const emCurso = packEmCurso(state.updateJob);
  const doJob = state.updateJob ? state.updateJob.source : null;

  setHTML(list, ordenarPacks(packs, emCurso, doJob).map((pack) => `
    <div class="pack ${state.updatePack === pack.name ? "selected" : ""}
                ${pack.name === emCurso ? "working" : ""}
                ${pack.name === doJob ? "in-job" : ""}"
         data-update-pack="${esc(pack.name)}">
      <div>
        <div class="name">
          ${pack.name === emCurso ? `<span class="spinner"></span>` : ""}
          ${esc(pack.name)}
          ${pack.name === doJob && !emCurso
            ? `<span class="tag in-job-tag">na atualização aberta</span>`
            : ""}
        </div>
        <div class="meta">${packMeta(pack)}</div>
        <div class="meta">${pack.size_mb} MB · ${fmtDate(pack.modified)}</div>
      </div>
      <span class="hint">›</span>
    </div>
  `).join(""));

  list.querySelectorAll("[data-update-pack]").forEach((el) => {
    el.addEventListener("click", () => {
      state.updatePack = el.dataset.updatePack;
      fecharUpdateMorto();
      renderUpdatePacks(state.packs || packs);
      syncLoaderChoice();
      updateUpdateButton();
      // o painel da direita mostra o que tem dentro, como no conversor
      renderUpdateDetail(state.updateJob && state.updateJob.update);
      loadInputDetail(state.updatePack, () =>
        renderUpdateDetail(state.updateJob && state.updateJob.update)
      );
    });
  });
}

/** O loader do pack escolhido, para o seletor começar no lugar certo. */
function selectedUpdatePack() {
  return (state.packs || []).find((p) => p.name === state.updatePack) || null;
}

/**
 * Alinha os três seletores com o pack escolhido.
 *
 * O ponto de partida é o pack como ele é hoje — mexer no Minecraft ou no loader
 * é a decisão que você veio tomar, e ela fica explícita. Depois que você mexe
 * num deles, trocar de pack não sobrescreve mais aquela escolha.
 */
/**
 * Alinha os três seletores com o pack escolhido.
 *
 * Volta **sempre** ao que o pack é hoje, mesmo que você já tenha mexido antes:
 * escolher outro pack é começar de novo, e herdar a escolha do anterior gerava
 * um alvo que não era de ninguém.
 */
function syncLoaderChoice() {
  const pack = selectedUpdatePack();
  if (!pack) return loadLoaderVersions();

  const mc = $("update-mc");

  // só dá para usar a versão do pack se o Modrinth a conhecer
  if (pack.minecraft && [...mc.options].some((o) => o.value === pack.minecraft)) {
    mc.value = pack.minecraft;
  }

  if (pack.loader) $("update-loader-name").value = pack.loader;

  // a versão do loader é escolhida quando a lista chega
  state.loaderVersion = "keep";

  loadLoaderVersions();
}

/** Trocar de loader é uma mudança grande: a interface avisa e exige a versão. */
function loaderChanged() {
  const pack = selectedUpdatePack();
  const escolhido = $("update-loader-name").value;

  return !!(pack && pack.loader && escolhido && escolhido !== pack.loader);
}

/**
 * Preenche o dropdown de versões do loader.
 *
 * A lista vem do serviço do próprio loader (fabric/quilt meta, maven do
 * neoforge/forge) e depende da versão do Minecraft — por isso recarrega quando
 * qualquer um dos dois muda. Se a versão que estava escolhida não servir mais
 * no alvo novo, a interface avisa em vez de mandar um número inválido.
 */
async function loadLoaderVersions() {
  const select = $("update-loader");
  const loader = $("update-loader-name").value;
  const minecraft = $("update-mc").value;
  const anterior = state.loaderVersion;
  const pack = selectedUpdatePack();

  if (!loader || !minecraft) {
    setHTML(select, `<option value="">(escolha o Minecraft primeiro)</option>`);
    renderLoaderHint(null);
    updateUpdateButton();
    return;
  }

  const chave = `${loader}|${minecraft}`;
  state.loaderRequest = chave;

  if (!state.loaderVersions[chave]) {
    setHTML(select, `<option value="">carregando…</option>`);

    try {
      state.loaderVersions[chave] = await api(
        `/api/loaders/${encodeURIComponent(loader)}/versions` +
          `?minecraft=${encodeURIComponent(minecraft)}`
      );
    } catch (_) {
      state.loaderVersions[chave] = { versions: [], latest: null };
    }
  }

  // outra troca aconteceu enquanto isto carregava
  if (state.loaderRequest !== chave) return;

  const dados = state.loaderVersions[chave];

  if (!dados.versions.length) {
    setHTML(
      select,
      `<option value="">não foi possível listar — a do pack será usada</option>`
    );
    renderLoaderHint(dados);
    updateUpdateButton();
    return;
  }

  // "manter" só aparece quando faz sentido: mesmo loader e versão diferente da
  // mais nova (trocar de loader torna a versão do pack inútil)
  const doPack = pack && !loaderChanged() ? pack.loader_version : null;
  const manter = doPack && doPack !== dados.latest;

  setHTML(select, [
    manter ? `<option value="keep">${esc(doPack)} — a atual</option>` : "",
    `<option value="">${esc(dados.latest || "?")} — mais recente</option>`,
    ...dados.versions.map(
      (item) =>
        `<option value="${esc(item.version)}">${esc(item.version)}` +
        `${item.stable ? "" : " (instável)"}</option>`
    ),
  ].join(""));

  // mantém a escolha se ela ainda existir no alvo novo
  const aindaServe =
    anterior === "keep"
      ? !!manter
      : dados.versions.some((item) => item.version === anterior);

  select.value = aindaServe ? anterior : manter ? "keep" : "";
  state.loaderVersion = select.value;

  // "keep" é sentinela, não versão: só avisa quando um número real se perdeu
  const perdida =
    anterior && anterior !== "keep" && !aindaServe ? anterior : null;

  renderLoaderHint(dados, perdida, doPack);
  updateUpdateButton();
}

function renderLoaderHint(dados, perdida, doPack) {
  const hint = $("update-loader-hint");

  if (perdida) {
    setText(hint, `${perdida} não serve aqui`);
    setClass(hint, "hint warn-text");
    return;
  }

  if (dados && !dados.versions.length) {
    setText(hint, "serviço fora do ar");
    setClass(hint, "hint warn-text");
    return;
  }

  // trocando de loader a versão do pack não serve de referência nenhuma
  if (loaderChanged()) {
    setText(hint, "");
    setClass(hint, "hint");
    return;
  }

  // qual está no pack hoje, para reconhecer na lista
  setClass(hint, "hint");
  setText(hint, doPack ? `atual ${doPack}` : "");
}

function updateUpdateButton() {
  // só a própria atualização bloqueia: uma conversão pode rodar em paralelo
  const ocupado = !!state.updateJobId;
  const dados = state.loaderVersions[
    `${$("update-loader-name").value}|${$("update-mc").value}`
  ];
  // sem lista e trocando de loader não dá: o backend recusa sem a versão
  const semVersao = loaderChanged() && !$("update-loader").value && !dados?.latest;

  $("btn-update").disabled =
    !state.updatePack || !$("update-mc").value || ocupado || semVersao;

  setText(
    $("update-selected"),
    ocupado
      ? "feche a atualização aberta para iniciar outra"
      : semVersao
        ? `informe a versão do ${$("update-loader-name").value} para trocar de loader`
        : state.updatePack || "nenhum modpack selecionado"
  );
}

async function loadMinecraftVersions() {
  const select = $("update-mc");

  try {
    const data = await api("/api/minecraft-versions");

    select.innerHTML = data.versions
      .map((v) => `<option value="${esc(v)}">${esc(v)}</option>`)
      .join("");
  } catch (error) {
    select.innerHTML = `<option value="">(falha ao carregar)</option>`;
  }

  // agora que a lista existe, dá para escolher a versão do pack
  syncLoaderChoice();
  updateUpdateButton();
}

/** Vazio no dropdown = "mais recente": resolve para o número de verdade. */
function escolhaDoLoader() {
  const escolhida = $("update-loader").value;

  // "manter a do pack": o backend usa a que já está no índice
  if (escolhida === "keep") return null;
  if (escolhida) return escolhida;

  const dados = state.loaderVersions[
    `${$("update-loader-name").value}|${$("update-mc").value}`
  ];

  // sem lista, o backend mantém a versão do pack (ou recusa, se trocou o loader)
  return (dados && dados.latest) || null;
}

async function loadLoaders() {
  const select = $("update-loader-name");

  try {
    const data = await api("/api/loaders");
    select.innerHTML = data.loaders
      .map((l) => `<option value="${esc(l)}">${esc(l)}</option>`)
      .join("");
  } catch (_) {
    select.innerHTML = `<option value="">(falha ao carregar)</option>`;
  }

  syncLoaderChoice();
}

// a lista de versões do loader depende dos dois: Minecraft e loader
$("update-mc").addEventListener("change", loadLoaderVersions);
$("update-loader-name").addEventListener("change", loadLoaderVersions);

$("update-loader").addEventListener("change", () => {
  state.loaderVersion = $("update-loader").value;
  updateUpdateButton();
});

$("btn-update").addEventListener("click", async () => {
  if (!state.updatePack) return;

  await fecharUpdateMorto();

  $("btn-update").disabled = true;
  $("u-log").innerHTML = "";
  state.updateLogCount = 0;
  resetUpdateDecisions();

  try {
    const job = await api("/api/update", json({
      file: state.updatePack,
      minecraft: $("update-mc").value,
      loader: $("update-loader-name").value || null,
      loader_version: escolhaDoLoader(),
    }));

    state.updateJobId = job.id;
    startUpdatePolling();
  } catch (error) {
    toast(error.message, "error");
    updateUpdateButton();
  }
});

/* A atualização tem o seu próprio ciclo: pode rodar junto com uma conversão. */
function startUpdatePolling() {
  stopUpdatePolling();
  state.updatePolling = setInterval(pollUpdateJob, 600);
  pollUpdateJob();
}

function stopUpdatePolling() {
  if (state.updatePolling) clearInterval(state.updatePolling);
  state.updatePolling = null;
}

async function pollUpdateJob() {
  if (!state.updateJobId) return;

  let job;
  try {
    job = await api(
      `/api/jobs/${state.updateJobId}?log_offset=${state.updateLogCount}`
    );
  } catch (_) {
    stopUpdatePolling();
    resetUpdateJob();
    return;
  }

  const anterior =
    state.updateJob && state.updateJob.id === job.id ? state.updateJob.status : undefined;

  state.updateJob = job;
  appendLogs(job.logs, $("u-log"));
  state.updateLogCount = job.log_count;

  renderUpdateJob();
  renderUpdateReview();
  updateUpdateButton();

  if (job.status !== anterior) renderUpdatePacks(state.packs || []);

  if (job.status !== anterior && job.status === "awaiting_review") {
    toast("Análise pronta — revise as mudanças antes de aplicar", "ok");
  }

  if (["done", "error", "cancelled", "awaiting_review"].includes(job.status)) {
    stopUpdatePolling();
    if (job.status === "done" && job.status !== anterior) {
      await loadSavedUpdates();
      loadCacheSize();

      if (job.output) {
        state.freshOutput = job.output.name;
        mostrarLado("update", "update-out");
        showSavedUpdate(job.output.name);
      }
    }
  }
}

function resetUpdateJob() {
  stopUpdatePolling();
  state.updateJobId = null;
  state.updateJob = null;
  resetUpdateDecisions();
  renderUpdateJob();
  renderUpdateReview();
  updateUpdateButton();
  leaveEmptyTab("update-review", "update");
}

function renderUpdateJob() {
  const job = state.updateJob;

  if (!job) {
    $("u-body").classList.add("hidden");
    $("u-empty").classList.remove("hidden");
    setText($("u-status"), "nenhuma aberta");
    setClass($("u-status"), "pill pill-muted");
    return;
  }

  $("u-empty").classList.add("hidden");
  $("u-body").classList.remove("hidden");
  setText($("u-source"), job.source);
  setText($("u-status"), STATUS_LABEL[job.status] || job.status);
  setClass($("u-status"), "pill " + (STATUS_PILL[job.status] || "pill-muted"));
  setText($("u-stage"), job.stage || STATUS_LABEL[job.status] || "");

  const fill = $("u-fill");

  if (job.total > 0) {
    fill.classList.remove("indeterminate");
    fill.style.width = ((job.done / job.total) * 100).toFixed(1) + "%";
    setText($("u-counter"), `${job.done} / ${job.total}`);
  } else if (["running", "queued"].includes(job.status)) {
    fill.classList.add("indeterminate");
    setText($("u-counter"), "");
  } else {
    fill.classList.remove("indeterminate");
    fill.style.width = job.status === "done" ? "100%" : "0%";
    setText($("u-counter"), "");
  }

  const notice = $("u-notice");
  const update = job.update;

  if (job.status === "error") {
    setClass(notice, "notice bad");
    setText(notice, job.error || "erro desconhecido");
    notice.classList.remove("hidden");
  } else if (job.status === "cancelled") {
    // cancelar não é sucesso, mas também não é erro: laranja, como os avisos
    setClass(notice, "notice warn");
    setHTML(notice, `
      <strong>Atualização cancelada.</strong>
      ${update && update.packaged
        ? "O <span class=\"mono\">.mrpack</span> gerado antes do cancelamento continua na pasta de saída."
        : "Nada foi gravado."}
    `);
    notice.classList.remove("hidden");
  } else if (job.status === "awaiting_review" && update) {
    const pendentes = updateFiles("without_version").filter(
      (f) => !pickFor(f)
    ).length;
    const escolhidos = allUpdateFiles().filter((f) => pickFor(f)).length;

    setClass(notice, "notice warn");
    setHTML(notice, `
      <strong>Nada foi gravado ainda.</strong>
      ${pendentes} arquivo(s) sem versão para o alvo e ${escolhidos} escolhido(s)
      por você. Ajuste o que quiser na aba <strong>Revisar</strong> e depois
      <strong>aplique</strong> para gerar o <span class="mono">.mrpack</span>.
    `);
    notice.classList.remove("hidden");
  } else if (update && job.status === "done") {
    setClass(notice, "notice ok");
    setHTML(notice, `
      <strong>${update.summary.updated}</strong>
      ${update.downgrade ? "trocados de versão" : "atualizados"} ·
      <strong>${update.summary.manual}</strong> escolhidos por você ·
      <strong>${update.summary.excluded}</strong> fora do pack
      ${job.output ? `· ${esc(job.output.name)} (${job.output.size_mb} MB)` : ""}
    `);
    notice.classList.remove("hidden");
  } else {
    notice.classList.add("hidden");
  }

  const botoes = [];

  if (["queued", "running", "finishing"].includes(job.status)) {
    botoes.push(
      `<button class="btn ${armados.ucancel ? "btn-danger" : ""}" data-uact="cancel">
         ${rotuloArmado("ucancel", "Cancelar")}</button>`
    );
  }

  if (job.status === "awaiting_review") {
    const pendentes = updateFiles("without_version").filter(
      (f) => !pickFor(f)
    ).length;

    botoes.push(
      `<button class="btn" data-uact="review">Revisar (${pendentes})</button>`,
      `<button class="btn btn-primary" data-uact="apply">Aplicar mudanças</button>`,
      `<button class="btn ${armados.ucancel ? "btn-danger" : ""}" data-uact="cancel">
         ${rotuloArmado("ucancel", "Cancelar")}</button>`
    );
  }

  if (job.status === "done" && job.output) {
    botoes.push(
      `<a class="btn btn-primary"
          href="/api/jobs/${job.id}/download">⬇ Baixar .mrpack</a>`,
      `<a class="btn btn-ghost" href="/api/jobs/${job.id}/report">Baixar registro (JSON)</a>`,
      `<button class="btn" data-uact="to-input">Adicionar ao input</button>`
    );
  }

  if (["done", "error", "cancelled"].includes(job.status)) {
    botoes.push(`<button class="btn btn-ghost" data-uact="close">Fechar</button>`);
  }

  if (setHTML($("u-actions"), botoes.join(""))) {
    $("u-actions").querySelectorAll("[data-uact]").forEach((el) => {
      el.addEventListener("click", () => runUpdateAction(el.dataset.uact));
    });
  }

  renderUpdateConfirm(job);
  renderUpdateDetail(update);
}

/** O painel da direita: o que a atualização fez, em números e por arquivo. */
function renderUpdateDetail(update) {
  const box = $("u-detail");

  // sem resultado ainda: mostra o que tem dentro do pack escolhido
  if (!update) {
    if (state.updatePack) {
      renderInputDetail(box, state.updatePack);
      return;
    }

    setHTML(box, `
      <h2>Resultado</h2>
      <p class="hint">
        Clique num modpack de entrada para ver o que tem dentro dele, ou num pack
        atualizado para rever as decisões.
      </p>`);
    return;
  }

  const resumo = update.summary;
  const arquivos = [...update.without_version, ...update.with_version];

  const grupos = [
    {
      titulo: "fora do pack",
      tom: "missing",
      itens: arquivos.filter((f) => f.excluded),
    },
    {
      titulo: "escolhidos por você",
      tom: "done",
      itens: arquivos.filter((f) => !f.excluded && f.status === "manual"),
    },
    {
      titulo: update.downgrade ? "trocam de versão" : "atualizados",
      tom: "ok",
      itens: arquivos.filter(
        (f) => !f.excluded && f.status === "updated" && !f.skipped
      ),
    },
    {
      titulo: "ficam na versão atual",
      tom: "version",
      itens: arquivos.filter((f) => !f.excluded && f.skipped),
    },
    {
      titulo: "sem versão, mas incluídos",
      tom: "version",
      itens: arquivos.filter(
        (f) => !f.excluded && !f.has_version && f.status !== "manual"
      ),
    },
  ].filter((grupo) => grupo.itens.length);

  setHTML(box, `
    <h2>Resultado</h2>
    <p class="detail-title mono">
      ${esc(update.from_minecraft)} → ${esc(update.to_minecraft)}
      ${update.loader_changed
        ? `· ${esc(update.from_loader)} → ${esc(update.to_loader)}`
        : `· ${esc(update.to_loader)}`}
    </p>

    <div class="kv-list">
      ${statRow("Arquivos no índice", resumo.total)}
      ${statRow(update.downgrade ? "Trocados" : "Atualizados", resumo.updated, "ok")}
      ${statRow("Já na versão mais recente", resumo.unchanged)}
      ${statRow("Escolhidos por você", resumo.manual, resumo.manual ? "ok" : "")}
      ${statRow("Ficam na versão atual", resumo.kept_by_choice)}
      ${statRow("Fora do pack", resumo.excluded, resumo.excluded ? "warn" : "")}
      ${resumo.unlisted ? statRow("Sem URL no índice", resumo.unlisted, "bad") : ""}
    </div>

    ${grupos.map((grupo) => `
      <div class="decision-group">
        <h3>
          <span class="tag ${grupo.tom}">${esc(grupo.titulo)}</span>
          <span class="count">${grupo.itens.length}</span>
        </h3>
        <div class="decision-list">
          ${grupo.itens.map((file) => `
            <div class="decision">
              <div class="line">
                <span class="label">arquivo</span>
                <span class="value">${esc(file.title || file.file_name)}</span>
              </div>
              ${file.to_version
                ? `<div class="line">
                     <span class="label">versão</span>
                     <span class="value mono ok">${esc(file.from_version || "?")} → ${esc(file.to_version)}</span>
                   </div>`
                : `<div class="line">
                     <span class="label">arquivo</span>
                     <span class="value mono dim">${esc(file.file_name)}</span>
                   </div>`}
            </div>
          `).join("")}
        </div>
      </div>
    `).join("")}
  `);
}

async function runUpdateAction(action) {
  if (action === "review") return goToTab("update-review");

  if (action === "apply") return applyUpdate();

  if (action === "cancel") {
    if (!armarBotao("ucancel", "Cancelar mesmo?")) return;

    state.updateConfirming = false;
    try {
      await post(`/api/jobs/${state.updateJobId}/cancel`);
      startUpdatePolling();
    } catch (error) {
      toast(error.message, "error");
    }
    return;
  }

  if (action === "to-input") {
    try {
      const data = await post(`/api/jobs/${state.updateJobId}/to-input`);
      await loadState();
      toast(`${data.name} está em input_modpacks — pronto para converter`, "ok");
    } catch (error) {
      toast(error.message, "error");
    }
    return;
  }

  if (action === "close") {
    try {
      await post(`/api/jobs/${state.updateJobId}/close`);
    } catch (_) {}

    resetUpdateJob();
    await loadState();
  }
}

/* =========================================================================
   ATUALIZADOR · REVISÃO

   Mesma forma da aba de conflitos do conversor: uma aba só, três seções.
   Sem versão -> (escolhe um arquivo) -> Resolvidos <- (troca a versão) <- Com
   versão. A origem do card fica visível: quem veio do "sem versão" tem uma cor,
   quem veio do "com versão" tem outra.
   ========================================================================= */

function resetUpdateDecisions() {
  state.updatePending = {};
  state.updateInclude = {};
  state.updateKeep = {};
  state.openUpdateFile = null;
  state.updateConfirming = false;
}

/** A escolha que vale: a da tela ganha da que veio do servidor. */
function pickFor(file) {
  const caminho = file.file_path;

  if (Object.prototype.hasOwnProperty.call(state.updatePending, caminho)) {
    return state.updatePending[caminho];
  }

  return file.chosen || null;
}

/** Entra no pack? Só faz diferença para quem não tem versão nem escolha. */
function includeFor(file) {
  const caminho = file.file_path;

  if (Object.prototype.hasOwnProperty.call(state.updateInclude, caminho)) {
    return state.updateInclude[caminho];
  }

  return !file.excluded;
}

function keepFor(file) {
  const caminho = file.file_path;

  if (Object.prototype.hasOwnProperty.call(state.updateKeep, caminho)) {
    return state.updateKeep[caminho];
  }

  return !!file.skipped;
}

/**
 * Os arquivos de um grupo da revisão.
 *
 * Depois que o `.mrpack` é gerado a lista fica **vazia**: não há mais decisão
 * pendente, e é o mesmo comportamento da aba de conflitos do conversor
 * (`outcome.packaged` -> `conflicts() == []`). Para mudar de ideia, feche e
 * analise de novo.
 */
const updateFiles = (grupo) => {
  const update = state.updateJob ? state.updateJob.update : null;
  if (!update || update.packaged) return [];

  return update[grupo] || [];
};

function allUpdateFiles() {
  return [...updateFiles("without_version"), ...updateFiles("with_version")];
}

/** Quantas decisões ainda não foram salvas no servidor. */
function unsavedUpdateCount() {
  return (
    Object.keys(state.updatePending).length +
    Object.keys(state.updateInclude).length +
    Object.keys(state.updateKeep).length
  );
}

/* ------------------------------------------------------------- as 3 seções */
const UPDATE_SECTIONS = [
  {
    key: "sem-versao",
    title: "Sem versão",
    tag: "missing",
    hint:
      "Não existe versão para o alvo, então nada aqui entra no pack. Escolha um " +
      "arquivo à mão (dá para procurar outro projeto) para resolver.",
    match: (file) => !file.has_version && !pickFor(file) && !includeFor(file),
  },
  {
    key: "resolvidos",
    title: "Resolvidos por você",
    tag: "done",
    hint: "entram no pack quando você aplicar",
    match: (file) => !!pickFor(file) || (!file.has_version && includeFor(file)),
  },
  {
    key: "com-versao",
    title: "Com versão",
    tag: "ok",
    hint:
      "O Modrinth tem versão para o alvo. Trocar a versão (ou o projeto) manda o " +
      "card para o meio.",
    match: (file) => file.has_version && !pickFor(file),
  },
];

/**
 * Ordem dos resolvidos, do que mais precisa de atenção para o que menos:
 * 1. tinha versão e você trocou (roxo) — você contrariou a proposta;
 * 2. não tinha versão e você escolheu uma (verde);
 * 3. entrou na versão atual pelo botão de não-mods (verde escuro) — decisão
 *    em massa, é o que menos precisa ser conferido item a item.
 */
function nivelResolvido(file) {
  if (!pickFor(file)) return 2;
  return file.has_version ? 0 : 1;
}

function ordenarResolvidos(itens) {
  return [...itens].sort((a, b) => {
    const diff = nivelResolvido(a) - nivelResolvido(b);
    if (diff) return diff;
    return (a.title || a.file_name).localeCompare(b.title || b.file_name);
  });
}

/**
 * Botões de ação em massa no cabeçalho de cada seção.
 *
 * "Sem versão" só inclui; quem já está dentro é assunto da seção do meio, e é
 * lá que fica o "tirar" — senão o mesmo botão significaria coisas diferentes
 * conforme o estado.
 */
function bulkNonMods(secao) {
  const naoMods = updateFiles("without_version").filter(
    (file) => !file.is_mod && !pickFor(file)
  );

  const fora = naoMods.filter((file) => !includeFor(file));
  const dentro = naoMods.filter((file) => includeFor(file));
  const alvos = secao === "sem-versao" ? fora : dentro;

  if (!alvos.length) return "";

  const rotulo = `${alvos.length} que não ${alvos.length === 1 ? "é mod" : "são mods"}`;

  return secao === "sem-versao"
    ? `<button class="btn btn-sm" data-ubulk="incluir"
               title="Resourcepacks, shaders e datapacks costumam funcionar além da versão em que foram publicados">
         Incluir ${rotulo}
       </button>`
    : `<button class="btn btn-sm" data-ubulk="tirar">Tirar ${rotulo}</button>`;
}

function renderUpdateReview() {
  const container = $("ur-groups");
  const arquivos = allUpdateFiles();
  const pendentes = updateFiles("without_version").filter(
    (file) => !pickFor(file)
  ).length;
  const naoSalvas = unsavedUpdateCount();

  setText($("tab-update-badge"), String(pendentes));
  setClass($("tab-update-badge"), "badge" + (pendentes ? " hot" : ""));

  if (!arquivos.length) {
    $("ur-empty").classList.remove("hidden");
    $("ur-summary").classList.add("hidden");
    setHTML(container, "");
    return;
  }

  const update = state.updateJob.update;

  $("ur-empty").classList.add("hidden");
  $("ur-summary").classList.remove("hidden");

  setHTML(
    $("ur-target"),
    mcTag(update.to_minecraft) +
      " " +
      loaderTag(update.to_loader, update.loader.split("-")[1])
  );

  const resolvidos = arquivos.filter((f) => pickFor(f)).length;
  const fora = updateFiles("without_version").filter(
    (f) => !pickFor(f) && !includeFor(f)
  ).length;

  // mesmo formato do hint da aba de conflitos: quantos faltam, o que acontece
  // com quem ficar sem decisão, e as alterações pendentes
  const semVersao = updateFiles("without_version").length;

  setHTML(
    $("ur-hint"),
    `${semVersao - fora} de ${semVersao} resolvidos. O que ficar sem escolha ` +
      `<strong>não entra</strong> no pack novo — os outros ${resolvidos} já estão ` +
      `decididos.` +
      (naoSalvas
        ? ` <span class="unsaved-inline">${naoSalvas} alteração(ões) não salva(s)</span>`
        : "")
  );

  const gerando = jobGerando(state.updateJob);

  $("btn-ur-save").disabled = naoSalvas === 0 || gerando;
  $("btn-ur-discard").disabled = naoSalvas === 0 || gerando;
  travarRevisao(container, gerando);

  // idem: durante o `finishing` o poll volta a passar por aqui
  const markup = UPDATE_SECTIONS.map((section) => {
    const itens = arquivos.filter(section.match);

    return `
      <section class="conflict-section ${itens.length ? "" : "empty"}">
        <header>
          <span class="tag ${section.tag}">${esc(section.title)}</span>
          <span class="badge">${itens.length}</span>
          ${section.key === "com-versao" ? "" : bulkNonMods(section.key)}
          <p class="hint">${esc(section.hint)}</p>
        </header>
        ${itens.length
          ? (section.key === "resolvidos" ? ordenarResolvidos(itens) : itens)
              .map((file) => updateCard(file, section.key))
              .join("")
          : `<p class="hint">nenhum</p>`}
      </section>
    `;
  }).join("");

  if (setHTML(container, markup)) bindUpdateReviewEvents();
  autoLoadOpenUpdateFile();
}

/* ------------------------------------------------------------ card de arquivo */
function updateCard(file, secao) {
  const aberto = state.openUpdateFile === file.file_path;
  const escolha = pickFor(file);
  const dentro = includeFor(file);
  const manter = keepFor(file);
  const sujo =
    Object.prototype.hasOwnProperty.call(state.updatePending, file.file_path) ||
    Object.prototype.hasOwnProperty.call(state.updateInclude, file.file_path);

  // a cor diz como o card foi resolvido: troca de versão num mod que já tinha
  // (roxo), escolha manual de quem não tinha (verde) ou entrada em massa dos
  // não-mods na versão atual (verde escuro)
  const origem = !escolha
    ? "as-is"
    : file.has_version
      ? "from-version"
      : "from-missing";
  const resolvido = escolha || (!file.has_version && dentro);

  const classes = [
    "conflict",
    resolvido ? `resolved ${origem}` : "",
    !resolvido && !file.has_version ? "missing" : "",
    manter ? "kept" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return `
    <div class="${classes}">
      <div class="conflict-head" data-utoggle="${esc(file.file_path)}">
        <div class="conflict-title">
          <span class="conflict-file mono">${esc(file.file_name)}</span>
          ${updateHeadNote(file, escolha, sujo, dentro, manter)}
        </div>
        <div class="row">
          ${resolvido
            ? `<button class="btn btn-sm" data-uundo="${esc(file.file_path)}">desfazer</button>`
            : ""}
          <span class="hint">${aberto ? "▲" : "▼"}</span>
        </div>
      </div>
      <div class="conflict-body ${aberto ? "" : "collapsed"}">
        ${updateCardBody(file, secao, escolha, dentro, manter)}
      </div>
    </div>
  `;
}

/** A linha de resumo do cabeçalho: o que vai acontecer com este arquivo. */
function updateHeadNote(file, escolha, sujo, dentro, manter) {
  if (escolha) {
    const projeto = escolha.project_title
      ? `<span class="hint">${esc(escolha.project_title)}</span>`
      : "";

    return `
      <span class="conflict-chosen hint">
        <span class="arrow-to">→</span>
        <span class="value mono">${esc(
          escolha.file_name || escolha.version_number || escolha.version_id
        )}${sujo ? " (não salvo)" : ""}</span>
      </span>
      ${projeto}`;
  }

  if (manter) {
    return `<span class="hint">fica na versão atual</span>`;
  }

  if (file.has_version) {
    return file.new_file_name
      ? `<span class="hint mono">${esc(file.from_version || "?")} → ${esc(
          file.to_version
        )}</span>`
      : `<span class="hint">já está na versão mais recente</span>`;
  }

  return dentro
    ? `<span class="hint">entra na versão atual</span>`
    : `<span class="hint">fica de fora do pack</span>`;
}

function updateCardBody(file, secao, escolha, dentro, manter) {
  let body = updateReference(file);

  if (escolha) {
    body += `
      <p class="section-label">escolha atual</p>
      <div class="file-row match">
        <div>
          <div class="name mono">${esc(
            escolha.file_name || escolha.version_number || escolha.version_id
          )}</div>
          <div class="file-meta">
            <span>${esc(escolha.project_title || "mesmo projeto")}</span>
            ${escolha.version_number
              ? `<span class="mono">${esc(escolha.version_number)}</span>`
              : ""}
          </div>
        </div>
      </div>
      <p class="section-label">trocar</p>`;
  } else if (file.has_version) {
    body += `
      <p class="hint">
        ${file.new_file_name
          ? `Vai para <span class="mono">${esc(file.to_version)}</span>
             (<span class="mono">${esc(file.new_file_name)}</span>).`
          : "Já está na versão mais recente para o alvo."}
      </p>
      <div class="row">
        <button class="btn btn-sm ${manter ? "btn-primary" : ""}"
                data-ukeep="${esc(file.file_path)}">
          ${manter ? "voltar a atualizar" : "manter a versão atual"}
        </button>
      </div>
      <p class="section-label">trocar a versão ou o projeto</p>`;
  } else {
    body += `
      <p class="hint">
        ${file.title
          ? `<strong>${esc(file.title)}</strong> não publicou nada para o alvo.`
          : "Não foi identificado no Modrinth."}
        ${dentro
          ? " Vai entrar na versão que já está no pack."
          : " Escolha uma versão abaixo para levá-lo assim mesmo."}
      </p>
      <p class="section-label">escolher uma versão</p>`;
  }

  body += `
    ${updateSearchBox(file)}
    <div data-upanel="${esc(file.file_path)}"></div>`;

  return body;
}

/** O arquivo como está no pack — a referência para comparar com o que vem. */
function updateReference(file) {
  const icone = file.icon
    ? `<img class="logo-thumb" src="${esc(file.icon)}" alt="" loading="lazy">`
    : `<span class="logo-thumb placeholder">${esc(
        (file.title || file.file_name).trim()[0]
      )}</span>`;

  return `
    <div class="mod-reference">
      ${icone}
      <div class="info">
        <div class="name">${esc(file.title || "(não identificado)")}</div>
        <div class="file-meta mono">${esc(file.file_name)}</div>
        ${file.url
          ? `<a class="hint" href="${esc(file.url)}" target="_blank" rel="noreferrer noopener">ver no Modrinth ↗</a>`
          : ""}
      </div>
      <span class="tag">no seu pack</span>
    </div>
  `;
}

function updateSearchBox(file) {
  const sugestao = file.title || file.file_name.replace(/\.(jar|zip).*/i, "");

  return `
    <div class="row">
      <input class="input" data-usearchinput="${esc(file.file_path)}"
             value="${esc(sugestao)}" placeholder="nome do mod no Modrinth">
      <button class="btn btn-sm" data-usearch="${esc(file.file_path)}">Procurar projeto</button>
    </div>
  `;
}

/* ------------------------------------------------------------------ eventos */
function bindUpdateReviewEvents() {
  const lista = $("ur-groups");

  lista.querySelectorAll("[data-utoggle]").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;

      const caminho = el.dataset.utoggle;
      state.openUpdateFile = state.openUpdateFile === caminho ? null : caminho;
      renderUpdateReview();
    });
  });

  lista.querySelectorAll("[data-usearch]").forEach((el) => {
    el.addEventListener("click", () => runUpdateSearch(el.dataset.usearch, true));
  });

  lista.querySelectorAll("[data-usearchinput]").forEach((el) => {
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter") runUpdateSearch(el.dataset.usearchinput, true);
    });
  });

  lista.querySelectorAll("[data-uundo]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.stopPropagation();
      const caminho = el.dataset.uundo;

      state.updatePending[caminho] = null;
      state.updateInclude[caminho] = false;
      afterUpdateDecision();
    });
  });

  lista.querySelectorAll("[data-ubulk]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.stopPropagation();
      const incluir = el.dataset.ubulk === "incluir";

      updateFiles("without_version")
        .filter((file) => !file.is_mod && !pickFor(file))
        .forEach((file) => (state.updateInclude[file.file_path] = incluir));

      afterUpdateDecision();
    });
  });

  lista.querySelectorAll("[data-ukeep]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.stopPropagation();
      const caminho = el.dataset.ukeep;
      const arquivo = allUpdateFiles().find((f) => f.file_path === caminho);
      state.updateKeep[caminho] = !keepFor(arquivo);
      afterUpdateDecision();
    });
  });
}

function afterUpdateDecision() {
  renderUpdateReview();
  renderUpdateJob();
}

const updatePanelFor = (caminho) =>
  $("ur-groups").querySelector(`[data-upanel="${cssEscape(caminho)}"]`);

/** Abrir um card já traz as versões do projeto detectado, sem clicar em buscar. */
function autoLoadOpenUpdateFile() {
  const caminho = state.openUpdateFile;
  if (!caminho) return;

  const painel = updatePanelFor(caminho);
  if (!painel || painel.innerHTML.trim()) return;

  const file = allUpdateFiles().find((f) => f.file_path === caminho);
  if (!file) return;

  const escolha = pickFor(file);
  const projeto =
    (escolha && escolha.project_id) || file.project_id || null;

  if (projeto) {
    showUpdateVersions(caminho, projeto);
  } else {
    runUpdateSearch(caminho);
  }
}

/* ------------------------------------------------------ busca de projetos */
function updateLoader(file) {
  if (!file.is_mod) return "";
  const update = state.updateJob ? state.updateJob.update : null;
  return update ? update.to_loader || "" : "";
}

async function runUpdateSearch(caminho, force) {
  const lista = $("ur-groups");
  const input = lista.querySelector(`[data-usearchinput="${cssEscape(caminho)}"]`);
  const painel = updatePanelFor(caminho);
  if (!input || !painel) return;

  const termo = input.value.trim();
  if (!termo) return;

  const file = allUpdateFiles().find((f) => f.file_path === caminho);
  const loader = file ? updateLoader(file) : "";
  const chave = `${termo.toLowerCase()}|${loader}`;
  const cache = state.updateSearch[caminho];

  if (!force && cache && cache.key === chave) {
    renderUpdateSearchResults(painel, caminho, cache.results);
    return;
  }

  painel.innerHTML = `<p class="hint">procurando no Modrinth…</p>`;

  try {
    const query = loader ? `&loader=${encodeURIComponent(loader)}` : "";
    const data = await api(
      `/api/modrinth/search?q=${encodeURIComponent(termo)}${query}`
    );

    state.updateSearch[caminho] = { key: chave, results: data.results };

    if (!data.results.length) {
      painel.innerHTML = `<p class="hint">Nada encontrado para essa busca.</p>`;
      return;
    }

    renderUpdateSearchResults(painel, caminho, data.results);
  } catch (error) {
    painel.innerHTML = `<p class="hint">erro: ${esc(error.message)}</p>`;
  }
}

function renderUpdateSearchResults(painel, caminho, results) {
  painel.innerHTML = `
    <p class="section-label">${results.length} projeto(s) — clique para ver as versões</p>
    <div class="search-results">
      ${results.map((projeto) => `
        <div class="search-row">
          <div class="project-info">
            ${projeto.icon
              ? `<img class="logo-thumb" src="${esc(projeto.icon)}" alt="" loading="lazy">`
              : `<span class="logo-thumb placeholder">${esc((projeto.title || "?").trim()[0])}</span>`}
            <div>
              <div class="name">${esc(projeto.title)}</div>
              <div class="file-meta">
                <span class="mono">${esc(projeto.slug)}</span>
                <span>${(projeto.downloads || 0).toLocaleString("pt-BR")} downloads</span>
                ${projeto.author ? `<span>por ${esc(projeto.author)}</span>` : ""}
              </div>
              ${projeto.description
                ? `<div class="summary">${esc(projeto.description)}</div>`
                : ""}
            </div>
          </div>
          <button class="btn btn-sm btn-primary"
                  data-upick="${esc(projeto.project_id)}"
                  data-utitle="${esc(projeto.title || "")}"
                  data-ufile="${esc(caminho)}">ver versões</button>
        </div>
      `).join("")}
    </div>
  `;

  painel.querySelectorAll("[data-upick]").forEach((el) => {
    el.addEventListener("click", () =>
      showUpdateVersions(el.dataset.ufile, el.dataset.upick, el.dataset.utitle)
    );
  });
}

/* ---------------------------------------------------- versões de um projeto */
async function showUpdateVersions(caminho, projectId, projectTitle) {
  const painel = updatePanelFor(caminho);
  if (!painel) return;

  const file = allUpdateFiles().find((f) => f.file_path === caminho);
  if (!file) return;

  const loader = updateLoader(file);
  const chave = `${projectId}|${loader}`;

  painel.innerHTML = `<p class="hint">carregando versões do Modrinth…</p>`;

  try {
    if (!state.updateVersions[chave]) {
      const query = loader ? `?loader=${encodeURIComponent(loader)}` : "";
      state.updateVersions[chave] = (
        await api(`/api/modrinth/projects/${projectId}/versions${query}`)
      ).versions;
    }

    let titulo = projectTitle;
    if (!titulo) {
      if (!state.updateProjects[projectId]) {
        try {
          state.updateProjects[projectId] = await api(
            `/api/modrinth/projects/${projectId}`
          );
        } catch (_) {
          state.updateProjects[projectId] = null;
        }
      }
      const info = state.updateProjects[projectId];
      titulo = info ? info.title : projectId === file.project_id ? file.title : "";
    }

    renderUpdateVersionList(
      painel,
      file,
      projectId,
      titulo,
      state.updateVersions[chave]
    );
  } catch (error) {
    painel.innerHTML = `<p class="hint">erro: ${esc(error.message)}</p>`;
  }
}

function renderUpdateVersionList(painel, file, projectId, projectTitle, versoes) {
  if (!versoes.length) {
    painel.innerHTML = `<p class="hint">Esse projeto não tem versões publicadas${
      file.is_mod ? " para o loader escolhido" : ""
    }.</p>`;
    return;
  }

  const update = state.updateJob.update;
  const alvo = update.to_minecraft;
  const escolha = pickFor(file);
  const serve = (v) => (v.game_versions || []).includes(alvo);

  // as que servem no alvo primeiro; dentro de cada grupo, a mais nova antes
  const ordenadas = [...versoes.filter(serve), ...versoes.filter((v) => !serve(v))];
  const compativeis = versoes.filter(serve).length;

  painel.innerHTML = `
    ${projectTitle
      ? `<div class="project-header">
           <div>
             <div class="name">${esc(projectTitle)}</div>
             <div class="file-meta"><span class="mono">${esc(projectId)}</span></div>
           </div>
         </div>`
      : ""}
    <p class="section-label">
      ${versoes.length} versão(ões) · ${compativeis} serve(m) no Minecraft ${esc(alvo)}
    </p>
    <div class="row">
      <input class="input" data-ufilter="${esc(file.file_path)}" placeholder="filtrar versão…">
    </div>
    <div class="file-list">
      ${ordenadas.map((versao) => {
        const mcs = versao.game_versions || [];
        const compativel = serve(versao);
        const atual = escolha && escolha.version_id === versao.id;
        const nome = versao.file.filename || versao.version_number;
        // a que já está no pack: dá para comparar sem sair da tela
        const noPack = mesmoArquivo(nome, file.file_name);

        return `
          <div class="file-row ${atual ? "exact" : ""} ${compativel ? "compatible" : ""} ${noPack ? "in-pack" : ""}"
               data-name="${esc((nome + " " + versao.version_number + " " + mcs.join(" ")).toLowerCase())}">
            <div>
              <div class="name mono">${esc(nome)}</div>
              <div class="file-meta">
                ${atual ? `<span class="exact-tag">escolhida</span>` : ""}
                ${noPack ? `<span class="in-pack-tag">está no seu pack</span>` : ""}
                ${compativel ? `<span class="compat">serve no alvo</span>` : ""}
                ${mcTags([...mcs, ...(versao.loaders || [])], alvo)}
                <span>${esc(versao.version_type)}</span>
              </div>
            </div>
            <button class="btn btn-sm btn-primary"
                    data-uuse="${esc(versao.id)}"
                    data-upath="${esc(file.file_path)}"
                    data-unumber="${esc(versao.version_number || "")}"
                    data-uname="${esc(nome || "")}"
                    data-uproject="${esc(projectId)}"
                    data-uptitle="${esc(projectTitle || "")}">usar esta</button>
          </div>`;
      }).join("")}
    </div>
  `;

  const filtro = painel.querySelector(`[data-ufilter="${cssEscape(file.file_path)}"]`);
  if (filtro) {
    filtro.addEventListener("input", () => {
      const termo = filtro.value.toLowerCase();
      painel.querySelectorAll(".file-row").forEach((linha) => {
        linha.classList.toggle("hidden", !linha.dataset.name.includes(termo));
      });
    });
  }

  painel.querySelectorAll("[data-uuse]").forEach((el) => {
    el.addEventListener("click", () => {
      const caminho = el.dataset.upath;

      state.updatePending[caminho] = {
        version_id: el.dataset.uuse,
        version_number: el.dataset.unumber || null,
        file_name: el.dataset.uname || null,
        project_id: el.dataset.uproject || null,
        project_title: el.dataset.uptitle || null,
      };

      // escolher um arquivo resolve o card: ele leva a escolha e entra no pack
      state.updateInclude[caminho] = true;
      state.updateKeep[caminho] = false;
      state.openUpdateFile = null;

      afterUpdateDecision();
      toast("Escolha registrada — clique em Salvar mudanças", "ok");
    });
  });
}

/* ----------------------------------------------------- salvar e descartar */
function collectDecisions() {
  const choices = [];
  const keep = [];
  const exclude = [];
  const include = [];

  allUpdateFiles().forEach((file) => {
    const escolha = pickFor(file);

    if (escolha && escolha.version_id) {
      choices.push({ file_path: file.file_path, ...escolha });
    }
    if (keepFor(file)) keep.push(file.file_path);
    (includeFor(file) || escolha ? include : exclude).push(file.file_path);
  });

  return { choices, keep, exclude, include };
}

async function saveUpdateDecisions() {
  if (!state.updateJobId) return false;

  try {
    await api(`/api/jobs/${state.updateJobId}/update-resolutions`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectDecisions()),
    });

    state.updatePending = {};
    state.updateInclude = {};
    state.updateKeep = {};

    // relê o job para os cards passarem a mostrar o que ficou salvo
    await pollUpdateJob();
    return true;
  } catch (error) {
    toast(error.message, "error");
    return false;
  }
}

$("btn-ur-discard").addEventListener("click", () => {
  state.updatePending = {};
  state.updateInclude = {};
  state.updateKeep = {};
  afterUpdateDecision();
});

$("btn-ur-save").addEventListener("click", async () => {
  if (await saveUpdateDecisions()) {
    toast(
      'Decisões salvas. Volte em Atualizar e clique em "Aplicar e gerar .mrpack".',
      "ok"
    );
  }
});

/** Aplicar: salva o que estiver pendente e regera o pack. */
/** O que o `apply` vai fazer, contado do que está na tela agora. */
function updatePlan() {
  const update = state.updateJob.update;
  const entram = [];
  let trocam = 0;
  let manuais = 0;
  let fora = 0;

  allUpdateFiles().forEach((file) => {
    const escolha = pickFor(file);
    const dentro = escolha || file.has_version || includeFor(file);

    if (!dentro) {
      fora += 1;
      return;
    }

    entram.push(file);
    if (escolha) manuais += 1;
    else if (file.new_file_name && !keepFor(file)) trocam += 1;
  });

  return {
    entram: entram.length,
    trocam,
    manuais,
    fora,
    mantidos: allUpdateFiles().filter((f) => keepFor(f)).length,
    downgrade: update.downgrade,
    loader: update.loader_changed
      ? `${update.from_loader} → ${update.to_loader}`
      : null,
  };
}

/** Espelha o painel de confirmação do conversor: nada é gerado sem revisar. */
function renderUpdateConfirm(job) {
  const box = $("u-confirm");

  // o painel só existe enquanto dá para aplicar
  const podeAplicar = job && job.update && job.status === "awaiting_review";

  if (!state.updateConfirming || !podeAplicar) {
    state.updateConfirming = false;
    box.classList.add("hidden");
    return;
  }

  const plano = updatePlan();
  const update = job.update;

  const linhas = [
    `<li><strong>${plano.entram}</strong> entram no
     <span class="mono">.mrpack</span> novo</li>`,
    `<li><strong>${plano.trocam}</strong>
     ${plano.downgrade ? "voltam para versões mais antigas" : "vão para versões novas"}
     ${plano.manuais ? `, <strong>${plano.manuais}</strong> escolhidos por você` : ""}</li>`,
  ];

  if (plano.mantidos) {
    linhas.push(`<li><strong>${plano.mantidos}</strong> ficam na versão atual</li>`);
  }

  if (plano.fora) {
    linhas.push(
      `<li><strong>${plano.fora}</strong> ficam <strong>de fora</strong>: sem versão
       para o alvo</li>`
    );
  }



  const mudou = setHTML(box, `
    <h3>
      O que vai acontecer
      <span class="hint">
        ${esc(update.from_minecraft)} → ${esc(update.to_minecraft)}
        ${plano.loader ? `· ${esc(plano.loader)}` : ""}
      </span>
    </h3>
    <ul>${linhas.join("")}</ul>
    <div class="row">
      <button class="btn btn-primary" data-uconfirm="go">Continuar</button>
      <button class="btn btn-ghost" data-uconfirm="back">Voltar</button>
    </div>
  `);
  box.classList.remove("hidden");

  if (!mudou) return;

  box.querySelectorAll("[data-uconfirm]").forEach((el) => {
    el.addEventListener("click", async () => {
      state.updateConfirming = false;
      box.classList.add("hidden");

      if (el.dataset.uconfirm === "back") {
        renderUpdateJob();
        return;
      }

      await runUpdateApply();
    });
  });
}

/** Abre o painel de confirmação (o `apply` de verdade é o `runUpdateApply`). */
function applyUpdate() {
  if (!state.updateJobId) return;

  state.updateConfirming = true;
  goToTab("update");
  renderUpdateJob();
}

async function runUpdateApply() {
  if (unsavedUpdateCount() && !(await saveUpdateDecisions())) return;

  try {
    await post(`/api/jobs/${state.updateJobId}/reapply`);
    startUpdatePolling();
  } catch (error) {
    toast(error.message, "error");
    renderUpdateJob();
  }
}

/* --------------------------------------------------------------------- cache */
async function loadCacheSize() {
  try {
    const data = await api("/api/cache");
    setText($("cache-size"), data.size_mb ? `${data.size_mb} MB` : "vazio");
    $("btn-clear-cache").disabled = !data.files.length;
  } catch (_) {
    setText($("cache-size"), "");
  }
}

/* ------------------------------------------------------------------ sair */
$("btn-quit").addEventListener("click", async () => {
  const botao = $("btn-quit");

  // dois cliques: fechar o servidor no meio de um download seria irreversível
  if (botao.dataset.armed !== "1") {
    botao.dataset.armed = "1";
    botao.classList.add("btn-danger");
    setText(botao, "Encerrar mesmo?");
    setTimeout(() => {
      botao.dataset.armed = "";
      botao.classList.remove("btn-danger");
      setText(botao, "Encerrar");
    }, 4000);
    return;
  }

  try {
    const dados = await post("/api/shutdown");
    stopPolling();
    stopUpdatePolling();

    setHTML(
      document.body,
      `<div class="bye">
         <h1>Servidor encerrado</h1>
         <p class="hint">
           ${dados.cancelled.length
             ? `${dados.cancelled.length} trabalho(s) cancelado(s) antes de sair.`
             : "Nada estava em andamento."}
           Pode fechar esta aba.
         </p>
       </div>`
    );
  } catch (error) {
    toast(error.message, "error");
  }
});

$("btn-clear-cache").addEventListener("click", async () => {
  const botao = $("btn-clear-cache");
  botao.disabled = true;

  try {
    const data = await api("/api/cache", { method: "DELETE" });

    if (data.locked.length) {
      toast(
        `Cache parcialmente limpo: ${data.locked.join(", ")} está em uso. ` +
          "Feche a interface e rode `mrpack2curseforge clear-cache`.",
        "warn"
      );
    } else if (data.removed.length) {
      toast(`Cache limpo — ${data.freed_mb} MB liberados`, "ok");
    } else {
      toast("Não havia cache para limpar", "ok");
    }
  } catch (error) {
    toast(error.message, "error");
  }

  botao.disabled = false;
  loadCacheSize();
});

/* =========================================================================
   CONFIGURAÇÕES — um editor do `.env`, não um banco de preferências

   O arquivo continua sendo a fonte da verdade: o painel lê dele ao abrir e
   escreve nele ao salvar. A chave da API chega mascarada do servidor e só é
   enviada de volta quando você digita uma nova.
   ========================================================================= */

const SETTINGS = {
  aberto: false,
  campos: [],       // como vieram do servidor
  rascunho: {},     // chave -> valor digitado, só o que mudou
  revelando: {},    // chave -> mostrar o segredo em texto
  travado: null,    // nome do trabalho aberto que impede editar agora
};

const settingsSujo = () => Object.keys(SETTINGS.rascunho).length > 0;

/** O valor que o campo deve mostrar agora: o digitado, ou o do servidor. */
function settingsValor(campo) {
  if (Object.prototype.hasOwnProperty.call(SETTINGS.rascunho, campo.chave)) {
    return SETTINGS.rascunho[campo.chave];
  }

  // o segredo vem mascarado: nunca é o valor de verdade
  return campo.tipo === "secret" ? "" : campo.valor;
}

async function abrirSettings() {
  try {
    const dados = await api("/api/settings");
    SETTINGS.campos = dados.campos;
    SETTINGS.travado = dados.locked_by || null;
    setText($("settings-path"), dados.path);
  } catch (error) {
    toast(error.message, "error");
    return;
  }

  SETTINGS.aberto = true;
  SETTINGS.rascunho = {};
  SETTINGS.revelando = {};

  $("settings-panel").classList.remove("hidden");
  $("btn-settings").classList.add("open");
  $("btn-settings").setAttribute("aria-expanded", "true");

  renderSettings();
}

function fecharSettings() {
  SETTINGS.aberto = false;
  SETTINGS.rascunho = {};

  $("settings-panel").classList.add("hidden");
  $("btn-settings").classList.remove("open");
  $("btn-settings").setAttribute("aria-expanded", "false");
  $("settings-notice").classList.add("hidden");
}

function renderSettings() {
  if (!SETTINGS.aberto) return;

  let grupoAtual = null;
  const blocos = [];

  SETTINGS.campos.forEach((campo) => {
    if (campo.grupo !== grupoAtual) {
      grupoAtual = campo.grupo;
      blocos.push(`<p class="setting-group">${esc(grupoAtual)}</p>`);
    }
    blocos.push(campoSettings(campo));
  });

  if (setHTML($("settings-fields"), blocos.join(""))) bindSettings();

  // metade das configurações é lida enquanto o trabalho roda: mexer no meio
  // daria um resultado que não é nem o antigo nem o novo
  const travado = !!SETTINGS.travado;
  $("settings-fields").classList.toggle("locked", travado);
  $("btn-settings-reset").disabled = travado;

  if (travado) {
    avisoSettings(
      `Há um trabalho aberto (<span class="mono">${esc(SETTINGS.travado)}</span>).
       Feche-o para mexer nas configurações.`,
      "warn"
    );
  }

  marcarSettingsSujo();
  renderSettingsBotoes();
}

/**
 * Título do campo. Quem ainda não tem o valor ganha ao lado a etiqueta para a
 * página onde ele é obtido; com o valor já configurado ela some — vira ruído.
 */
function tituloSettings(campo, id) {
  // só o símbolo: sem texto, o `title`/`aria-label` é o que diz para onde vai
  const link = campo.link && !campo.definido
    ? `<a class="tag setting-link" href="${esc(campo.link)}"
          target="_blank" rel="noreferrer noopener"
          title="Abrir a página onde este valor é obtido"
          aria-label="Abrir a página onde este valor é obtido">↗</a>`
    : "";

  return `<div class="setting-title">
            <label for="${id}">${esc(campo.rotulo)}</label>${link}
          </div>`;
}

function campoSettings(campo) {
  const valor = settingsValor(campo);
  const id = `set-${campo.chave}`;
  const titulo = tituloSettings(campo, id);

  if (campo.tipo === "secret") {
    const revelar = SETTINGS.revelando[campo.chave];
    const temChave = campo.definido;

    return `
      <div class="setting">
        ${titulo}
        <span class="hint">${esc(campo.ajuda)}</span>
        <div class="secret-row">
          <input class="input" id="${id}" data-set="${esc(campo.chave)}"
                 type="${revelar ? "text" : "password"}"
                 value="${esc(valor)}"
                 placeholder="${temChave ? esc(campo.valor) : "nenhuma chave salva"}">
          <button class="btn btn-sm" data-reveal="${esc(campo.chave)}"
                  title="${revelar ? "esconder" : "mostrar o que você digitou"}">
            ${revelar ? "🙈" : "👁"}
          </button>
        </div>
        ${temChave
          ? `<div class="row secret-actions">
               <button class="btn btn-ghost btn-sm ${armados["set-forget"] ? "btn-danger" : ""}"
                       id="btn-settings-forget" ${SETTINGS.travado ? "disabled" : ""}>
                 ${rotuloArmado("set-forget", "Apagar chave salva")}
               </button>
             </div>`
          : ""}
      </div>`;
  }

  if (campo.tipo === "texto") {
    return `
      <div class="setting">
        ${titulo}
        <span class="hint">${esc(campo.ajuda)}</span>
        <input class="input full" id="${id}" data-set="${esc(campo.chave)}"
               value="${esc(valor)}" placeholder="padrão do projeto">
      </div>`;
  }

  // numérico: slider para procurar, caixa para acertar
  const atual = valor === "" ? campo.padrao : valor;

  return `
    <div class="setting">
      ${titulo}
      <span class="hint">${esc(campo.ajuda)}</span>
      <div class="row">
        <input type="range" data-set-range="${esc(campo.chave)}"
               min="${campo.minimo}" max="${campo.maximo}" step="${campo.passo}"
               value="${esc(atual)}">
        <input class="input" type="number" id="${id}" data-set="${esc(campo.chave)}"
               min="${campo.minimo}" max="${campo.maximo}" step="${campo.passo}"
               value="${esc(atual)}"
               placeholder="${esc(campo.padrao)}">
      </div>
    </div>`;
}

function bindSettings() {
  const painel = $("settings-fields");

  painel.querySelectorAll("[data-set]").forEach((el) => {
    el.addEventListener("input", () => {
      SETTINGS.rascunho[el.dataset.set] = el.value;

      // o slider irmão acompanha, sem redesenhar o campo inteiro
      const par = painel.querySelector(`[data-set-range="${cssEscape(el.dataset.set)}"]`);
      if (par) par.value = el.value;

      marcarSettingsSujo();
    });
  });

  painel.querySelectorAll("[data-set-range]").forEach((el) => {
    el.addEventListener("input", () => {
      const chave = el.dataset.setRange;
      SETTINGS.rascunho[chave] = el.value;

      const caixa = painel.querySelector(`[data-set="${cssEscape(chave)}"]`);
      if (caixa) caixa.value = el.value;

      marcarSettingsSujo();
    });
  });

  painel.querySelectorAll("[data-reveal]").forEach((el) => {
    el.addEventListener("click", (event) => {
      // sem isto o clique borbulha até o `document`, e como o redesenho abaixo
      // tira o botão do DOM, o `closest(".settings-wrap")` de lá dá null — o
      // painel se fechava sozinho ao revelar a chave
      event.stopPropagation();

      const chave = el.dataset.reveal;
      SETTINGS.revelando[chave] = !SETTINGS.revelando[chave];
      renderSettings();
    });
  });

  const apagar = painel.querySelector("#btn-settings-forget");
  if (apagar) {
    apagar.addEventListener("click", (event) => {
      event.stopPropagation();
      apagarChave();
    });
  }
}

/** Atualiza só o que depende de "há mudança", sem reescrever os campos. */
function marcarSettingsSujo() {
  const sujo = settingsSujo();

  $("settings-dirty").classList.toggle("hidden", !sujo);
  $("btn-settings-save").disabled = !sujo || !!SETTINGS.travado;
  $("btn-settings-discard").disabled = !sujo;
}

function avisoSettings(texto, tom) {
  const aviso = $("settings-notice");
  setClass(aviso, `notice ${tom}`);
  setHTML(aviso, texto);
  aviso.classList.remove("hidden");
}

async function salvarSettings() {
  try {
    const dados = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values: SETTINGS.rascunho }),
    });

    SETTINGS.campos = dados.state.campos;
    SETTINGS.rascunho = {};
    renderSettings();

    if (dados.restart_needed.length) {
      avisoSettings(
        `Salvo. <strong>${dados.restart_needed.join(", ")}</strong> só vale ` +
          "depois de reiniciar o servidor.",
        "warn"
      );
    } else {
      avisoSettings("Salvo.", "ok");
    }

    await loadState();
  } catch (error) {
    avisoSettings(esc(error.message), "bad");
  }
}

async function aplicarSettings(rota, chaveBotao, pergunta, sucesso) {
  if (!armarBotao(chaveBotao, pergunta)) return renderSettings();

  try {
    const dados = await api(rota, { method: "POST" });

    SETTINGS.campos = dados.state.campos;
    SETTINGS.rascunho = {};
    renderSettings();

    avisoSettings(sucesso, "ok");
    await loadState();
  } catch (error) {
    avisoSettings(esc(error.message), "bad");
  }
}

const restaurarSettings = () =>
  aplicarSettings(
    "/api/settings/reset",
    "set-reset",
    "Restaurar mesmo?",
    "Tudo de volta ao padrão. A chave da API foi preservada."
  );

/** Apaga só o segredo: o resto das configurações fica como está. */
const apagarChave = () =>
  aplicarSettings(
    "/api/settings/forget-key",
    "set-forget",
    "Apagar a chave mesmo?",
    "Chave da API apagada do .env. As outras configurações não mudaram."
  );

function renderSettingsBotoes() {
  setText($("btn-settings-reset"), rotuloArmado("set-reset", "Restaurar padrão"));
  $("btn-settings-reset").classList.toggle("btn-danger", !!armados["set-reset"]);
}

$("btn-settings").addEventListener("click", (event) => {
  event.stopPropagation();

  if (SETTINGS.aberto) {
    // fechar pelo próprio botão só vale se não houver nada pendente
    if (settingsSujo()) {
      return avisoSettings("Salve ou descarte antes de fechar.", "warn");
    }
    return fecharSettings();
  }

  abrirSettings();
});

$("btn-settings-save").addEventListener("click", salvarSettings);

$("btn-settings-discard").addEventListener("click", () => {
  SETTINGS.rascunho = {};
  $("settings-notice").classList.add("hidden");
  renderSettings();
});

$("btn-settings-reset").addEventListener("click", restaurarSettings);

/* Clicar fora fecha — mas só quando não há alteração pendente. Fechar sozinho
   com coisa digitada perderia o que você escreveu sem avisar. */
document.addEventListener("click", (event) => {
  if (!SETTINGS.aberto) return;
  if (event.target.closest(".settings-wrap")) return;

  if (settingsSujo()) {
    avisoSettings("Salve ou descarte antes de fechar.", "warn");
    return;
  }

  fecharSettings();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && SETTINGS.aberto && !settingsSujo()) fecharSettings();
});

/* --------------------------------------------------------------------- init */
selectTool("converter");
loadState().catch((error) => toast(error.message, "error"));
loadMinecraftVersions();
loadLoaders();
loadCacheSize();
