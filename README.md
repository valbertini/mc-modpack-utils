# mrpack2curseforge

Duas ferramentas sobre modpacks do **Modrinth** (`.mrpack`), na mesma interface:

| | O que faz |
|---|---|
| **Converter** | transforma o pack no formato do **CurseForge** (`manifest.json` + `overrides/`), procurando os projetos equivalentes lá |
| **Atualizar mods** | troca cada mod pela versão mais recente **para a versão do Minecraft que você escolher** e devolve um `.mrpack` novo |

As duas se completam: dá para atualizar um pack antigo e, com um clique, mandar o
resultado para a conversão.

---

## Uso — interface web (recomendado)

```powershell
uv run mrpack2curseforge web
```

Abre <http://127.0.0.1:8000> no navegador.

A tela tem três colunas que cabem **numa tela só** — nada de rolar a página:
**entradas** (ou as saídas salvas, no seletor do topo) · **o trabalho**
(progresso, log ao vivo e botões) · **detalhes** do que estiver selecionado.
Cada coluna rola por dentro quando precisa.

### O passo a passo

1. **Escolha um modpack** (arraste um `.mrpack` ou clique em um da lista). A
   direita mostra o que tem dentro: versão, loader, contagem de arquivos e a lista
   de mods com os **nomes reais consultados no Modrinth**.
2. **Iniciar conversão.** Só existe **uma conversão aberta por vez**; dá para
   **cancelar** a qualquer momento (inclusive no meio de um download).
3. O conversor procura cada mod no CurseForge. Se sobrar algum conflito, ele
   **pausa antes de baixar qualquer coisa** e avisa — os mods não encontrados
   seriam justamente os que vão para `overrides/mods`, então vale resolver antes.
4. Na aba **Conflitos** (dividida em *sem equivalente* · *versão indisponível* ·
   *resolvidos*), resolva o que quiser. Cada card abre mostrando o mod original
   (ícone, nome e link do Modrinth) para comparar com os candidatos, e já traz as
   opções prontas:
   - *versão indisponível* → lista as versões daquele projeto no CurseForge, com
     as **compatíveis com o Minecraft do pack em primeiro** e marcadas;
   - *sem equivalente* → já vem com a busca feita pelo nome do mod; dá para
     refazer a busca com outro termo;
   - escolher move o card para **resolvidos** (e *desfazer* devolve ao lugar);
   - as escolhas ficam pendentes até você clicar em **Salvar mudanças**.
5. Volte em **Converter** e clique em **Aplicar mudanças**: aparece um resumo do
   que vai acontecer (quantos mods no manifest, quantos jars serão baixados, etc).
   Confirme em **Continuar** — seguir sem resolver nada também funciona.
6. No fim aparecem **⬇ Baixar modpack**, o registro em JSON e **Fechar conversão** —
   só depois de fechar dá para começar outra. O painel de detalhes passa a mostrar
   o resumo completo da conversão (o mesmo das conversões salvas).

Durante a busca o log mostra uma linha por mod (`++` achado, `--` amarelo sem a
versão, `--` vermelho sem projeto) e, ao terminar, um resumo agrupado no estilo do
`terraform plan`.

### Conversões salvas (e por que o `.zip` some)

Fechar a conversão **apaga o `.zip`** e mantém só o **registro** em
`output_modpacks/conversions/<nome>.json`: um arquivo pequeno com o que foi decidido
para cada mod, incluindo suas escolhas manuais.

Isso porque o `.zip` é 100% regenerável — com o `.mrpack` de origem e os
`projectID/fileID` já gravados, o modpack é remontado **sem consultar o CurseForge**
(no pack de teste, 8 segundos). Guardar 500 MB de saída para sempre não vale a pena.

Na lista **Conversões salvas** cada registro traz o resumo, todas as decisões e um
botão **Gerar modpack**. Se o `.mrpack` de origem tiver saído de `input_modpacks/`,
a interface avisa que não dá para remontar.

> O CLI continua deixando o `.zip` em `output_modpacks/` — lá o objetivo é
> justamente produzir o arquivo.

### Aba **Atualizar mods**

Escolha o modpack e a **versão do Minecraft de destino** e clique em *Analisar
atualização*. A análise **não grava nada**: ela monta uma diff para você revisar.

A aba **Revisar** é igualzinha à de conflitos do conversor: **três seções lado a
lado**, e o card anda entre elas conforme você decide.

| seção | o que tem lá | o que dá para fazer |
| --- | --- | --- |
| **Sem versão** | o Modrinth não tem nada para o alvo | escolher um arquivo à mão (inclusive **de outro projeto**) |
| **Resolvidos por você** | o que você escolheu | conferir e desfazer |
| **Com versão** | achou versão para o alvo | trocar a versão ou o projeto, ou manter a versão atual |

Escolher um `.jar` — em qualquer das pontas — manda o card para o meio, e a cor
diz o que foi feito: **roxo** para quem tinha versão e você trocou (fica no topo,
é o que mais merece conferência), **verde** para quem não tinha e você escolheu
uma, e **verde escuro** no fim para os que entraram em massa pelo botão dos
não-mods. Na lista de versões, a
que já está no seu pack vem marcada, para comparar sem sair da tela.

**Nada sem versão entra sozinho.** Entrar no pack é a decisão que quebra o jogo se
estiver errada, então ela é sempre sua: o que ficar na seção *Sem versão* não vai
para o pack novo. Para resourcepacks, shaders e datapacks — que costumam funcionar
além da versão em que foram publicados, como o *Simply 3D* — há um botão no topo da
seção que traz **todos os que não são mods** de uma vez (e outro, na seção do
meio, para tirá-los de volta).

*Salvar mudanças* guarda as decisões (nada é gerado ainda); *Aplicar mudanças*,
na aba **Atualizar**, escreve o pack. É o mesmo par do conversor.

### Trocar de modloader

Além da versão do Minecraft dá para escolher **outro modloader** — de `fabric`
para `neoforge`, por exemplo. Na prática é um filtro a mais nas consultas: cada
mod passa a ser procurado para o loader novo, e quem não tiver versão lá cai na
seção *Sem versão* para você decidir. O `modrinth.index.json` sai com a
dependência certa (`neoforge` no lugar de `fabric-loader`).

A **versão do loader** é um dropdown carregado do serviço do próprio loader
(fabric/quilt meta, maven do neoforge/forge) e filtrado pela versão do Minecraft
escolhida: a opção padrão é *mais recente*, e a do seu pack aparece como
alternativa quando ainda serve. Trocar de Minecraft recarrega a lista e avisa se a
versão que estava escolhida não serve mais.

Como a versão do fabric que está no pack não significa nada para o neoforge,
**trocar de loader usa a versão do dropdown** — não a do pack.

Só então *Aplicar mudanças* escreve o pack, com:

- cada mod na versão mais recente publicada para aquele Minecraft (prefere
  `release`; só cai para `beta`/`alpha` se não houver release);
- `overrides/`, `env` (client/server) e mods `.disabled` preservados;
- o resumo dizendo quantos trocaram de versão, quantos você escolheu à mão e
  quantos ficaram de fora — nada some em silêncio.

Nada é baixado: o índice do `.mrpack` já traz URL, tamanho e hashes, então a
atualização é só consulta (o pack de teste, com 59 arquivos, leva ~6s). Se a
versão escolhida for **anterior** à do pack, a interface avisa — aí os mods vão
para versões mais antigas, que é o que existe para aquele Minecraft.

No fim dá para **baixar o `.mrpack`** ou mandá-lo para `input_modpacks/` com
*Adicionar ao input* — é assim que um pack atualizado vira entrada da conversão.

Os packs já gerados ficam em **Packs atualizados**: clicando, o painel mostra as
decisões daquela atualização e os botões de baixar, adicionar ao input e excluir.

> A interface tem dois níveis: em cima você escolhe a **ferramenta** (Conversor ou
> Atualizador) e logo abaixo aparecem só as abas dela. As duas rodam ao mesmo
> tempo — uma conversão em andamento não impede uma atualização.

No canto superior direito ficam **Limpar cache**, **Encerrar** (fecha o servidor;
pede dois cliques e cancela o que estiver rodando) e a **engrenagem**.

### Configurações

A engrenagem abre um editor do seu `.env`: um campo por configuração, slider onde
existe um intervalo com sentido e caixa de texto para caminhos. A chave da API
aparece mascarada (`••••••••9999`) com um botão para revelar o que você digitou.

Enquanto não houver chave salva, uma etiqueta **↗** ao lado do título abre a
página do console do CurseForge onde a chave é gerada; assim que ela está
configurada a etiqueta some.

*Restaurar padrão* limpa tudo **menos a chave**; para apagar a chave há um botão
logo abaixo dela, que não mexe no resto. O painel não fecha enquanto houver
alteração pendente — salve ou descarte primeiro.

Com uma conversão ou atualização aberta os campos ficam desabilitados: parte das
configurações é lida enquanto o trabalho roda, e trocá-las no meio daria um
resultado que não corresponde a nenhum dos dois valores. Feche o trabalho antes.

O **limiar de similaridade do diagnóstico** (`M2CF_VERSION_THRESHOLD`) fica
de fora da tela de propósito — é calibrado com dados medidos (§4b) e continua
ajustável editando o `.env`.

O arquivo continua sendo a fonte da verdade: seus comentários e suas próprias
variáveis são preservados, e trocar de pasta avisa que só vale depois de
reiniciar.

Tudo é hospedado na sua máquina: HTML, CSS e JS saem do próprio servidor (nada de
CDN, nada de build), e o servidor escuta só em `127.0.0.1` — use `--host` para
mudar. As conexões externas são as APIs do Modrinth e do CurseForge usadas pela
conversão, mais os ícones dos mods exibidos na busca (sem internet eles viram um
placeholder e o resto continua funcionando).

---

## Uso — linha de comando

1. Coloque um ou mais arquivos `.mrpack` na pasta **`input_modpacks/`**.
2. Rode, na raiz do projeto:

```powershell
uv run mrpack2curseforge
```

3. Acompanhe o progresso no terminal. Ao final, o modpack convertido aparece em
   **`output_modpacks/`**:

```
output_modpacks/
├── Meu-Pack-1.0.0-[convertido].zip               <- importe no CurseForge App
├── Meu-Pack-1.0.0-mc1.21.11-[atualizado].mrpack  <- saída do atualizador
└── conversions/
    └── Meu-Pack-1.0.0-[convertido].json          <- registro: o que foi decidido
                                                     para cada mod (e permite
                                                     regerar o .zip depois)
```

O sufixo diz de onde o arquivo saiu: **`[convertido]`** veio do conversor,
**`[atualizado]`** veio do atualizador. Conversões antigas mantêm o nome que
tinham — regerar não renomeia nada.

O arquivo de entrada não é movido nem apagado — pode reconverter à vontade.

### Importando no CurseForge

CurseForge App → **Create Custom Profile** → **Import** → selecione o `.zip`.

---

## Instalação

Requer [uv](https://docs.astral.sh/uv/) e Python 3.12+.

```powershell
uv sync
```

Crie um arquivo `.env` na raiz (veja `.env.example`) com sua chave da API do
CurseForge (grátis em <https://console.curseforge.com/>):

```
CURSEFORGE_API_KEY=sua_chave_aqui
```

---

## O que acontece durante a conversão

```text
input_modpacks/pack.mrpack
        │
        ▼
  leitura do modrinth.index.json          (mods, resourcepacks, hashes, URLs)
        │
        ▼
  API do Modrinth (em lote)               SHA1 -> projeto -> slug + título real
        │
        ▼
  API do CurseForge                       busca por slug, título, regex do arquivo
        │
        ▼
  confirmação POR NOME DE ARQUIVO         só vale se o CurseForge tem o MESMO .jar
        │
        ├── achou  ──► manifest.json (projectID + fileID)
        │
        └── não achou ──► diagnóstico (versão indisponível? ou mod não existe lá?)
                     └──► download do jar original ──► overrides/mods/
        │
        ▼
output_modpacks/pack-[convertido].zip
```

Tudo que não é mod (resourcepacks, shaderpacks, datapacks) e todo o conteúdo de
`overrides/` do pack original são copiados para o `overrides/` do zip final.

Detalhes e justificativas de cada escolha estão em [`DECISIONS.md`](DECISIONS.md).

---

## Estratégia de matching

Para cada mod, nesta ordem, parando na primeira que confirmar:

| Ordem | Estratégia | Como busca |
|-------|-----------|------------|
| 1 | `modrinth-slug` | lookups exatos por slug: o do Modrinth, o do título (`Essential Mod` → `essential-mod`) e os das variações de grafia |
| 2 | `modrinth-title` | busca textual pelo título real do projeto |
| 3 | `modrinth-variant` | outras grafias: `Extended AE` → `ExtendedAE`, `VitalityFix` → `Vitality Fix`, `extended_ae` |
| 4 | `modrinth-loader` | nome + loader do pack (`Things` → `Things fabric`), que desempata buscas genéricas |
| 5 | `filename-regex` | consulta derivada do nome do `.jar` (`ImmediatelyFast-Fabric-1.16.1.jar` → `immediately fast`) |
| 6 | `filename-simple` | primeiro token relevante (último recurso) |

**Um candidato só é aceito se existir nele um arquivo com exatamente o mesmo nome
do `.jar` original** (ignorando maiúsculas/minúsculas e `.disabled`). Versão faz
parte da comparação. Na dúvida, o mod vai para `overrides` — instalar o mod errado
é pior do que não instalar pelo CurseForge.

### Diagnóstico: "não existe" ou "versão indisponível"?

Quando nenhuma estratégia encontra o arquivo exato, o conversor volta ao Modrinth,
pega os **10 arquivos mais recentes** daquele projeto e compara, um a um, com os
**10 arquivos mais recentes** de cada candidato do CurseForge:

- similaridade **≥ 0.85** → o projeto está no CurseForge, só **aquela versão** não
  está publicada lá → status `version-unavailable`;
- abaixo disso → o mod realmente **não existe** no CurseForge → `not-on-curseforge`.

Nos dois casos o mod vai para `overrides/mods` (o pack continua idêntico), mas o
terminal e o relatório dizem qual é o caso, o link do projeto no CurseForge e qual
é a versão mais próxima disponível:

```
litematica-fabric-26.2-0.28.2.jar   versão indisponível   Litematica   100%
   mais próxima no CurseForge: litematica-fabric-1.21.10-0.24.8.jar
```

A similaridade combina o nome completo com o nome sem versão/loader — é isso que
separa `litematica-...-0.28.2` × `litematica-...-0.28.3` (0.98 → mesma família) de
`sodium-...` × `sodium-extra-...` (0.76 → mods diferentes). O limiar é ajustável
via `M2CF_VERSION_THRESHOLD`.

---

## Comandos

```powershell
uv run mrpack2curseforge web                  # interface gráfica local
uv run mrpack2curseforge web --port 9000      # outra porta
uv run mrpack2curseforge                      # converte tudo de input_modpacks/
uv run mrpack2curseforge update pack.mrpack -m 1.21.8   # atualiza os mods
uv run mrpack2curseforge versions             # versões do Minecraft aceitas
uv run mrpack2curseforge --workers 10         # mais paralelismo
uv run mrpack2curseforge --no-cache           # ignora o cache local
uv run mrpack2curseforge -i pasta -o saida    # pastas alternativas
uv run mrpack2curseforge convert pack.mrpack  # converte um arquivo específico
uv run mrpack2curseforge inspect pack.mrpack  # só mostra o conteúdo (offline)
uv run mrpack2curseforge clear-cache          # limpa o cache das APIs
```

Trocar de modloader pelo terminal (a versão do loader é obrigatória):

```powershell
uv run mrpack2curseforge update pack.mrpack -m 1.21.11 `
  --loader neoforge --loader-version 21.11.5
```

O cache também tem um **botão no canto superior direito** da interface, com o
tamanho atual ao lado. Se algum arquivo estiver em uso, ele avisa e aí vale o
`clear-cache` do terminal com a interface fechada.

Variáveis de ambiente opcionais (todas com default sensato) estão em `.env.example`.

---

## Resultado típico

Pack de teste com 49 mods (Fabric):

| Métrica | Valor |
|---------|-------|
| Encontrados no CurseForge | 45 |
| Enviados para overrides | 4 |
| · versão indisponível | 4 |
| · projeto não existe lá | 0 |
| Taxa de conversão | 91.8% |
| Duração (1ª execução) | ~17s |
| Duração (com cache) | ~8s |

Modpack grande (Prominence II, 406 mods): **392 no manifest / 14 conflitos**,
conversão completa (com downloads e `.zip` de 506 MB) em ~42s com cache quente.

Os 4 restantes (Litematica, MiniHUD, Syncmatica, Axiom) **existem** no CurseForge,
mas nenhum publicou lá exatamente a versão usada no pack — por isso vão para
`overrides` com o arquivo original do Modrinth.

---

## Desenvolvimento

```powershell
uv sync --group dev
uv run pytest
```

Os testes não usam rede (o cliente do CurseForge é simulado).

Estrutura:

```
src/mrpack2curseforge/
├── cli.py            interface de terminal (typer + rich)
├── converter.py      orquestração: parse -> match -> download -> zip
├── updater.py        atualização dos mods para outra versão do Minecraft
├── progress.py       abstração de progresso (terminal ou web)
├── records.py        registros persistentes + regeneração do .zip
├── web/              interface web local (FastAPI + HTML/CSS/JS sem build)
│   ├── server.py     rotas da API
│   ├── jobs.py       conversões em background + estado dos conflitos
│   └── static/       index.html, style.css, app.js
├── domain.py         modelos internos (PackFile, Modpack, MatchResult)
├── reporting.py      relatório JSON + resumo no terminal
├── parsers/mrpack.py leitura do .mrpack
├── services/
│   ├── modrinth.py   descobre o nome real de cada mod
│   ├── curseforge.py cliente da API (busca, arquivos, cache, retries)
│   ├── matcher.py    ⭐ toda a inteligência de matching
│   ├── downloader.py downloads com verificação de SHA1
│   └── cache.py      cache persistente (SQLite)
└── builders/
    ├── curseforge_manifest.py  manifest.json + modlist.html
    └── package.py              geração do .zip
```

---

## Licença

Ainda não definida.
