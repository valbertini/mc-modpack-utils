# mrpack2curseforge — contexto para agentes

## Objetivo

Duas ferramentas sobre modpacks `.mrpack` (Modrinth), na mesma base de código:

1. **Converter** para o CurseForge — não é tradução de manifest: o projeto
   **procura os projetos equivalentes** e só aceita um match quando encontra lá o
   mesmo arquivo. Vale para mod, resourcepack e shader — e também para o que já
   viajava dentro do `overrides/` do mrpack. O que não for encontrado é baixado
   do Modrinth e vai para `overrides/`.
2. **Atualizar os mods** para outra versão do Minecraft — para cada arquivo do
   índice, pega no Modrinth a versão mais recente compatível com a versão alvo e
   monta um `.mrpack` novo. Nada é baixado (o índice já tem URL e hashes).

Decisões de projeto (e o porquê de cada uma) ficam em **`DECISIONS.md`** — leia
antes de mudar comportamento.

---

## Stack

Python 3.12+ · uv · httpx · pydantic · typer · rich · pytest

---

## Estrutura

```
input_modpacks/        entrada (.mrpack) — não é modificada pela conversão
output_modpacks/       .zip gerado + conversions/*.json (registros persistentes)
.cache/                cache das APIs (regenerável)

src/mrpack2curseforge/
├── cli.py                    CLI (comando padrão = converter input_modpacks/)
│                             + `web` sobe a interface local
├── converter.py              conversão: analyze() + finish(), cancelamento
├── updater.py                atualização: .mrpack -> outra versão do Minecraft
├── progress.py               Reporter: ConsoleReporter (rich) | JobReporter (web)
├── records.py                registros em conversions/*.json + regeneração
├── web/
│   ├── server.py             monta o app: CSP, estáticos e os routers
│   ├── context.py            AppContext: pastas, JobManager, clientes, guardas
│   ├── schemas.py            corpos das requisições (pydantic)
│   ├── payloads.py           o que a tela consome: pack, projeto, log e
│   │                         atualização — tudo sem FastAPI
│   ├── routes/               um módulo por assunto, cada um expõe router(ctx)
│   │   ├── packs.py          estado da tela, upload, inspeção
│   │   ├── jobs.py           iniciar, revisar, aplicar, cancelar, baixar
│   │   ├── updates.py        packs atualizados já salvos
│   │   ├── records.py        conversões salvas + regeração
│   │   ├── catalog.py        Minecraft, loaders, Modrinth, CurseForge
│   │   └── system.py         configurações, cache, encerrar
│   ├── jobs.py               Job/JobManager: conversão em thread + resoluções
│   │                         (só o ciclo de vida; payload é do payloads.py)
│   └── static/               index.html + style.css + app.js (zero dependências)
├── config.py                 .env, paths e limites
├── settings.py               editor do .env usado pela tela de configurações
├── constants.py              endpoints, ids da API, page sizes
├── domain.py                 PackFile (folder/disabled/override_path), Modpack
│                             (convertible/plain_extras/override_bytes),
│                             MatchResult, MatchStrategy, Diagnosis,
│                             MissingReason
├── reporting.py              ConversionReport (só os números) + tabela do CLI
├── exceptions.py
├── parsers/mrpack.py         zip -> domínio (não conhece CurseForge)
├── schemas/modrinth.py       schema do modrinth.index.json
├── services/
│   ├── http.py               retentativa, backoff e 429 — a política dos dois
│   │                         clientes, num lugar só
│   ├── modrinth.py           SHA1 -> project_id -> slug/title (em lote)
│   ├── curseforge.py         HTTP puro: search, files, cache (sem heurística)
│   ├── matcher.py            ⭐ toda a heurística mora aqui
│   ├── downloader.py         download + verificação de SHA1
│   └── cache.py              cache persistente em SQLite (thread-safe)
└── builders/
    ├── curseforge_manifest.py  manifest.json + modlist.html (sem rede)
    ├── mrpack.py               índice + .mrpack do atualizador
    └── package.py              zip final

tests/                 pytest, sem rede (cliente do CurseForge é falso)
└── ui/                a interface, sem navegador (node, sem dependências)
    ├── fake_dom.js    o DOM de mentira em que o app.js roda
    ├── check_ui.js    asserções por estado, com dados escritos à mão
    └── render_real.js a mesma tela com o payload de um job de verdade
tools/                 o que você roda, não o que é verificado
├── check_all.py       a bateria inteira num comando
└── capture_job.py     captura o payload real (precisa de rede e da chave)
```

A divisão entre as duas pastas é essa: **`tests/` afirma, `tools/` faz.**
`check_ui.js` e `render_real.js` quebram a bateria quando algo está errado, então
são teste — deixá-los em `tools/` escondia 128 asserções de quem procura em
`tests/`. O `capture_job.py` não afirma nada e **precisa de rede**, o que o
proíbe de morar em `tests/`.

Antes de dar por pronto, **um comando**:

```powershell
uv run python tools/check_all.py
```

Roda o `pytest` (193 testes, nenhum toca a rede), o `flake8` (88 colunas,
importes e variáveis sem uso), o `tests/ui/check_ui.js` (128 asserções sobre os
estados da interface) e o `node --check`. É um script para não precisar de pipe
nem `$(...)` no shell — os dois travam a sessão pedindo aprovação.

---

## Fluxo

```
CLI
 └─ Converter
     ├─ MrpackParser        .mrpack -> Modpack (mods, extra_files, overrides)
     ├─ ModrinthClient      hashes -> project_id -> slug/title (2 requisições)
     ├─ CurseForgeMatcher   por mod, em paralelo -> MatchResult
     ├─ Downloader          não convertidos + não-mods -> overrides/
     ├─ ManifestBuilder     manifest.json + modlist.html
     ├─ build_zip           output_modpacks/<pack>.zip
     └─ save_record         output_modpacks/conversions/<pack>.json
```

O `manifest.json` sai no formato do export oficial: `overrides` antes de
`files`, `isLocked: false` em cada entrada e `required: false` para o que era
`.jar.disabled` no mrpack (é assim que o launcher reinstala um mod desligado).
Fica de fora só o `image`/`profileImage/`, que aponta para o ícone da instância —
o `.mrpack` não tem ícone nenhum.

Regras de camada:

- o parser **nunca** conhece CurseForge;
- o builder **nunca** interpreta mrpack e **nunca** faz rede;
- toda heurística de matching fica **apenas** em `services/matcher.py`;
- `services/curseforge.py` é transporte puro (sem heurística);
- respostas da API passam por `slim_project`/`slim_file` antes de circular: só os
  campos usados sobrevivem (o resto inflava o cache em ~7x e não servia para nada);
- retentativa, backoff e `429` moram **só** em `services/http.py`: os dois
  clientes tinham o mesmo laço em cópias que já começavam a divergir.

---

## Matcher (o coração)

**A pasta do arquivo escolhe a seção.** `mods/` → `classId` 6, `resourcepacks/`
→ 12, `shaderpacks/` → 6552 (`CURSEFORGE_CLASSES`). Sem isso, procurar
"Low Shield" devolvia 150 mods e nenhum texture pack. A mesma tabela dá a seção
da URL do site (`CURSEFORGE_SECTIONS`), que só é `mc-mods` para mod.

Para cada arquivo, na ordem, parando na primeira que confirmar:

1. `MODRINTH_SLUG` — lookups exatos (`?slug=`), na ordem: slug do Modrinth, título
   slugificado e slugs das variações. São 0-ou-1 resultado, então vêm primeiro.
   Para não-mods, cada slug é tentado também com o prefixo `minecraft-`: o
   texture pack `3d-default` do Modrinth é `minecraft-3d-default` lá, e a busca
   textual não o devolve de jeito nenhum
2. `MODRINTH_TITLE` — busca textual pelo título do Modrinth
3. `MODRINTH_VARIANT` — outras grafias (`name_variants`, que usa `split_words`
   para entender CamelCase/snake_case): junta (`ExtendedAE`), separa
   (`Vitality Fix`), snake (`extended_ae`) e junções de um espaço por vez
4. `MODRINTH_LOADER` — nome + loader do pack (`Things fabric`). **Só para mod**:
   resourcepack não tem loader, e o termo só estragaria a busca
5. `FILENAME_REGEX` — busca pela consulta derivada do nome do arquivo
6. `FILENAME_SIMPLE` — primeiro token relevante

`rank_projects` usa `clean_project_name`, que remove sufixos entre colchetes ou
parênteses: sem isso "Better Combat [Fabric & Forge]" caía na 14ª posição e nunca
era inspecionado (só olhamos os 8 primeiros candidatos).

Confirmação (invariante do projeto):

> Um candidato só vira match se algum arquivo dele tiver **exatamente o mesmo
> nome** do `.jar` original (`normalize_file_name`: minúsculas, sem extensão, sem
> `.disabled`, com `%2B`/`%20` decodificados). A **versão continua contando**.

Empate dentro do projeto (`_pick_file`): o nome nem sempre identifica uma
release. `Low Shield.zip` é o nome de 40 arquivos do mesmo projeto, um por versão
do Minecraft; e o Cloth Config publica o jar de Fabric e o de NeoForge **com o
mesmo nome**. A ordem do desempate é `(loader, versão do Minecraft, data)`.

`_loader_rank` tem quatro degraus: declara o loader do pack (0) · declara outro
que o pack aceita (1 — Quilt roda mod de Fabric) · não declara nada (2 — arquivo
antigo, ou resourcepack) · declara só loader incompatível (3). **O 3 não veta**:
num pack Forge com Sinytra Connector há mods de Fabric de propósito, e recusá-los
mandaria 6 matches certos para `overrides/`. Em vez disso ele vira *reserva* —
`_find_file_in_candidates` continua procurando e só o usa se nada melhor
aparecer.

**Arquivos que já estavam em `overrides/`** (`Modpack.override_candidates`)
também são procurados, com `diagnose=False`: achar tira o arquivo de lá e põe no
manifest; não achar é o normal deles, e não vira conflito nem gasta diagnóstico.
O parser só oferece `mods/`, `resourcepacks/` e `shaderpacks/` na raiz, e só
`.jar`/`.zip` — `.zip.rpo` e `.zip.txt` são packs que o launcher desligou
renomeando, e não existem no CurseForge com esse nome.

Economia de chamadas:

- `latestFiles` (grátis, vem da busca) é varrido primeiro, em todos os candidatos;
- listagem de arquivos só nos 8 melhores candidatos, filtrada pela versão do MC;
- histórico completo (20 páginas) só nos 3 melhores;
- projetos já inspecionados não são reinspecionados nas estratégias seguintes.

Funções puras e testáveis: `normalize_mod_name`, `simple_mod_name`,
`normalize_file_name`, `similarity`, `symmetric_similarity`, `file_similarity`.
O separador de CamelCase do `normalize_mod_name` só corta **depois de letra**:
cortar depois de dígito partia "3D Default" em "3 D" e o filtro de tokens comia
os dois pedaços, sobrando `"default"`.

### Diagnóstico (`CurseForgeMatcher.diagnose`)

Roda **só** quando as 4 estratégias falham. Responde: o mod não está no CurseForge
ou está e só falta aquela versão?

1. referências = arquivo local + os `RECENT_FILES` (10) arquivos mais recentes do
   projeto **no Modrinth** (`GET /project/{id}/version`);
2. para cada um dos `DIAGNOSIS_CANDIDATES` (5) melhores candidatos, pega os 10
   arquivos mais recentes do CurseForge (`latestFiles` + 1ª página de `/files`);
3. compara todos contra todos com `file_similarity` e guarda o melhor par;
4. `>= VERSION_THRESHOLD` (0.85) → `MissingReason.VERSION_UNAVAILABLE`, senão
   `MissingReason.NOT_ON_CURSEFORGE`.

`file_similarity` = média entre a similaridade dos nomes completos e a dos nomes
sem versão/loader, ambas simetrizadas (`SequenceMatcher` **não** é simétrico).
Calibração medida: mesma família 0.85–0.98, mods diferentes ≤ 0.76.

O diagnóstico **não muda o manifest** — o mod continua indo para `overrides`. Ele
só popula `MatchResult.diagnosis` (projeto, link, versão mais próxima, similaridade,
qual arquivo do Modrinth casou) para o relatório.

---

## Estado atual (v0.24)

Pack "Otimizado 1.21.11" (48 mods + 10 não-mods no índice + 3 candidatos em
`overrides/`), comparado com o export feito pelo próprio launcher do CurseForge:
**55 entradas no manifest contra 54 deles, e as 54 estão todas lá**. A 55ª é um
resourcepack que o mrpack levava em `overrides/` e que o CurseForge publica. Só
ficam de fora 5 mods sem aquela versão lá — exatamente os mesmos 5 que o export
oficial também deixou em `overrides/`.

Quatro packs reais medidos com e sem o desempate por loader (v0.25): **7
arquivos corrigidos, nenhum match perdido** — todos eram jars de Forge indo para
packs de Fabric, com o mesmo nome do jar certo.

O mesmo modpack instalado nos dois launchers, comparado pasta a pasta (v0.25.1):
**48/48 mods e 3/3 shaders byte a byte**, 11/11 resourcepacks (um recompactado,
mesmas 439 entradas e mesmos CRCs) e 323/323 config (três diferem só no carimbo
de hora que o jogo escreve ao abrir).

Pack de teste com 49 mods: **45 convertidos / 4 em overrides (91.8%)**, ~17s na
primeira execução e ~8s com cache. Os 4 restantes existem no CurseForge, mas sem
aquela versão publicada (todos diagnosticados como `version-unavailable`).

Pack grande (Prominence II, 406 mods): **400 no manifest / 6 conflitos** (1 sem
projeto, 5 sem a versão); análise em ~42s com cache frio e poucos segundos com
cache quente.

Já implementado: parser completo (incluindo `client-overrides/`), resolução via
Modrinth, matching por arquivo, paralelismo, cache persistente, retries com
backoff e tratamento de 429, downloads com SHA1, zip final, relatório JSON,
`modlist.html`, testes offline.

---

## Atualizador (`updater.py`)

Duas fases, como o conversor — a análise não escreve nada:

```
Updater.analyze(pack, minecraft_version, loader_version?)
 ├─ MrpackParser              .mrpack -> Modpack
 ├─ ModrinthClient            hashes -> projetos (em lote)
 └─ latest_version(projeto)   por arquivo, em paralelo -> UpdateResult

Updater.apply(outcome, resolutions?, skips?)
 ├─ _apply_choices            versões manuais + o que o usuário mandou manter
 ├─ build_index               modrinth.index.json novo
 └─ build_mrpack              índice + overrides copiados do pack de origem
```

- **Nada é baixado**: o índice do Modrinth guarda URL, tamanho e hashes, então a
  atualização é só consulta (e o `overrides/` é copiado entrada por entrada).
- `UpdateStatus`: `UPDATED` · `UNCHANGED` (mesmo sha1) · `INCOMPATIBLE` (o projeto
  não publicou nada para o alvo) · `UNKNOWN` (não identificado no Modrinth) ·
  `MANUAL` (versão escolhida à mão). `UpdateResult.has_version` separa os dois
  primeiros (+ manual) dos dois últimos, e é o que divide as abas da revisão.
- **A revisão do atualizador é a aba de conflitos do conversor, de novo.** Uma aba,
  três seções (`UPDATE_SECTIONS`), o mesmo par *Salvar mudanças* / *Aplicar
  mudanças*, as mesmas classes CSS (`.conflict-section`, `.conflict`,
  `.conflict-head`…). O que muda é a origem do card resolvido, que ganha cor
  própria: `.from-missing` (verde, veio do sem-versão) × `.from-version`
  (dourado, você contrariou a proposta automática). Estado pendente no front:
  `state.updatePending` / `updateInclude` / `updateKeep`, do mesmo jeito que
  `state.pending` na aba de conflitos.
- **Nada sem versão entra sozinho** (`default_excluded` = `not has_version`). O
  padrão é conservador de propósito: entrar no pack é a decisão que quebra o
  jogo, então é sempre do usuário. Quem resolve é escolher uma versão (o card vai
  para *Resolvidos*) ou o botão no topo da seção que inclui **todos os que não
  são mods** de uma vez.
- **A revisão decide quem entra no pack**, não só qual versão usar:
  `UpdateResult.skipped` = "mantenha a versão atual"; `excluded` = "não entra no
  pack novo". `final_file` respeita os dois, e `build_index` pula os excluídos.
  O padrão vem de `default_excluded()`: sem versão para o alvo, **mod fica de
  fora** (um `.jar` de outra versão quebra o jogo) e **não-mod entra**
  (resourcepack/shader costuma funcionar além da versão publicada).
  `UpdateDecisions(versions, keep, exclude, include)` carrega a escolha do
  usuário; `_decided_exclusion()` só cai no padrão quando não há decisão.
  `remember_auto()`/`restore_auto()` guardam o resultado automático para dar
  desfazer.
- **Um trabalho aberto por ferramenta**: `JobManager.current(kind)`. Conversão e
  atualização rodam ao mesmo tempo, cada uma com job, polling e estado próprios no
  front (`state.job` × `state.updateJob`).
- Escolha da versão: `release` primeiro, `beta`/`alpha` só se não houver release;
  entre iguais, a mais recente (`ModrinthClient._pick_version`).
- Só mods levam filtro de loader; resourcepack e shader não.
- Preservados: `env` do índice (client/server) e o sufixo `.disabled`
  (atualizar não pode reativar um mod que estava desligado).
- `ModrinthClient` tem limitador de vazão (240 req/min, abaixo dos 300 da API),
  necessário porque a atualização faz uma consulta por projeto.
- Alvo anterior ao Minecraft do pack é sinalizado (`UpdateOutcome.downgrade`): os
  mods vão para versões mais antigas, e a interface avisa.
- **Escolha manual de versão — e de projeto.** Vale nas duas pontas: em *Com
  versão* para trocar a versão proposta, em *Sem versão* para achar algo
  utilizável. `/api/modrinth/search` procura outro projeto (o mod certo pode ser
  um fork ou ter sido renomeado) e `/api/modrinth/projects/{id}/versions` lista
  **todas** as versões publicadas (sem filtrar por Minecraft — é o ponto); o front
  põe as compatíveis com o alvo no topo. `ManualPick` carrega `version_id` +
  metadados só para a interface; `_retag_project()` troca o `result.modrinth`
  quando a versão vem de outro projeto, e `auto_modrinth` guarda o detectado para
  o desfazer voltar ao original.
- **Versões do loader** vêm de `services/loaders.py`: nem o Modrinth nem o
  CurseForge listam isso, então cada loader tem o seu serviço (fabric/quilt meta,
  maven do neoforge/forge). O `_fetch` **retenta**: o maven do NeoForge devolve
  404 esporádico para uma URL que existe (~1 em 3 na primeira chamada). Falha de
  rede devolve lista vazia e **não** é cacheada — senão um serviço fora do ar
  envenenaria o cache; a interface aceita a lista vazia e segue.
- **Trocar de modloader** (`Updater.analyze(..., loader=)`) é, na prática, só um
  filtro diferente: `latest_version(..., loader=alvo)` em vez do loader do pack.
  O que muda além disso é o índice — `build_index(..., loader=)` troca a chave da
  dependência (`fabric-loader` → `neoforge`) e marca o `versionId`. Trocar de
  loader **exige `loader_version`**: a versão do fabric que está no pack não serve
  para o neoforge, e chutar geraria um índice inválido — melhor recusar com uma
  mensagem clara (`Mrpack2CurseForgeError`) do que entregar um pack quebrado.

---

## Interface web

`uv run mrpack2curseforge web` → <http://127.0.0.1:8000> (FastAPI + uvicorn).

- **Hospedagem 100% local, sem build**: `static/` tem só HTML, CSS e JS escritos à
  mão, servidos pela própria aplicação. Bibliotecas são permitidas, mas devem ser
  **vendorizadas em `static/`** — nunca referenciadas por CDN, senão a página passa
  a exigir internet para abrir (a CSP `script-src 'self'` bloqueia isso).
  `img-src https:` é liberado de propósito: os ícones dos projetos do CurseForge
  aparecem na busca e na lista de versões, com placeholder quando offline.
- **Conversão em duas fases** (`Converter.analyze` / `Converter.finish`): a análise
  só consulta APIs; nada é baixado ou escrito antes de `finish`. É isso que permite
  parar no meio para o usuário resolver conflitos.
- **Ciclo de vida do job** (`web/jobs.py`):
  `running → awaiting_conflicts → finishing → done` (sem conflitos, vai direto para
  `finishing`). `cancelled`/`error` são finais. Só existe **um job por vez**;
  `POST /api/jobs/{id}/close` libera a vaga (`/api/convert` responde 409 antes disso).
- **Cancelamento** via `threading.Event` (`Converter.cancel_event`), checado entre
  mods, entre downloads e **entre chunks** de cada arquivo (`Downloader.cancelled`),
  então abortar no meio de um download de 400 MB é instantâneo. Quando o job está
  *pausado* não há thread para observar o evento: `JobManager.cancel` marca o
  estado na hora.
- **Conflitos**: `/api/jobs/{id}/conflicts` devolve os mods não convertidos com o
  diagnóstico. A página acumula as escolhas localmente e salva tudo de uma vez em
  `PUT /api/jobs/{id}/resolutions`; `POST /api/jobs/{id}/apply` roda o `finish`,
  que marca `MatchStrategy.MANUAL`, tira o jar de `overrides/mods` e gera o zip.
- Jobs web rodam com `keep_work=True`; `finish` reaproveita a pasta de trabalho
  (`_assemble(reuse=True)`), então aplicar mudanças de novo não rebaixa nada.
- **Layout**: coluna esquerda = listas (entradas + conversões salvas); coluna
  direita = conversão em andamento (progresso, log, botões) e, abaixo, detalhes do
  item selecionado. Enriquecimento das entradas vem de `/api/packs/{name}/modrinth`.
- **Contagens**: `conflicts` é o total (inclui resolvidos) e `unresolved` é o que
  ainda falta — a interface mostra `unresolved` em todo lugar. `plan()` recebe as
  resoluções ainda **não aplicadas**, senão mostraria downloads que não vão ocorrer.
- **`dirty`**: fica `True` quando escolhas são salvas e volta a `False` depois do
  `finish`. É o que decide se o botão "Aplicar mudanças" aparece num job concluído.
- **Log estilo Terraform**, em duas etapas: `Converter._result_line` emite uma linha
  por mod durante a busca (a partir da thread que coleta, para acompanhar a barra) e
  `Converter._log_analysis` fecha com o bloco agrupado — sucessos viram contagem
  (`++ N … não listados`), depois os sem versão (amarelo), os sem projeto (vermelho)
  e o resumo. `_segments()` traduz a marcação do `rich` em trechos coloridos (é o que
  deixa cada número do resumo na sua cor); `_plain()` dá a cor de base da linha:
  neutra se começa com `[bold]`, senão a primeira cor encontrada. Indentação é
  preservada e linha vazia é espaçador.
- **A versão vai na URL dos estáticos** (`render_index()` serve
  `/static/app.js?v=0.27.1`). Um front antigo contra um servidor novo é como um
  campo renomeado no payload vira `NaN` na tela. O `Cache-Control: no-cache` em
  `/` e `/static/` ajuda ("guarde, mas revalide" — o ETag responde 304), **mas
  não é retroativo**: o que já está no cache foi guardado sem cabeçalho nenhum.
  Quem fecha a porta é o `?v=`. E para o caso de a **própria página** ser a
  velha, a `<meta name="app-version">` é comparada com a versão do `/api/state`
  e a diferença vira um aviso vermelho pedindo `Ctrl`+`F5` (`conferirVersao`).
- **O comando `web` abre `.../?v={versão}`, e não a URL nua.** `webbrowser.open`
  numa URL já aberta **foca a aba** em vez de navegar; a página é uma aplicação
  de uma tela só, não recarrega sozinha, e o `app.js` velho continua vivo na
  memória. Reiniciar o servidor trocava o back-end debaixo do front antigo. A
  versão também aparece no cabeçalho da página e na linha que o terminal
  imprime: com as duas à vista, "estou na versão nova?" deixa de ser
  adivinhação.
- **Cabeçalhos HTTP** entram por um middleware **ASGI puro**
  (`SecurityHeadersMiddleware`). Não troque por `@app.middleware("http")`:
  `BaseHTTPMiddleware` reencaminha o corpo e quebra downloads de arquivo
  (`Too much data for declared Content-Length`). Pelo mesmo motivo `build_zip` é
  atômico (`.part` + `os.replace`) e nenhuma resposta 204 pode carregar corpo.
- **Nada de re-render cego**: o polling é de 600 ms, então toda escrita no DOM
  passa por `setHTML`/`setText`/`setClass` (só mexem se mudou) e o polling para
  quando o job fica parado (`awaiting_conflicts`, `done`, `cancelled`, `error`).
  Sem isso os botões piscam e a seleção de texto do usuário se perde.
- **Navegação em dois níveis**: primeiro a ferramenta (`.tool`: conversor ou
  atualizador), depois as abas dela (`.tab[data-tool]`). Cada ferramenta tem o seu
  painel de trabalho — `renderJob` (conversão) e `renderUpdateJob` (atualização)
  ignoram jobs do outro tipo, senão um aparece dentro do painel do outro.
- **Aviso verde só em `done`.** O `else` final do aviso pegava qualquer job com
  resultado e pintava de verde até o cancelado. Cada estado final tem o seu:
  `cancelled` → neutro, `error` → vermelho, `awaiting_*` → amarelo.
- **Fixture não pega campo que o servidor esqueceu.** Quando algo aparecer
  errado na tela e o `check_ui.js` estiver verde:

  ```powershell
  uv run python tools/capture_job.py "meu pack.mrpack"
  node tests/ui/render_real.js
  ```

  Sobe um servidor nas pastas de teste (`test_modpacks/`, nunca nas suas), roda
  uma análise de verdade, salva o payload e renderiza a tela inteira com ele. Foi o que separou "o código está
  errado" de "o navegador está com a versão velha" — e a resposta foi a
  segunda. Fora do `check_all.py` porque precisa de rede e da chave da API.
- **`node tests/ui/check_ui.js`** roda o `app.js` num DOM de mentira e afirma, para
  cada estado de job, qual aviso e quais botões aparecem — **e os dois painéis de
  confirmação**, que é onde um `NaN` chegou à tela do usuário porque só o do
  atualizador era exercitado. Os "avisos de renderização" do DOM falso
  (`undefined`, `NaN`, `[object Object]` no HTML) **contam como falha**; antes
  eram impressos e a linha final dizia "consistente" do mesmo jeito — além das três seções
  da revisão e das cores por origem. Rode junto com o `pytest`: os bugs de estado
  daqui (card verde ao cancelar, botão sobrando) só apareciam abrindo a página.
- **Aplicar salva o que está na tela.** Nas duas ferramentas, *Aplicar mudanças*
  grava antes as escolhas pendentes (`saveConflictResolutions` /
  `saveUpdateDecisions`). Antes o conversor recusava com um toast e o usuário
  ficava sem saída óbvia.
- **Depois de salvar, releia o job.** O polling **para** em `awaiting_conflicts`
  / `awaiting_review`; sem um `pollJob()` explícito o painel "o que vai
  acontecer" mostrava o plano de antes das escolhas.
- **Reescrever o container é sempre por `setHTML`.** Vale para `#conflict-groups`
  e `#ur-groups`: durante o `finishing` o poll volta a passar por eles, e o
  `innerHTML =` cru destruiria a busca aberta num card. Cuidado ao misturar os
  dois estilos no mesmo elemento — o cache do `setHTML` é um `WeakMap` e uma
  escrita crua o deixa mentindo.
- **`.mrpack` desatualizado.** `job.dirty` (ligado ao salvar decisões, desligado
  ao gerar) é o que faz a atualização já concluída oferecer *Aplicar mudanças* de
  novo em vez de deixar o arquivo em disco divergir das decisões.
- `goToTab` para uma aba de outra ferramenta abre **aquela** aba
  (`selectTool(tool, aba)`), não a primeira; trocar de ferramenta volta para a
  última aba que estava aberta nela (`state.lastTab`); e fechar um job tira o
  usuário da aba de revisão que acabou de ficar vazia (`leaveEmptyTab`).
- **Aplicar sempre passa pelo painel de confirmação**, nas duas ferramentas. No
  conversor o plano vem do servidor (`outcome.plan`); no atualizador é calculado
  no front (`updatePlan()`), porque lá ele precisa refletir também as decisões
  que ainda não foram salvas.
- **Só o que interrompe trabalho em andamento pede dois cliques**
  (`armarBotao`): cancelar, encerrar, apagar a chave da API. O rótulo vira
  "Cancelar mesmo?" por 4 s e volta sozinho — nada de diálogo modal. **Apagar
  um item de lista não pede**: o ✕ só aparece no card sob o cursor, leva um
  arquivo escolhido, e o clique a mais só atrapalhava.
- **A revisão esvazia quando o pack é gerado** (`updateFiles` devolve `[]` se
  `update.packaged`), igual aos conflitos do conversor: não há mais decisão
  pendente. Para mudar de ideia, feche e analise de novo.
- **Enquanto o pack é gerado (`finishing`), a revisão fica só de leitura**
  (`travarRevisao` → `.locked`: apagada e sem cliques). O backend já recebeu as
  decisões que valem, e o que fosse clicado ali se perderia em silêncio. Sem
  texto de aviso: o cinza já diz, e um aviso que sobra depois é pior que nenhum.
- **O popup de confirmação cobre o aviso e os botões** (`.job-head` relativo +
  `.confirm` absoluto), acima do log. Ele não pode empurrar o log: a tela
  inteira dançava na hora de confirmar.
- **`packEmCurso` inclui os estados de espera** (`awaiting_conflicts`/
  `awaiting_review`): o trabalho não terminou, só está esperando você — o verde
  sai quando o arquivo fica pronto, não quando a análise acaba.
- **A lista é redesenhada quando o job muda de estado** (no `pollJob`/
  `pollUpdateJob`). Sem isso o destaque só aparecia no clique seguinte.
- **Selecionar um pack — ou começar outro trabalho — fecha um job terminal que
  não produziu nada** (`fecharJobMorto`/`fecharUpdateMorto`). Um job `cancelled`/`error` aberto ficava dizendo
  "cancelada" enquanto o usuário já tinha seguido adiante. `done` **não** é
  descartado: tem `.zip` para baixar.
- **O painel de confirmação mostra três contagens e um tamanho**: o que vai
  para o manifest, o que sai do `overrides/` do mrpack, o que vai ser baixado —
  e o `zip_mb`, o arquivo final (`≈`, em ciano). Saíram, por não responderem à
  pergunta que se faz com o dedo sobre o *Continuar*: versão do Minecraft,
  loader, nome do `.zip`, barra de proporção, a contagem de arquivos copiados do
  `overrides/` e — na v0.27.1 — os outros três tamanhos (manifest, desconto,
  download), que diziam *de onde vêm os bytes*. **`plan()` devolve exatamente o
  que o card mostra**; campo que ninguém lê é peso morto.
- **O peso do `overrides/` é o comprimido** (`Modpack.override_bytes`, somado de
  `ZipInfo.compress_size` no parser; o mesmo vale para o `file_size` dos
  `override_candidates`). No pack de teste são 6,7 MB dentro do zip contra 33,7
  MB crus — usar o tamanho aberto erraria a estimativa em 27 MB, porque
  `config/` é texto. Para `.jar`/`.zip` os dois coincidem.
- **`test_modpacks/` é do ferramental, não da interface.** `Config.TEST_INPUT_DIR`
  / `TEST_OUTPUT_DIR` existem para o `tools/capture_job.py` e para quem está
  desenvolvendo rodar sem sujar `input_modpacks/`. A tela não sabe que elas
  existem, e as configurações também não — é isso que as torna seguras.
- **Cada ferramenta tem a sua seleção.** `state.selection` é do conversor
  (entrada/registro) e `state.selectedUpdate` é do atualizador — dividir a mesma
  variável fazia escolher um pack de um lado desmarcar o do outro.
- **Ordem da lista de entrada** (`ordenarPacks`): o que está rodando agora
  (`.working` + spinner), depois o pack do trabalho aberto (`.in-job`), depois
  por `last_used` — quando aquele pack foi convertido/atualizado pela última vez
  (o `/api/state` calcula isso juntando `conversions/*.json` e `*-update.json`).
  Você continua podendo clicar em outro para ver detalhes; ao terminar, a coluna
  troca para o lado da saída (`mostrarLado`) já com o resultado selecionado.
- **Encerrar o servidor** (`POST /api/shutdown`) só existe quando o processo
  subiu pelo comando `web` — é lá que `app.state.server` é guardado. Rodando o
  uvicorn direto, o endpoint devolve 501 e o botão nem aparece (`can_quit` no
  `/api/state`). Antes de sair, os trabalhos em andamento são cancelados: as
  threads são `daemon` e morrer no meio de um download deixaria `.part` para
  trás.
- **Versão do Minecraft e loader usam as mesmas tags em toda parte**
  (`mcTag`/`loaderTag`/`mcTags`): cada loader com a sua cor e um gradiente de
  matiz por versão (`mcHue`: 1.21 azul → 1.7 vermelho). O objetivo é reconhecer
  batendo o olho, sem ler.
- **Uma tela, sem rolar a página.** `body` é flex-column, `main` é quem rola, e
  as abas principais (`.panel.split`) têm `height: 100%`: as três colunas cabem
  na tela e **cada uma rola por dentro** (`.col-card` + `.scroller`). O `.log`
  cresce com o que sobra (`.job-body` é flex). Nada de `calc(100vh - 150px)`:
  altura fixa chutada quebra quando o cabeçalho muda.
  Ao acrescentar coisa numa dessas colunas, pergunte **o que vai encolher** —
  se nada puder, ela precisa entrar num `.scroller`.
- **Entrada × saída dividem a coluna 1** (`.switch` + `.side`), em vez de
  empilhar dois cards: empilhado, a lista de baixo ficava sempre fora da tela.
- **Layout em três colunas iguais** (listas · trabalho · detalhes) com cabeçalho
  fixo; a aba de conflitos também é uma grade de três colunas.
  A aba de conflitos tem três seções (sem equivalente / versão indisponível /
  resolvidos); abrir um card já dispara a busca ou a listagem de versões, e as
  versões compatíveis com o Minecraft do pack vêm primeiro.
- **Conflitos somem depois de empacotar** (`outcome.packaged` → `conflicts() == []`):
  não há mais decisão pendente. O histórico fica no registro.
- A busca da UI usa `rank_projects` (o mesmo ranking do matcher) porque a ordenação
  da API do CurseForge é ruim.
- **A lista de entrada mostra Minecraft, loader e nº de mods** (`_pack_meta`), que é
  o que decide se o pack serve para o que você quer. O `/api/state` é consultado a
  cada 600 ms, então o resultado é memoizado por `(caminho, mtime, tamanho)`: o zip
  só é reaberto quando o arquivo muda. Pack ilegível vira campos `None`, nunca um
  erro que derruba a lista.
- **Configurações = editor do `.env`** (`settings.py`, `GET/PUT /api/settings`).
  Três invariantes: o que o usuário escreveu à mão **não se perde** (comentários,
  ordem e chaves desconhecidas são preservados); a chave da API **nunca sai
  inteira** do servidor (`mascarar` devolve só os 4 últimos caracteres); e valor
  vazio **comenta** a linha em vez de apagá-la. Campos numéricos declaram
  `minimo`/`maximo`, e é isso que deixa o front usar slider — sem intervalo, o
  campo vira caixa de texto.
  Pastas e cache são resolvidos na importação: o `PUT` devolve `restart_needed`
  para a interface avisar em vez de fingir que já valeu.
- **Configurações travam com trabalho aberto** (`settings_livre()` → 409, e
  `locked_by` no `GET` para a interface desabilitar antes). Metade delas
  (workers, timeout, páginas) é lida enquanto o trabalho roda: trocar no meio
  daria um resultado que não é nem o antigo nem o novo.
- **Restaurar padrão e apagar a chave são rotas diferentes** (`/reset` e
  `/forget-key`). Juntar as duas num parâmetro fazia "apagar a chave" limpar
  todas as configurações junto.
- **Onde obter o valor mora no campo, não na tela.** `Campo.link` vira a etiqueta
  "↗" ao lado do título, e só enquanto o campo estiver vazio: depois
  de configurado o atalho é ruído.
- **Handler que redesenha precisa de `stopPropagation`.** O botão do olho
  re-renderiza os campos; sem parar o borbulho, o elemento clicado sai do DOM,
  o `closest(".settings-wrap")` do listener global dá `null` e o painel se
  fecha achando que o clique foi fora.
- **Nada apaga uma pasta inteira.** O botão *Limpar* por pasta existiu até a
  v0.26 e saiu com a rota e o módulo `storage.py` juntos: dois cliques não
  compensam um botão fixo cujo pior caso é a tarde inteira. Quem apaga é o **✕
  de cada card** (`botaoLixeira`/`ligarLixeiras`), o mesmo nas três listas —
  entrada, conversões salvas e packs atualizados. Apagar um registro leva o
  `.zip` junto: sem o registro ele não aparecia em lista nenhuma e só ocupava
  disco. `stopPropagation` no ✕ — senão o mesmo clique seleciona o card.
- **Limpar cache** (`DELETE /api/cache`, botão no topo) usa o mesmo
  `services.cache.clear_cache()` do `clear-cache` do CLI, e fecha antes o cliente
  compartilhado do CurseForge. **Todo `ModrinthClient` tem de ser aberto junto com
  o seu `SimpleCache` num `with`**: cada conexão SQLite deixada aberta trava o
  arquivo no Windows, e era por isso que o botão só respondia "em uso" (no servidor
  use o helper `modrinth_client()`).

## Filosofia

> Falso positivo é pior que falso negativo.

Um mod em `overrides` funciona; um mod errado no manifest quebra o pack. Na dúvida,
o matcher desiste.

---

## Próximos passos possíveis

- Aplicar filtro de loader (`modLoaderType`) como desempate na busca.
- Match por fingerprint murmur2 (exige baixar os jars — ver `DECISIONS.md` §5).
- Suporte a `datapacks/` como projetos do CurseForge (hoje vão para `overrides`).
  O `classId` existe (6945); falta um pack de teste para conferir onde o launcher
  instala o arquivo — datapack no lugar errado não avisa, só não carrega.
- Modo `--dry-run` (relatório sem baixar nada nem gerar zip).
- Publicar no PyPI / CI.

---

## Ideias já rejeitadas (não reintroduzir)

- **Aceitar o projeto só pelo nome/score** — gerava falso positivo; o score serve
  apenas para ordenar candidatos.
- **Pegar `latestFiles[0]`** — instalava versão errada (era o que o atalho de
  Fabric API fazia).
- **Lista manual de aliases** — desnecessária: o nome real vem da API do Modrinth.
- **Baixar todos os jars para calcular fingerprint** — custo alto demais para o ganho.
- **Deixar resourcepack e shader sempre em `overrides/`** — funcionava, mas o
  export do CurseForge os põe no manifest, e eram 9 dos 11 buracos do v0.23.
