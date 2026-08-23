/* O DOM de mentira em que o `app.js` roda fora do navegador.
 *
 * Compartilhado por dois usos:
 *
 *   tests/ui/check_ui.js     — asserções sobre cada estado, com dados escritos à mão
 *   tests/ui/render_real.js  — a mesma tela, mas com o payload de um job de verdade
 *
 * A segunda existe porque a primeira tem um ponto cego: um fixture escrito à mão
 * sempre tem o campo que o servidor esqueceu de mandar.
 */

"use strict";

const fs = require("fs");
const vm = require("vm");
const path = require("path");

const STATIC = path.join(
  __dirname, "..", "..", "src", "mrpack2curseforge", "web", "static"
);

/** Cria a classe de elemento, ligada ao coletor de avisos daquela execução. */
function criarEl(avisos) {
  return class El {
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
      // conta que virou NaN: quase sempre um campo do payload que mudou de nome
      if (/(^|[>\s])NaN([<\s.,)]|$)/.test(texto)) {
        avisos.push(`"NaN" no HTML de #${this.id}`);
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
  };
}

/**
 * Sobe o `app.js` num contexto isolado.
 *
 * Devolve o coletor de avisos, o mapa de elementos (semeado pelos `id=` do
 * index.html), a `<meta>` de versão e um `pegar()` que alcança as funções e o
 * `state`, que vivem no escopo léxico do contexto.
 */
function montar({ respostas } = {}) {
  const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
  const js = fs.readFileSync(path.join(STATIC, "app.js"), "utf8");

  const avisos = [];
  const El = criarEl(avisos);

  const elementos = new Map(
    [...html.matchAll(/id="([^"]+)"/g)].map((m) => [m[1], new El(m[1])])
  );

  // a versão que a "página" declara; os testes trocam para simular cache velho
  const metaVersao = new El("meta-app-version");
  metaVersao.content = "9.9.9";

  const sandbox = {
    document: {
      getElementById: (id) => {
        if (!elementos.has(id)) elementos.set(id, new El(id));
        return elementos.get(id);
      },
      querySelector: (sel) =>
        sel === 'meta[name="app-version"]' ? metaVersao : null,
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
      json: async () => (respostas ? respostas(String(url)) : {}),
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

  return {
    html,
    // o fonte cru: algumas asserções olham o próprio código, e não o resultado
    js,
    avisos,
    elementos,
    metaVersao,
    El,
    pegar: (expr) => vm.runInContext(expr, sandbox),
  };
}

module.exports = { STATIC, montar };
