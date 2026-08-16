# Decisões de projeto

> **Nota de nome:** o pacote se chama `mrpack2curseforge`, mas o projeto agora tem
> duas ferramentas (converter e atualizar). Renomear mexeria no entrypoint, nos
> imports e nos comandos que você já usa — fica registrado como pendência, não como
> esquecimento.

Registro das decisões tomadas durante a reescrita do conversor (v0.2), incluindo
as que foram tomadas sem consultar ninguém. Cada item traz o *porquê*.

---

## 1. Fluxo de uso

**Decisão:** o uso normal é "jogue o arquivo na pasta e rode um comando".

```
input_modpacks/*.mrpack  ->  uv run mrpack2curseforge  ->  output_modpacks/*.zip
```

- O comando sem argumentos converte **todos** os `.mrpack` de `input_modpacks/`.
- Os arquivos de entrada **não são movidos, renomeados nem apagados** — reconverter
  é sempre seguro e idempotente.
- Cada conversão gera dois arquivos na saída:
  - `<Nome>-<versão>-curseforge.zip` (o modpack pronto para importar);
  - `<Nome>-<versão>-curseforge-report.json` (relatório completo, mod a mod).

**Por quê:** o pedido era não precisar responder perguntas nem passar parâmetros.
Nada na execução padrão é interativo; qualquer ambiguidade é resolvida por default.

---

## 2. Como o nome do mod é descoberto (Modrinth)

**Decisão:** o nome do projeto vem da **API do Modrinth**, não do nome do arquivo.

Ordem de resolução, em lote (rápido e barato):

1. `POST /v2/version_files` com os **SHA1** de todos os `.jar` do pack →
   devolve a versão e o `project_id` de cada arquivo;
2. se o hash não resolver, o `project_id` é extraído da URL do CDN
   (`cdn.modrinth.com/data/<PROJECT_ID>/...`);
3. `GET /v2/projects?ids=[...]` → `slug` e `title` de todos os projetos de uma vez.

**Por quê:** duas chamadas em lote resolvem o pack inteiro (49 mods = 2 requisições),
e o `slug`/`title` reais são consultas de busca muito melhores do que qualquer
heurística sobre o nome do arquivo.

---

## 3. Ordem das tentativas de busca no CurseForge

**Decisão:** quatro estratégias, nessa ordem, parando na primeira que confirmar:

| # | Estratégia | Consulta |
|---|-----------|----------|
| 1 | `modrinth-slug` | lookup exato `GET /mods/search?slug=<slug do Modrinth>` |
| 2 | `modrinth-title` | busca textual pelo título do Modrinth |
| 3 | `filename-regex` | busca textual pela consulta derivada do nome do `.jar` |
| 4 | `filename-simple` | busca pelo primeiro token relevante (último recurso) |

Se nenhuma confirmar, o mod é considerado **não convertível** e vai para
`overrides/mods`.

**Por quê:** é exatamente o fluxo pedido (nome via Modrinth → busca no CurseForge →
fallback por regex do arquivo). O `slug` foi acrescentado antes do título porque
Modrinth e CurseForge usam o mesmo slug na maioria esmagadora dos casos e o lookup
é exato — é a consulta mais barata e mais precisa que existe.

---

## 4. O que conta como "match" (regra mais importante)

**Decisão:** um projeto do CurseForge só é aceito quando ele oferece um arquivo com
**exatamente o mesmo nome** do `.jar` usado no modpack original.

A comparação é feita sobre um nome canônico:

- minúsculas;
- sem extensão (`.jar` / `.zip`) e sem sufixo `.disabled`;
- escapes de URL decodificados (`%2B` → `+`, `%20` → espaço);
- `_` e espaços múltiplos normalizados.

A **versão continua fazendo parte da comparação**: `sodium-0.9.0.jar` nunca casa
com `sodium-0.8.0.jar`.

**Por quê:** é a regra que o projeto sempre teve como filosofia ("falso positivo é
pior que falso negativo") e é o que foi pedido. Um mod na pasta `overrides` funciona;
um mod errado instalado quebra o pack.

---

## 4b. Diagnóstico: "versão indisponível" ≠ "mod não existe" (v0.3)

**Decisão:** quando nenhuma estratégia acha o arquivo exato, roda um passo extra de
**diagnóstico** antes de desistir:

1. volta ao Modrinth e pega os nomes dos **10 arquivos mais recentes** do projeto
   (`GET /project/{id}/version`), somados ao nome do arquivo local;
2. pega os **10 arquivos mais recentes** de cada um dos 5 melhores candidatos do
   CurseForge (`latestFiles` + 1ª página de `/files`);
3. compara todos contra todos por similaridade direta de nome;
4. `>= 0.85` → **`version-unavailable`** (o mod está no CurseForge, falta a versão);
   abaixo → **`not-on-curseforge`**.

**Fórmula da similaridade:** média entre (a) similaridade dos nomes completos e
(b) similaridade dos nomes sem versão/loader — ambas simetrizadas, porque
`SequenceMatcher` **não é simétrico** (chega a variar 0.10 conforme a ordem dos
argumentos, o que tornaria a classificação instável).

**Por que a metade (b) existe:** só o nome completo confundiria
`sodium-0.9.0` com `sodium-extra-0.9.1`. Com a comparação do "tronco" do nome, a
distinção fica limpa.

**Limiar 0.85 — calibrado com dados reais:**

| Caso | Similaridade |
|------|--------------|
| `litematica-...-0.28.2` × `litematica-...-0.28.3` | 0.98 |
| `fabric-api-0.154.0+26.2` × `fabric-api-0.115.0+1.21.4` | 0.92 |
| `syncmatica-fabric-26.2-0.3.18` × `syncmatica-1.21.4-0.3.15` | 0.86 |
| `c2me-fabric-mc26.2-...` × `c2me-fabric-mc1.21.4-...` | 0.85 |
| **limiar** | **0.85** |
| `sodium-...` × `sodium-extra-...` | 0.76 |
| `litematica-...` × `litematica-printer-...` | 0.70 |
| `xaeros_minimap` × `xaerosworldmap` | 0.63 |
| `appleskin` × `carpet` | 0.41 |

Ajustável por `M2CF_VERSION_THRESHOLD` (e `M2CF_RECENT_FILES`,
`M2CF_DIAGNOSIS_CANDIDATES`).

**Decisão importante — o diagnóstico NÃO muda o manifest.** Mesmo sabendo que o
projeto existe no CurseForge, o mod continua indo para `overrides` com o `.jar`
original do Modrinth. Instalar a versão 0.28.3 quando o pack pede 0.28.2 é
exatamente o falso positivo que o projeto evita (§4). O diagnóstico é informação
para *você* decidir, não licença para o conversor trocar versão.

O relatório passa a trazer, por mod: `status`, `similarity`, `project_name`,
`curseforge_url`, `closest_file_name` (a versão mais próxima disponível lá) e
`matched_reference` (qual arquivo do Modrinth produziu a evidência).

**Efeito prático no pack de teste:** os 4 mods que o README antes descrevia como
"não existem no CurseForge" são, na verdade, **todos** casos de versão indisponível
— inclusive com evidência conclusiva (para MiniHUD e Litematica um arquivo recente
do Modrinth é **byte a byte o mesmo nome** de um arquivo publicado no CurseForge,
similaridade 1.00).

---

## 4c. Interface web local (v0.4)

**Decisão:** FastAPI + HTML/CSS/JS escritos à mão, servidos de `127.0.0.1`.

**Por que não Streamlit:** o fluxo de resolver conflitos é interativo e cheio de
estado (buscar projeto → listar versões → escolher → desfazer, mod a mod). O
modelo de *rerun* do Streamlit reexecuta o script inteiro a cada clique, o que
tornaria isso lento e frágil. Com FastAPI o backend é só uma API e a página
controla o estado; também dá para mostrar progresso real de uma conversão que
roda em background.

**O que "rodar local" significa aqui** (esclarecido pelo usuário: a exigência é
*hospedagem* local, bibliotecas são permitidas):

- **a aplicação inteira é servida pela sua máquina** — HTML, CSS e JS saem de
  `static/`, nunca de um CDN. A página abre e funciona sem internet;
- não há build, bundler nem framework de front: três arquivos escritos à mão dão
  conta de três abas com tabelas e listas. Se um dia precisar de uma lib, o
  caminho é **baixá-la para `static/`** (vendorizar), não apontar para um CDN —
  senão a página passa a depender de rede para abrir;
- CSP `default-src 'self'` + `script-src 'self'`: um `<script src="https://...">`
  adicionado sem querer é bloqueado pelo navegador;
- **`img-src` permite `https:`**: os ícones dos projetos do CurseForge são
  carregados na busca e na tela de versões. É o que deixa óbvio qual é o "Sodium"
  certo entre "Sodium", "Sodium Mod" e "Sodium Extra" (junto com o nome do autor).
  Sem internet, as imagens degradam para um placeholder com a inicial;
- servidor escuta em `127.0.0.1` por padrão; `--host` é escolha explícita do usuário;
- uvicorn com `log_level=warning` e sem telemetria.

**Decisões de comportamento:**

- **Jobs vivem em memória.** Reiniciou o servidor, perdeu o job (e a possibilidade
  de reempacotar aquele pack sem reconverter). Persistir estado exigiria um banco;
  como reconverter com cache leva ~8s, não compensa.
- **Reempacotar não baixa nada de novo**: jobs da web rodam com `keep_work=True` e
  `_assemble(reuse=True)` reaproveita a pasta de trabalho. Só o `.zip` é refeito.
- **A escolha manual entra no manifest como `MatchStrategy.MANUAL`** e o jar
  correspondente é removido de `overrides/mods` — mas apenas se ele veio do
  matcher; arquivos que já estavam no `overrides/` do mrpack original nunca são
  apagados.
- **Desfazer uma escolha** devolve o mod para `overrides` (o jar é baixado de novo).
- **A busca da interface reordena os resultados** com `rank_projects`: a busca da
  API do CurseForge devolve lixo antes do projeto certo (uma consulta por
  "Just Enough Items" trazia `DeepCore'` em primeiro; com o ranking, o JEI vem em
  primeiro).
- **Upload valida antes de aceitar**: o arquivo precisa terminar em `.mrpack` e
  abrir como um mrpack válido, senão é apagado e o erro volta para a página.
- A pasta `.work/` é apagada quando o servidor encerra.

**O que a interface deliberadamente NÃO faz:** escolher versão sozinha. Ela mostra
as versões disponíveis e a evidência; quem decide trocar `0.28.2` por `0.28.3` é
você (mesma razão da §4b).

---

## 4d. Conversão em duas fases e um job por vez (v0.5)

**O problema que motivou:** os mods em conflito são exatamente os que seriam
baixados do Modrinth para `overrides/mods`. Baixar tudo e só depois deixar o
usuário resolver os conflitos significava baixar jars que seriam jogados fora
(em packs grandes, centenas de MB).

**Decisão:** a conversão virou duas fases explícitas.

| Fase | O que faz | Escreve em disco? |
|------|-----------|-------------------|
| `Converter.analyze()` | lê o `.mrpack`, consulta Modrinth e CurseForge, diagnostica | **não** |
| `Converter.finish()` | aplica escolhas, copia overrides, baixa o que falta, gera o `.zip` | sim |

A interface roda `analyze` e **para** se houver conflitos (`awaiting_conflicts`),
explicando que os downloads ainda não começaram. Sem conflitos, segue direto.
O CLI chama as duas em sequência e não muda de comportamento.

**Salvar ≠ aplicar.** Na aba de conflitos as escolhas ficam **pendentes** até o
botão *Salvar mudanças* (`PUT /resolutions`, salva todas de uma vez). Aplicar é uma
ação separada, na tela principal, que **mostra antes o que vai acontecer**
(quantos mods no manifest, quantos jars serão baixados, quantos arquivos extras)
e só age depois do *Continuar*. Motivo: aplicar baixa arquivos e regenera o zip —
não pode acontecer como efeito colateral de clicar numa versão.

**Um job por vez.** `/api/convert` responde **409** enquanto houver conversão
aberta, e "aberta" inclui *concluída mas não fechada* — o botão **Fechar conversão**
é o que libera a vaga. Isso evita dois jobs disputando a mesma pasta de trabalho e
deixa o resultado (com download e relatório) visível até o usuário decidir sair.

**Cancelamento cooperativo** (`threading.Event`), checado em três granularidades:
entre mods, entre downloads e **entre chunks** de cada arquivo. Medido: cancelar no
meio dos downloads do Prominence II (531 MB) leva **~1 segundo**. Quando o job está
pausado em conflitos não existe thread rodando para observar o evento, então
`JobManager.cancel` marca o estado imediatamente (bug encontrado testando ao vivo:
antes disso, cancelar uma conversão pausada não fazia nada).

**Cores e prefixos.** `not-on-curseforge` virou **vermelho** em toda a interface
(antes era amarelo, igual a "versão indisponível") — são situações bem diferentes:
uma você resolve escolhendo outra versão, a outra exige procurar outro projeto (ou
aceitar o override). No log, prefixos no estilo Terraform: `++` verde para sucesso,
`--` amarelo para "foi para overrides", `--` vermelho para projeto inexistente ou
erro.

**Layout master-detail.** A coluna esquerda concentra as listas e a conversão
(terminal e botões); a direita mostra detalhes do que está selecionado: modpack de
entrada (conteúdo do arquivo + nomes reais dos mods via API do Modrinth), conversão
em andamento (números e o que falta fazer) ou conversão anterior (resumo + todas as
decisões tomadas, lidas do `-report.json`).

---

## 4e. Só os metadados persistem; o `.zip` é regenerado (v0.6)

**Decisão:** o que fica guardado entre sessões é um **registro** por conversão em
`output_modpacks/conversions/<id>.json`. Na interface, fechar a conversão apaga o
`.zip` (e a pasta de trabalho) e mantém o registro.

**Por quê:** o `.zip` é derivado — com o `.mrpack` de origem e os
`(projectID, fileID)` já decididos, ele é remontado **sem nenhuma chamada ao
CurseForge**. Medido no pack de teste: regenerar leva **8 segundos** e produz um
arquivo idêntico (mesmos 46 itens no manifest, mesma escolha manual, mesmos jars em
`overrides/mods`). Guardar 500 MB por conversão para sempre não se paga.

O registro guarda, por mod: status, estratégia, `projectID/fileID`, projeto, o
diagnóstico completo (motivo, similaridade, versão mais próxima) e o mapa de
escolhas manuais. É o que alimenta a tela de detalhes **e** a regeneração.

**Cuidado importante:** `close()` só apaga o `.zip` quando **aquele job** o gerou
(`outcome.packaged`). O caminho de saída já é calculado no `analyze()`, então, sem
essa guarda, fechar uma conversão cancelada na análise apagava o `.zip` de uma
conversão anterior (ou do CLI) que tivesse o mesmo nome — foi exatamente o que
aconteceu num teste, antes da correção.

**Consequências assumidas:**

- se o `.mrpack` de origem sair de `input_modpacks/`, não dá mais para regerar —
  a lista marca o registro como *origem ausente* e o botão fica desabilitado;
- regenerar rebaixa os arquivos que vão para `overrides/` (esses não estão no
  registro, e nem deveriam);
- reconverter o mesmo pack **sobrescreve** o registro (a data de criação é
  preservada) e, por ser uma análise nova, **não reaplica** escolhas manuais
  anteriores;
- o **CLI continua deixando o `.zip`** em `output_modpacks/` — ali o objetivo é
  produzir o arquivo, não navegar por conversões.

Isso substituiu a listagem de `.zip` da interface (e o antigo
`<nome>-report.json`, cujo conteúdo virou parte do registro).

---

## 4f. Correções de estado da interface (v0.6)

Três bugs reportados, todos de *estado exibido* e não de conversão:

1. **As contagens de conflito não diminuíam** ao resolver. Causa: `conflicts()`
   devolve todos os conflitos (inclusive os resolvidos — de propósito, para o
   usuário ver a escolha feita) e a interface usava esse total em todo lugar.
   Correção: o job passou a expor `unresolved` além de `conflicts`, e a interface
   mostra `unresolved` no selo da aba, no aviso e nos botões. O `plan()` também
   passou a receber as resoluções ainda não aplicadas — antes ele anunciava
   "4 downloads" mesmo depois de você resolver um.
2. **O popup do "Aplicar" continuava aberto** depois de confirmar, e o botão
   sobrevivia à conversão terminada. Correção: o painel é escondido no clique
   (não só no próximo poll) e o botão em jobs concluídos depende de `dirty` —
   ou seja, só aparece se houver escolha salva **ainda não aplicada**.
3. **A saída recém-convertida não abria na lista de conversões anteriores.** A
   lista era construída a partir dos `.zip` em disco e a de detalhes lia um
   arquivo de relatório em formato antigo. Resolvido pela mudança da §4e: a lista
   agora vem dos registros.

Também saiu a aba **Relatório**: ela duplicava (mal) o que o painel de detalhes já
mostra por conversão.

---

## 4g. Resumo da análise no estilo `terraform plan` (v0.7)

**Decisão:** durante a busca **nada é logado por mod**; quando ela termina, sai um
bloco único, ordenado e agrupado:

```
Resultado da análise

++ 392 mod(s) encontrados no CurseForge (não listados)

-- 7 mod(s) sem essa versão no CurseForge (vão para overrides):
     -- kleeslabs-fabric-1.20-15.0.4.jar
        KleeSlabs · mais próxima lá: 1.21.3-kleeslabs-...-21.3.1.jar (91%)

-- 7 mod(s) sem projeto no CurseForge (vão para overrides):
     -- bettertrims-2.3.2.jar
        Modrinth: BetterTrims

Resumo: 392 no manifest · 7 sem a versão · 7 sem projeto
```

**Por quê:** a busca roda em paralelo, então as linhas por mod saíam fora de ordem
e, num pack de 406 mods, 392 linhas de sucesso enterravam justamente as 14 que
exigem decisão. Sucesso vira contagem; o que importa fica listado e agrupado.

Vale para o CLI e para a interface (é o mesmo `Converter._log_analysis`). A tabela
detalhada que o CLI imprimia no fim foi removida — era a mesma informação, duas vezes.

**Detalhes que custaram bug:** no log da interface, o nível de cor vem da
**primeira** tag da linha (a linha de resumo tem verde, amarelo e vermelho juntas e
não é um erro), a indentação é preservada (`rstrip`, não `strip`) e linhas vazias
viram espaçadores em vez de serem descartadas.

---

## 4h. Conflito resolvido some da aba (v0.7)

**Decisão:** depois que o modpack é empacotado, `Job.conflicts()` devolve lista
vazia — a aba **Conflitos** fica limpa e o selo zera.

**Por quê:** depois de aplicar não existe mais decisão pendente: o que foi escolhido
está no manifest e o resto já foi para `overrides`. Manter os cartões lá sugeria
trabalho a fazer que não existe mais.

**Consequência assumida:** para mudar de ideia depois de aplicar, é preciso
reconverter o pack. O histórico do que foi decidido não se perde — ele fica no
registro, agora exibido agrupado (escolhidos à mão / versão indisponível / projeto
não encontrado), com o formato *esperado × encontrado* em cada linha.

---

## 4i. Ajustes de interface (v0.7.1)

Lote de refinamentos pedidos depois de usar a interface de verdade:

- **Três colunas**: entradas/conversões salvas · conversão · detalhes. O terminal
  passou a ocupar a coluna do meio inteira (460 px de altura) e a página usa
  100% da largura — antes sobrava faixa vazia à direita.
- **Cabeçalho fixo**: título e abas ficam grudados no topo num bloco só, sem
  encolher (o incômodo anterior era a barra de abas sozinha, "flutuando").
- **Log em duas etapas**: volta a sair **uma linha por mod em tempo real** durante
  a busca e, no fim, o resumo agrupado (§4g) — os dois, não um ou outro. As linhas
  são emitidas pela thread que coleta os resultados, não pelo matcher, para
  acompanharem a barra de progresso.
- **Números coloridos no resumo**: o log da interface passou a suportar trechos
  com cores diferentes na mesma linha (`_segments`), então `45` sai verde, `4`
  amarelo e `0` vermelho — antes a linha inteira herdava uma cor só.
- **Aviso ao aplicar**: o log ganha um bloco "Aplicando as mudanças — baixando o
  que falta e gerando o .zip" quando o `finish` começa.
- **Avisos (toasts) maiores**, no topo e centralizados: no canto inferior direito
  passavam despercebidos.
- **Conflitos em três seções** — *sem equivalente* · *versão indisponível* ·
  *resolvidos* — e resolver/desfazer move o card entre elas. O botão *desfazer*
  ficou no cabeçalho do card, sem precisar abrir.
- **Abrir um conflito já busca**: versão indisponível carrega direto as versões do
  projeto detectado; sem equivalente já dispara a busca pelo nome do Modrinth. A
  caixa de busca continua ali para refazer com outro termo.
- **Versão do Minecraft do pack** fica visível na aba de conflitos, as versões
  compatíveis aparecem primeiro e levam um selo *compatível*.
- **Fim da conversão**: o painel de detalhes abre automaticamente o registro
  recém-criado, com o mesmo resumo agrupado das conversões salvas.
- Corrigidos: caminho longo da pasta furando o card, e o painel "o que vai
  acontecer" que continuava aberto se a conversão fosse cancelada.

---

## 4j. Erro de Content-Length no download (v0.7.2)

**Sintoma reportado:** `h11._util.LocalProtocolError: Too much data for declared
Content-Length`, com o traceback passando por
`starlette/middleware/base.py` durante o envio do corpo.

**Causa raiz — duas, somadas:**

1. O middleware de cabeçalhos usava `@app.middleware("http")`, que é
   `BaseHTTPMiddleware`: ele **reencaminha o corpo pedaço a pedaço**. O
   `Content-Length` é calculado do `os.stat()` no início; se o arquivo mudar de
   tamanho durante o envio, o h11 aborta a conexão.
2. `build_zip` escrevia **direto no arquivo final** (apagava e recriava). Um
   download em andamento via o `.zip` ser recriado embaixo dele.

**Correções:**

- middleware virou **ASGI puro** (só mexe em `http.response.start`, nunca no
  corpo) — o `FileResponse` volta a transmitir o arquivo diretamente;
- `build_zip` ficou **atômico**: monta em `<nome>.zip.part` e só então ocupa o
  nome final, com retentativas se o destino estiver travado (Windows) e uma
  mensagem clara caso não dê ("o arquivo está em uso, tente de novo");
- `close()` não explode mais se o `.zip` estiver travado por um download em
  andamento — ele é regenerável, então o erro é ignorado.

Testes cobrem os três pontos (cabeçalhos + `Content-Length` no download, zip
atômico, destino travado, fechar com arquivo em uso). Verificação real: download
de 62.203.952 bytes com `Content-Length` batendo e zip íntegro.

---

## 4l. O mesmo erro, outra origem: 204 com corpo (v0.7.3)

Depois de corrigir o §4j, o `Too much data for declared Content-Length` voltou —
agora **ao abrir a página**, e com outro traceback (`responses.py` mandando
`self.body`, não um `FileResponse`).

Causa: o endpoint `/favicon.ico` respondia
`JSONResponse(status_code=204, content=None)`. Um **204 não pode ter corpo** —
o Starlette nem declara `Content-Length` nesse status — mas o `JSONResponse`
serializava `null` (4 bytes). O h11 recebia corpo onde não devia haver nenhum e
abortava a conexão. Como o navegador pede `/favicon.ico` sozinho, o erro aparecia
em todo carregamento.

Correção: o endpoint serve um favicon SVG de verdade (`static/favicon.svg`, também
declarado no `<head>`). Teste garante status 200, `Content-Length` igual ao corpo e
conteúdo SVG.

Lição registrada: resposta sem corpo precisa ser `Response(status_code=204)` —
qualquer `*Response` com `content` serializa alguma coisa.

---

## 4m. Piscar dos botões e seleção perdida (v0.7.3)

O polling de 600 ms **não parava** quando a conversão ficava pausada em
`awaiting_conflicts`, e cada tique reescrevia o `innerHTML` do aviso e dos botões.
Resultado: botões piscando e qualquer texto selecionado sumindo sozinho.

Duas correções, ambas necessárias:

1. o polling agora para também em `awaiting_conflicts` (nada muda sozinho ali; quem
   dispara a próxima etapa é o usuário, e cada ação já chama `startPolling`);
2. as escritas no DOM passam por `setHTML`/`setText`/`setClass`, que **só tocam no
   documento quando o conteúdo muda de verdade** — e os eventos só são reassinados
   quando o HTML foi de fato reescrito.

Também corrigido: o aviso ficava verde ("todos resolvidos") mesmo com escolhas
ainda não salvas. Agora só fica verde quando não há nada pendente — nem conflito
sem decisão, nem alteração sem salvar.

Os avisos (toasts) voltaram para o canto inferior direito, mantendo o tamanho maior.

---

## 4k. Três colunas de verdade (v0.7.2)

A divisão em três da aba principal estava desigual (`340px | 1fr | 400px`) e a de
conflitos era vertical. Agora ambas são `repeat(3, minmax(0, 1fr))` — três colunas
iguais lado a lado, caindo para duas abaixo de 1400px e uma abaixo de 900px. Nas
colunas estreitas as linhas de arquivo/projeto quebram em vez de vazar.

No resumo em diff, os rótulos secundários ficaram cinza e o **conteúdo** ganhou
cor por tipo: nome do projeto no CurseForge em ciano, versão mais próxima em
amarelo, nome no Modrinth em ciano. Antes o arquivo `.jar` e o
`Modrinth: <nome>` saíam no mesmo cinza e não dava para distinguir o que era o quê.

---

## 4n. Duas camadas novas de busca (v0.8)

Investigando por que "Better Combat" não era encontrado (nem manualmente),
apareceram **dois problemas diferentes**, e cada um exigiu uma correção:

**1. Sufixos decorativos no nome do projeto.** O mod existe como
`Better Combat [Fabric & Forge]` (slug `better-combat-by-daedelus`, que também não
bate com o slug do Modrinth). A busca por "Better Combat" **retorna** o projeto,
mas o sufixo derruba a similaridade: ele caía na **14ª posição** do nosso ranking e
só inspecionamos os 8 primeiros candidatos.

Correção: `clean_project_name` remove trechos entre `[]`/`()` e sufixos de loader
antes de comparar; nome limpo igual à consulta vale o mesmo bônus que um nome
exato. Medido: o projeto certo passou da **posição 14 para a 1ª**. A limpeza é
conservadora de propósito — "Fabric API" e "Sodium Extra" continuam intactos, para
não criar equivalências falsas.

**2. Grafia diferente do mesmo nome.** `Extended AE` no Modrinth é `ExtendedAE` no
CurseForge, e a busca com espaço devolve "Extended flax", "Extended Slabs",
"Extended Food"… — o projeto certo não aparece de jeito nenhum.

Correção: nova estratégia `MODRINTH_VARIANT`, entre o título e as heurísticas sobre
o nome do arquivo. Ela gera outras grafias (`ExtendedAE`, `extended_ae` e junções de
um espaço por vez, úteis em nomes de três ou mais palavras) e refaz a busca. Com
`ExtendedAE` o projeto certo vem em **1º**.

**3. O slug do CurseForge costuma ser o título slugificado.** O Essential Mod
existe no CurseForge como slug `essential-mod`, mas no Modrinth o slug é
`essential` — nosso lookup falhava, e a busca textual por "Essential Mod" devolve
100 resultados de "Niglo Essentials", "SMP Essentials"… sem o projeto certo.

Correção: além do slug do Modrinth, tentamos o **título slugificado** e os **slugs
das variações de grafia** (`Extended AE` → `extendedae`). Lookups por slug são
exatos e devolvem 0 ou 1 resultado, então rodam **antes** de qualquer busca textual.

**4. O loader desempata nomes genéricos.** "Things" devolve resultado demais;
"Things fabric" devolve um punhado. Nova estratégia `MODRINTH_LOADER` acrescenta o
loader do modpack ao nome, logo depois das variações.

**Resultado medido no Prominence II (406 mods):**

| | no manifest | sem projeto | sem a versão |
|---|---|---|---|
| antes | 392 | 7 | 7 |
| limpeza de nome + variações | 396 | 5 | 5 |
| **+ slugs derivados e loader** | **400** | **1** | **5** |

Sobrou só o `PhilipsRuins`, que aparentemente não está mesmo no CurseForge.
Nenhuma regressão no pack menor (45/49, zero "sem projeto").

Custo: as camadas extras só rodam para mods que ainda não casaram, e consultas
repetidas são deduplicadas — quem casa no primeiro slug (a maioria) não paga nada.

A busca manual da interface passou a trazer 3 páginas (150 resultados): "Better
Combat" sozinho devolve 138, e com 2 páginas o projeto certo podia ficar de fora.

---

## 4o. Compatibilidade considera o loader (v0.8)

O selo *compatível* na lista de versões olhava só a versão do Minecraft, então
`bettercombat-forge-1.9.0+1.20.1.jar` aparecia como compatível num modpack Fabric.
Agora exige **versão do Minecraft e loader do pack** (arquivos que não declaram
loader — datapacks, resourcepacks — continuam aceitos). O loader do pack ficou
visível ao lado da versão do Minecraft na aba de conflitos.

A ordenação da lista virou, em ordem: **arquivo idêntico ao do pack** (destacado em
dourado — é o alvo ideal e o que o matcher procura), compatíveis, versão do
Minecraft mais nova, loader do pack e por fim `release` > `beta` > `alpha`.

---

## 4q. O mod original à vista na hora de escolher (v0.9.2)

Ao resolver um conflito você via o ícone e o nome dos **candidatos do CurseForge**,
mas não tinha com o que comparar: o mod que está no seu pack aparecia só como nome
de arquivo. Agora cada card abre com um bloco de referência — ícone, nome e link do
projeto **no Modrinth** — logo acima da busca e da lista de versões.

Para isso o `ModrinthProject` passou a guardar `icon_url` (vem de graça no mesmo
`GET /projects?ids=[]` que já buscava slug e título). O namespace do cache virou
`mr_project2`: as entradas antigas não tinham o ícone, e trocar o nome revalida
tudo sem precisar de migração.

O `img-src https:` da CSP (§4c) já permitia o CDN do Modrinth.

---

## 4p. Faxina (v0.9.1)

Revisão do repositório inteiro procurando o que dava para cortar sem perder nada:

- **`ModEntry` e `ConversionReport.mods` saíram.** Depois que o registro passou a
  ser a fonte do detalhamento (§4e), a lista mod a mod do relatório só era usada
  para *contar* — agora `build_report` conta direto sobre os resultados. O
  relatório voltou a ser o que o nome diz: números.
- **A classificação de status virou uma só.** `reporting` e `records`
  classificavam cada mod (curseforge / version-unavailable / not-on-curseforge /
  failed) com o mesmo `if` duplicado; virou a propriedade `MatchResult.status`.
- **Removidos por não serem usados**: `ManifestError`, `MatchError`,
  `NullReporter`, `ModrinthEnvironment`/`ModrinthFile.env`, o parâmetro `loader`
  da busca e o mapa `CURSEFORGE_LOADER_TYPES` (o filtro `modLoaderType` nunca foi
  ligado — quem cobre esse caso é a estratégia `MODRINTH_LOADER`, que é textual).
- **`.flake8`** fixando 88 colunas (o limite em que o projeto sempre foi escrito),
  e as 13 linhas que passavam disso foram quebradas. O editor parou de acusar
  ~40 avisos por arquivo.

Nada de comportamento mudou: os 100 testes continuam passando e o fluxo completo
foi verificado de novo ponta a ponta.

---

## 4r. Atualizador de mods (v0.10)

Segunda ferramenta do projeto: pega um `.mrpack`, uma versão do Minecraft, e
devolve um `.mrpack` com cada mod na versão mais recente para aquele Minecraft.

**Decisões principais:**

- **Não baixa nada.** O `modrinth.index.json` guarda URL, tamanho e hashes de cada
  arquivo — dá para montar o pack novo só com consultas. Atualizar o pack de teste
  (59 arquivos) leva ~6s e o `.mrpack` sai com o mesmo tamanho do original.
- **O que não tem versão para o alvo é mantido**, não removido. `INCOMPATIBLE` (o
  projeto existe mas não publicou para aquele Minecraft) e `UNKNOWN` (o arquivo não
  é do Modrinth) ficam no índice como estavam e aparecem no resumo. Mesma filosofia
  do conversor: melhor manter do que sumir em silêncio.
- **`release` antes de `beta`/`alpha`**, e a mais recente dentro do mesmo tipo. A
  interface marca quando a versão escolhida não é release.
- **Preservados**: o `env` (client/server) de cada arquivo — que tinha sido
  removido do schema na faxina §4p e voltou porque agora tem uso real — e o sufixo
  `.disabled`, para que atualizar não reative um mod que você tinha desligado.
- **Filtro de loader só para mods.** Resourcepacks e shaders não têm loader; passar
  `loaders=["fabric"]` neles não devolveria nada.
- **A versão do loader é mantida** por padrão (`--loader-version` troca). O Modrinth
  não sabe qual loader combina com qual Minecraft, e chutar seria pior.
- **Limitador de vazão no cliente do Modrinth** (240 req/min contra o teto de 300):
  a atualização faz uma consulta por projeto, e um pack de 400 mods estouraria o
  limite fácil.
- **Alvo anterior ao pack é sinalizado.** Escolher um Minecraft mais antigo é
  legítimo (portar um pack para trás), mas aí os mods vão para versões *mais
  antigas* — o CLI e a interface avisam, e a palavra "atualizados" vira "trocados
  de versão".

**Integração com o conversor:** o pack atualizado tem um botão *Usar como entrada
da conversão*, que o copia para `input_modpacks/`. As duas ferramentas compartilham
job, cache, limitador e painel de progresso; só um trabalho roda por vez.

**Conflitos do atualizador (v0.10.1):** o que fica sem versão para o alvo agora vai
para a aba *Sem versão*, que lista **todas** as versões publicadas do projeto — sem
filtrar por Minecraft, que é justamente o ponto: você vê o que existe e decide. As
versões que servem no alvo aparecem marcadas, e o filtro de loader só é aplicado
para mods. Escolher grava um `version_id`; o servidor busca a versão e regera o
`.mrpack` (`Updater.reapply`). Desfazer devolve o mod ao diagnóstico automático.

**Navegação em dois níveis (v0.10.1):** a barra de cima escolhe a **ferramenta**
(Conversor ou Atualizador) e a de baixo mostra só as abas dela. Antes o atualizador
era uma aba solta no meio das do conversor.

**O job precisa dizer o que ele é (v0.10.2):** iniciar uma atualização levava tudo
para a tela de conversão. Causa: o `kind` do job **não ia no snapshot** — a
interface lia `job.kind === undefined` e caía sempre no ramo da conversão. Os
testes de API não pegaram porque verificavam o conteúdo (`snapshot["update"]`), que
estava certo; faltava o campo que decide o roteamento. Agora `kind` vai no snapshot
e no `/api/state`, com teste garantindo os dois tipos, e a interface ainda usa a
presença do bloco `update` como segunda pista. De quebra, ao reencontrar um
trabalho (recarregar a página no meio dele), a interface abre a ferramenta dona
dele em vez de deixar o progresso escondido na outra aba.

Um bug pego no primeiro teste real: o cálculo de "arquivos fora do índice"
comparava o caminho **antigo** com o índice novo, e como mods atualizados mudam de
nome de arquivo, ele acusava todos os 49 atualizados como perdidos.

---

## 4s. O atualizador também propõe antes de aplicar (v0.11)

O atualizador analisava e **já gravava** o `.mrpack`. Agora ele segue o mesmo
contrato do conversor: `analyze()` só consulta, o resultado é uma **diff
revisável**, e só o `apply()` escreve.

- Aba **Revisar mudanças**: cada mod que ganharia versão nova aparece com
  `de → para` e um **checkbox**. Desmarcar é dizer "mantenha como está"
  (`UpdateResult.skipped`), e isso vira a contagem `kept_by_choice` no resumo.
- Aba **Sem versão**: continua sendo onde se escolhe uma versão à mão para o que
  não tem nada no alvo.
- As duas abas alimentam a mesma decisão e o mesmo botão de aplicar; reaplicar
  depois de gerar simplesmente regera o pack.

**As duas ferramentas ficaram independentes.** `JobManager.current(kind)` separa o
trabalho aberto por ferramenta: dá para ter uma conversão e uma atualização ao
mesmo tempo (verificado: iniciar conversão com atualização aberta responde 200, e
uma segunda atualização responde 409). No front, a atualização ganhou job, polling
e estado próprios — antes dividia `state.job` com a conversão, o que também era a
raiz do painel trocado.

**Saída da atualização:** os `.mrpack` gerados aparecem em *Packs atualizados*,
lidos dos `*-update.json` da pasta de saída. Clicando, o painel mostra as decisões
agrupadas e os botões **⬇ Baixar**, **Adicionar ao input** (retroalimenta a
conversão) e **Excluir**. Diferente do conversor, aqui o arquivo **fica**: ele é o
produto e é pequeno (o índice não carrega os jars).

---

## 4t. A revisão é sobre *entrar ou não* no pack (v0.12)

A pergunta que a revisão precisa responder mudou. Não é "aplico esta
atualização?" — é **"este arquivo entra no pack novo?"**. Um shader como o
*Simply 3D* está publicado com uma versão fixa do Minecraft mas roda em muitas;
um `.jar` de mod compilado para outra versão costuma quebrar o jogo. São
decisões opostas para o mesmo sintoma ("não achei versão para o alvo"), então
quem decide é o usuário — com um padrão que erra pouco.

**Padrão (`default_excluded`)**: quem tem versão para o alvo sempre entra; quem
não tem entra se **não for mod** (resourcepack, shader, datapack) e fica de fora
se for. Isso é só o ponto de partida — as duas decisões estão a um clique.

**Três abas**, porque são três perguntas diferentes:

| aba | o que tem lá | o que dá para fazer |
| --- | --- | --- |
| **Com versão** | achou versão para o alvo | trocar a versão à mão, ou manter a atual |
| **Sem versão** | não achou, e vai ficar de fora | incluir assim mesmo, ou escolher uma versão à mão |
| **Incluídos assim mesmo** | não achou, mas vai entrar | tirar do pack |

Incluir/excluir move o card de aba na hora, sem ida ao servidor: a decisão vive
em `state.decisions` no front e vira `UpdateDecisions(versions, keep, exclude,
include)` no `PUT /api/jobs/{id}/update-resolutions`. O backend nunca confia no
`excluded` que a análise carimbou — `_decided_exclusion()` consulta a decisão do
usuário e só cai no padrão quando não há uma.

**Trocou a versão à mão? Fica visível.** O card ganha borda dourada, a tag
*"versão escolhida por você"* e **sobe para o topo da lista** — em 57 mods, o que
você mexeu não pode ficar perdido no meio. `restore_auto()` desfaz e devolve o
resultado automático. No seletor, as versões que servem no alvo vêm primeiro
(eram 60 numa ordem só cronológica; agora as 16 compatíveis aparecem antes).

**Cancelar não é sucesso.** O card verde de "deu certo" aparecia também para job
cancelado, porque o `else` final do aviso pegava qualquer estado com `update`.
Agora `cancelled` tem aviso neutro próprio (e o conversor também), e o verde só
sai em `done`.

---

## 4u. O atualizador vira uma cópia da aba de conflitos (v0.13)

As três abas da v0.12 respondiam bem à pergunta certa, mas eram um padrão novo
num app que já tinha um padrão para exatamente isso: a **aba de conflitos** do
conversor. Trocamos por **uma aba com três seções**, com as mesmas peças
(`.conflict-section`, `.conflict`, `.conflict-head`), o mesmo par *Salvar
mudanças* / *Aplicar mudanças* e o mesmo modelo de estado pendente no front.

```
Sem versão  ──(escolhe um .jar)──►  Resolvidos por você  ◄──(troca a versão)──  Com versão
```

**A cor diz de onde o card veio.** `.from-missing` (verde) para quem estava sem
versão; `.from-version` (dourado) para quem você tirou do caminho automático. São
situações diferentes — uma é "achei um jeito", a outra é "discordo da proposta" —
e no meio da lista de resolvidos isso precisa se distinguir de relance.

**Trocar de projeto, não só de versão.** O mod certo às vezes é outro projeto:
fork, renomeado, ou um jar de outra origem. `/api/modrinth/search` faz o mesmo
papel do "procurar outro projeto" dos conflitos. Como o `version_id` do Modrinth
já identifica a versão sozinho, trocar de projeto não exigiu nada no motor —
só `_retag_project()`, para o card passar a mostrar o projeto novo, e
`auto_modrinth`, para o desfazer voltar ao detectado.

---

## 4v. Trocar de modloader (v0.13)

Era, como o usuário suspeitou, **só mais um filtro**: `latest_version(...,
loader=alvo)` em vez do loader do pack. O que não era óbvio:

- o índice precisa trocar a **chave** da dependência (`fabric-loader` →
  `neoforge`), não só o valor — daí o `loader` em `build_index()`;
- a **versão do loader** vira obrigatória. A do fabric que está no pack não
  significa nada para o neoforge, e inventar uma geraria um `.mrpack` que o
  launcher recusa. Recusamos antes, com mensagem explícita, e a interface
  desabilita o botão até o campo estar preenchido;
- o resultado é honesto sobre o custo: no pack de teste, `fabric → neoforge` no
  1.21.11 deixou 41 mods com versão e **18 sem** — que caem na revisão em vez de
  sumirem.

---

## 4w. Sufixo no nome e o cache que não limpava (v0.13)

**`[convertido]` / `[atualizado]`** no nome da saída, porque com as duas
ferramentas escrevendo na mesma pasta o nome sozinho não dizia de onde o arquivo
veio. Dois detalhes: o marcador é acrescentado **depois** do `safe_name()` (que
trocaria os colchetes por `_`), e `rebuild()` passou a usar o `id` do registro em
vez de recalcular o nome — regerar uma conversão antiga não pode renomeá-la.

**Limpar cache** ganhou botão (`DELETE /api/cache`) e reaproveita o
`clear_cache()` do CLI. Ao implementar, o botão só respondia *"em uso"*: cada
`with ModrinthClient(SimpleCache(...))` fechava o cliente HTTP mas **nunca o
cache**, e no Windows um SQLite aberto não pode ser apagado. Ou seja: o servidor
vazava uma conexão por requisição. O `SimpleCache` agora entra no mesmo `with` em
todos os pontos (helper `modrinth_client()` no servidor).

**A lista de entrada** passou a mostrar Minecraft, loader e nº de mods, lidos do
índice e memoizados por `(caminho, mtime, tamanho)` — o `/api/state` é consultado
a cada 600 ms e abrir um zip por pack a cada consulta seria absurdo.

---

## 4x. Varredura de estado e faxina (v0.13.1)

Os bugs que escaparam nesta base foram quase todos do mesmo tipo: **um estado da
interface que ninguém tinha olhado**. Em vez de caçar de novo à mão, o `app.js`
passou a rodar num DOM de mentira (`node tools/check_ui.js`, sem dependências) e
o teste **afirma**, para cada `status` de job, qual aviso e quais botões
aparecem. São 29 verificações; provei que falham reintroduzindo o bug do card
verde ao cancelar.

O que a varredura encontrou:

| problema | por quê |
| --- | --- |
| *Aplicar* sumia com escolhas não salvas | o conversor recusava com um toast e o usuário ficava sem saída; agora as duas ferramentas **salvam antes de aplicar** |
| o painel "o que vai acontecer" mostrava o plano velho | o polling **para** em `awaiting_conflicts`; salvar precisava reler o job |
| atualização pronta ignorava decisões novas | `job.dirty` existia no payload e ninguém lia; agora ele é o que faz o *Aplicar mudanças* reaparecer |
| `reapply` funcionava num job cancelado | faltava a guarda de estado (só `awaiting_review` e `done`) |
| `goToTab` de outra ferramenta abria a aba errada | `selectTool` sempre abria a primeira; passou a receber a aba pedida |
| cards piscando durante o `finishing` | `#conflict-groups`/`#ur-groups` eram reescritos crus a cada poll |
| aba de revisão vazia depois de fechar o job | `leaveEmptyTab` devolve o usuário para a aba principal |

As bordas da API foram varridas junto (job inexistente, estado errado, travessia
de caminho, arquivo sumido): 37 rotas, todas já se comportavam.

**Faxina.** Saíram 17 campos de payload que o front nunca leu — o `/api/state` é
consultado a cada 600 ms, então isso é tráfego por segundo. Saíram também o
`DELETE /api/packs` (sem uso, e apagaria o `.mrpack` de um job aberto sem avisar),
`Modpack.source_path`, duas constantes de JS e o CSS de uma tabela de resultados
que não existe mais. Em `jobs.py`, o `try/except` que cada runner repetia virou um
`_spawn()` — além de encurtar, garante que um runner novo não esqueça de tratar
cancelamento.

O que **não** foi mexido: os renderizadores parecidos (conflitos × revisão,
busca do CurseForge × do Modrinth). Parecem duplicados, mas os dados são
diferentes o bastante para que unificá-los custasse mais leitura do que economiza.

---

## 4y. A revisão só pergunta uma coisa (v0.14)

A seção *Sem versão* tinha um par de botões por card (incluir / não incluir) e um
padrão que decidia por você (mod sai, não-mod entra). Duas coisas erradas: o par
competia com a escolha de versão pela atenção, e o padrão tomava por você a única
decisão que quebra o jogo.

Agora **nada sem versão entra sozinho** — `default_excluded` é simplesmente
`not has_version`. O card oferece só o que resolve: escolher uma versão. Para o
caso legítimo em massa (resourcepacks, shaders e datapacks funcionam além da
versão em que foram publicados) há **um botão no topo da seção**, que inclui
todos os não-mods de uma vez e vira "tirar" quando já estão dentro.

Dois ajustes de leitura junto: resolvido que veio de *Com versão* passou a
**roxo** (era dourado) e vai para o **topo** da seção — é o que você contrariou,
merece destaque sobre o que só faltava resolver. E a lista de versões marca a que
**já está no seu pack**, para comparar sem sair da tela.

---

## 4z. Versão do loader: dropdown em vez de campo livre (v0.14)

Pedir a versão do loader num campo de texto era pedir para o usuário sair da tela
e caçar o número. Ninguém publica isso junto com os mods: `services/loaders.py`
consulta o serviço de cada loader — `meta.fabricmc.net`, `meta.quiltmc.org` e o
`maven-metadata.xml` do NeoForge e do Forge — e filtra pela versão do Minecraft
escolhida (no NeoForge o filtro é o próprio número: MC `1.21.11` → `21.11.*`).

A opção padrão é **mais recente**; a versão que está no pack aparece como
alternativa quando ainda serve. Trocar de Minecraft recarrega a lista e, se a
versão escolhida não existir no alvo novo, a interface avisa em vez de mandar um
número inválido.

**Descoberta ao testar:** o maven do NeoForge devolve **404 esporádico** para uma
URL que existe — reproduzido ~1 em 3 na primeira chamada, com a seguinte
funcionando. Sem retentar, o dropdown ficava vazio sem motivo. O `_fetch` agora
tenta 3 vezes, e uma falha **nunca** é cacheada: um serviço fora do ar não pode
deixar a lista vazia guardada.

---

## 5a. Uma tela por aba (v0.15)

As abas principais rolavam a página inteira: para ver a lista de conversões
salvas você perdia de vista o progresso, e o painel de confirmação do conversor
sozinho empurrava tudo para fora da tela.

Agora **a página não rola** nas abas principais. `body` é flex-column, `main` é
o único container com rolagem, e `.panel.split` ocupa `height: 100%` — as três
colunas cabem na tela e cada uma rola por dentro. Deliberadamente **sem**
`calc(100vh - 150px)`: número mágico de cabeçalho quebra na primeira vez que o
cabeçalho muda (e ele mudou três vezes só nesta sessão).

Para caber, duas mudanças de conteúdo:

- **entrada e saída dividem a coluna 1** (`.switch` + `.side`) em vez de
  empilhar dois cards — empilhado, o de baixo ficava sempre fora da tela;
- os três seletores do atualizador (Minecraft, loader, versão) passaram a ficar
  **lado a lado**, e o painel de confirmação encolheu (fonte, espaçamento e um
  teto de `40vh` com rolagem própria).

As abas de revisão continuam rolando a página: são listas longas de propósito.

---

## 5b. O atualizador ganhou o resto do conversor (v0.15)

Três coisas que existiam só de um lado:

- **Painel "o que vai acontecer"** antes de aplicar. A diferença é de onde vem o
  plano: no conversor o servidor calcula (`outcome.plan`), no atualizador é o
  front (`updatePlan()`) — lá ele precisa contar também as decisões que ainda
  não foram salvas, e o servidor não as conhece.
- **O painel da direita mostra o conteúdo do pack** enquanto não há resultado —
  o mesmo `renderInputDetail`, agora parametrizado pelo elemento de destino.
- **Trocar de ferramenta volta para onde você estava** (`state.lastTab`), em vez
  de sempre cair na primeira aba.

E na revisão, o incluído em massa ganhou identidade própria: quem entrou pelo
botão dos não-mods fica em **verde escuro** e **por último** na seção do meio.
Antes dividia o verde com quem teve versão escolhida à mão, e as duas coisas
pedem atenção bem diferente — uma foi decisão item a item, a outra foi um clique
só. Pelo mesmo motivo o botão de **tirar** mudou de lugar: fica na seção do meio
(onde estão os incluídos), e o do topo de *Sem versão* só inclui. Um botão que
troca de significado conforme o estado é um botão que se lê errado.

---

## 5c. O rodapé do job (v0.16)

Os botões ficavam **acima** do log, e o painel de confirmação empurrava o log
para baixo ao abrir — a tela inteira dançava na hora de confirmar.

Agora a ordem é progresso → aviso → log → botões, e o rodapé (`.job-foot`)
**reserva 160 px de altura mesmo fechado**. O painel é `position: absolute`
ancorado nesse rodapé: ele aparece por cima dos botões, com sombra, e **nada
mais se mexe**. O espaço reservado é exatamente o que o popup vai ocupar — sem
ele o rodapé fica vazio, e isso é de propósito.

## 5d. Não dá para mexer no que já foi enviado (v0.16)

Durante o `finishing` as abas de revisão continuavam clicáveis. Salvar uma
decisão ali não teria efeito nenhum — o `apply` já levou as que valiam — e o
usuário não teria como saber. As duas abas agora ficam **só de leitura**
enquanto o arquivo é gerado, com um aviso dizendo por quê.

## 5e. Cor como informação (v0.16)

A versão do Minecraft e o loader apareciam como texto corrido nos conflitos
(`1.21.1, 1.21, 1.20.6`) e como tag só na lista de entrada. Agora são as mesmas
tags em toda parte, e a cor carrega informação:

- **cada loader tem a sua** — fabric âmbar, quilt roxo, forge azul, neoforge
  laranja;
- **a versão do Minecraft é um gradiente** (`mcHue`): 1.21 em azul, descendo 18°
  de matiz por versão até o vermelho no 1.7. Dá para ver "isto é bem mais
  antigo que o resto" sem ler número nenhum.

A tag da versão-alvo ganha contorno. Versão que o `mcHue` não entende fica com
a cor padrão em vez de sumir.

## 5f. O ponto de partida é o pack (v0.16)

Os seletores do atualizador abriam na versão mais recente do Minecraft. Agora
abrem **no que o pack é hoje** — Minecraft, loader e versão do loader. Mexer é a
decisão que você veio tomar, e assim ela fica explícita; depois que você mexe num
seletor, trocar de pack não sobrescreve mais aquela escolha (`mcTouched`,
`loaderTouched`, `loaderVersionTouched`).

O dropdown de versão do loader passou a pôr o **número primeiro**
(`0.19.3 — a do pack`), porque é o número que se procura na lista.

---

## 5g. Ajustes de leitura (v0.17)

- **Os botões voltaram para cima do log**, e o popup passou a cobrir o aviso e
  os botões (`.job-head` relativo, `.confirm` absoluto). A versão anterior
  reservava um rodapé de 160 px que ficava vazio quando não havia popup.
- **A trava da revisão perdeu o texto.** O aviso "Gerando o modpack…" ficava na
  tela depois de terminar, porque `renderConflicts` só rodava de novo quando
  algo mais mudava. Um aviso que sobra é pior que nenhum: agora a seção apenas
  fica apagada e sem cliques enquanto o `finishing` dura.
- **Trocar de pack no atualizador volta sempre ao padrão dele** (Minecraft,
  loader e versão do loader). Antes, mexer num seletor "travava" a escolha e o
  pack seguinte herdava um alvo que não era de ninguém.
- **Bug do `KEEP`:** `"keep"` é o sentinela de "manter a versão do pack" no
  dropdown, e ele estava vazando para o texto de aviso quando a lista recarregava
  sem essa opção — aparecia um `keep não serve aqui` sem sentido. O aviso agora
  só fala de números de versão de verdade, e o hint passou a dizer qual é a do
  pack (`pack: 0.19.3`).

## 5h. A lista lembra o que você usou (v0.17)

O `/api/state` passou a devolver `last_used` por pack de entrada — a data do
último trabalho feito com ele, juntando os registros de conversão e os
relatórios de atualização. A lista ordena por isso, com duas exceções que vêm
antes: o pack que está rodando agora e o pack do trabalho aberto (que continua
marcado depois de terminar, para você saber de onde veio o resultado).

## 5i. Botão de encerrar (v0.17)

`POST /api/shutdown` põe `should_exit` no servidor uvicorn — por isso o comando
`web` passou a construir o `uvicorn.Server` à mão em vez de chamar
`uvicorn.run()`: sem a referência não há como sair limpo (no Windows, mandar
sinal para o próprio processo não encerra o uvicorn de forma confiável).

Duas salvaguardas: os trabalhos em andamento são **cancelados antes** (as
threads são `daemon`, sair no meio de um download deixaria `.part` para trás), e
o botão pede **dois cliques**. Quando o servidor não foi iniciado pelo comando
`web` — rodando o uvicorn direto, como nos testes — o endpoint devolve 501 e o
botão nem aparece, em vez de fingir que funcionou.

---

## 5j. Simetria entre as duas ferramentas (v0.18)

Um levantamento feature a feature (`renderJob` × `renderUpdateJob`,
`renderConflicts` × `renderUpdateReview`, listas, rotas) achou divergências que
não tinham motivo:

- **`/api/jobs/{id}/report` só servia conversões** — procurava `record_path`, e
  a atualização guarda em `report_path`. O relatório JSON da atualização existia
  em disco e não tinha como ser baixado. Agora o endpoint aceita os dois e o
  botão aparece nas duas ferramentas.
- **O hint da revisão** falava outra língua ("N escolhidos por você · M ficam de
  fora") e ainda avisava "o pack já foi gerado; aplicar de novo o regera". Passou
  a ter a mesma forma do hint dos conflitos: quantos resolvidos de quantos, e o
  que acontece com quem ficar sem escolha.
- **O alvo** (`#mc-pill` e `#ur-target`) usava texto corrido de um lado e do
  outro; agora os dois usam as mesmas tags de versão/loader do resto da
  interface.
- **As listas de saída** (conversões salvas, packs atualizados) usavam texto
  corrido; agora usam as mesmas tags, e a de atualização mostra
  `1.21.8 → 1.21.11` com o loader.

Divergência que **fica**, e por quê: depois de empacotar, os conflitos do
conversor somem (§4h) e a revisão do atualizador continua editável. Regenerar um
`.mrpack` é consulta pura; regenerar um `.zip` do CurseForge exige baixar os jars
de novo. São custos diferentes, então as affordances são diferentes.

## 5k. Estado do pack na lista (v0.18)

Três bugs no destaque, todos da mesma raiz — **a lista não era redesenhada**:

- o verde só aparecia no clique seguinte (nada chamava `renderPacks` quando o
  trabalho começava);
- sumia ao selecionar outro pack (`selectPack` mexia na classe à mão em vez de
  redesenhar);
- sumia quando a análise terminava, porque `packEmCurso` não contava
  `awaiting_conflicts`/`awaiting_review` — mas ali o trabalho não acabou, está
  esperando você.

E o falso **"Conversão cancelada"**: um job cancelado continua aberto até você
clicar em Fechar (é o que permite ler o log). Só que ao escolher outro pack o
painel seguia mostrando o cancelamento, como se fosse do pack novo. Agora
escolher um pack **descarta** um trabalho terminal que não produziu arquivo
nenhum — `done` fica, porque tem `.zip` para baixar.

---

## 5l. Cancelar deixou de assombrar (v0.19)

Três coisas mantinham o "Conversão cancelada" na tela depois que o usuário já
tinha seguido adiante:

1. o job cancelado continuava ocupando a vaga, então a próxima conversão levava
   **409** e o painel nem trocava. Agora o `btn-convert` **fecha o job morto
   antes** de pedir a nova conversão (e o mesmo no atualizador);
2. escolher outro pack já fechava, mas de forma assíncrona sem esperar — virou
   `await`;
3. o aviso era **neutro**, e neutro num painel de trabalho lê-se como "estado
   normal". Passou a laranja, como os outros avisos.

**Cancelar e excluir passaram a pedir dois cliques** (`armarBotao`, o mesmo do
Encerrar). São as ações que jogam trabalho fora, e um clique errado custava uma
análise inteira.

## 5m. A revisão esvazia quando o pack é gerado (v0.19)

Era a última assimetria grande: os conflitos do conversor somem depois de
empacotar (§4h), e a revisão do atualizador continuava listando tudo até você
fechar. Agora `updateFiles` devolve lista vazia quando `update.packaged` — as
duas abas se comportam igual.

Isso removeu junto o "aplicar de novo" da atualização pronta (`updateNeedsReapply`
foi apagado): com a revisão vazia não há o que reaplicar. É exatamente o que o
conversor faz, e a diferença de custo que justificava a exceção (§5j) não vale o
preço de duas ferramentas que se comportam diferente na mesma situação.

## 5n. Leitura das listas de saída (v0.19)

- **Verde no recém-gerado** (`.pack.fresh` + etiqueta "novo"), até você escolher
  outra coisa — é o que responde "onde foi parar o que eu acabei de fazer?".
- **Números com cor**: verde para atualizados, roxo para escolhidos por você,
  amarelo para os que ficaram de fora. O mesmo código de cor da aba de revisão.
- **Troca de loader visível** (`forge → fabric`), como já era a de Minecraft —
  o `/api/updates` passou a devolver `from_loader`.
- **Duas linhas de meta**, como a lista de entrada, e a contagem de mods virou
  tag (era o único texto solto no meio das tags).
- **As ações desceram para o rodapé do card** (baixar, adicionar ao input,
  excluir / gerar, excluir registro), no lugar onde a entrada tem o "Iniciar".
  Antes viviam no painel de detalhes: a coluna da esquerda tinha forma diferente
  conforme o lado.

---

## 5o. Configurações são um editor do `.env` (v0.20)

**Decisão:** a tela não introduz um segundo lugar onde a configuração mora. O
`.env` continua sendo a fonte da verdade — o painel lê dele ao abrir e escreve
nele ao salvar. Quem prefere editar o arquivo à mão continua podendo.

Isso impõe três invariantes, todos testados:

| invariante | por quê |
| --- | --- |
| comentários, ordem e chaves desconhecidas sobrevivem | o `.env` é do usuário, não nosso; ele pode ter `MINHA_VAR` lá |
| a chave da API nunca sai inteira do servidor | `mask()` devolve `••••••••9999` — o bastante para reconhecer, não para usar |
| valor vazio **comenta** a linha, não apaga | você vê que a configuração existiu e qual era o nome dela |

**Slider ou caixa de texto** não é escolha estética: sai do próprio campo. Quem
declara `minimum`/`maximum` vira slider **com caixa numérica ao lado** (o slider
para procurar, a caixa para acertar); caminho e segredo viram caixa de texto.
Assim acrescentar uma configuração nova é mexer numa lista só, em `settings.py`.

**Fechar sem querer.** O painel não fecha com alteração pendente — nem pelo
clique fora, nem pelo Esc, nem pelo próprio botão da engrenagem; ele avisa
"salve ou descarte". Sem nada pendente, qualquer um dos três fecha. Perder o que
foi digitado por um clique errado seria pior do que o incômodo de um clique a
mais.

**Restaurar padrão preserva a chave**, e há um botão separado para apagá-la —
as duas ações têm consequências muito diferentes para caberem no mesmo botão.
Ambas pedem dois cliques.

## 5p. Cards de lista com a mesma forma (v0.20)

Entrada e saída passaram a ter a mesma anatomia: **nome · tags · arquivo**. A
última linha (`linhaArquivo`) é literalmente a mesma função nos três, e a linha
de tags junta Minecraft, loader e contagem de mods.

Dois detalhes que só apareceram usando: as tags **se sobrepunham** ao quebrar
linha, porque `.pack .meta` não era flex e as pílulas têm padding vertical; e a
contagem de mods, cinza sobre fundo escuro, sumia no meio das tags coloridas —
virou magenta, a única faixa que nem o gradiente do Minecraft nem os loaders
usam.

---

## 5q. Correções das configurações (v0.21)

**O limiar de similaridade saiu da tela.** É o número que separa "o mod existe,
falta a versão" de "o mod não existe lá" (§4b), calibrado com dados medidos. Um
slider convida a mexer sem medir, e mexer nele muda o diagnóstico de *todo* mod.
Continua ajustável pelo `.env` à mão — quem for lá já sabe o que está fazendo.

**Apagar a chave virou rota própria.** Estava implementado como
`reset(keep_secrets=false)`, que limpa **tudo**: apagar a chave desfazia as
configurações recém-salvas. Agora são duas rotas com escopos diferentes
(`/reset` mantém o segredo, `/forget-key` mexe só nele), e o botão mudou de
lugar — fica ao lado do campo da chave, onde a ação pertence, não no rodapé
junto do "restaurar tudo".

**Revelar a chave fechava o painel.** O handler do olho redesenha os campos; o
botão clicado sai do DOM antes de o evento chegar ao `document`, e lá o
`closest(".settings-wrap")` devolve `null` — o listener de "clique fora"
concluía que o clique tinha sido fora. Um `stopPropagation()` resolve, e a
asserção que trava isso é **estática** (o DOM falso do teste não propaga
eventos, então exigir a chamada no código é o único jeito honesto de cobrir).

**Configurações travam com trabalho aberto.** Workers, timeout e limites de
página são lidos *enquanto* a conversão roda: trocá-los no meio daria um
resultado que não corresponde nem ao valor antigo nem ao novo. O backend recusa
com 409 e o `GET` devolve `locked_by`, para a interface desabilitar os campos em
vez de deixar você digitar para levar erro no fim.

---

## 5r. O `server.py` virou routers (v0.23)

**O problema.** `create_app()` tinha **858 linhas**: 47 rotas declaradas como
fechos dentro de uma função só, para capturar `input_path`, `output_path` e o
`JobManager`. Funcionava, mas era a decisão mais fora do padrão do repositório —
qualquer pessoa que já viu um projeto FastAPI espera `APIRouter` por assunto.

**A divisão.** Seis módulos em `web/routes/`, um por assunto (packs, jobs,
updates, records, catalog, system), mais três de apoio: `context.py` com o que
todos compartilham, `schemas.py` com os corpos de requisição e `payloads.py`
com os formatos que a tela consome — este último sem importar FastAPI, então dá
para testá-lo sem subir servidor. O `server.py` caiu para ~140 linhas: CSP,
middleware, estáticos e a inclusão dos routers.

**Cada router é uma fábrica** (`router(ctx) -> APIRouter`), e não um
`APIRouter` de módulo com `Depends`. O motivo é concreto: os testes criam várias
aplicações lado a lado apontando para pastas temporárias diferentes, e um router
global carregaria estado de uma para a outra. O contexto entra por parâmetro.

**O que a divisão revelou.** Três blocos repetidos que viraram método do
contexto: `input_pack()` (o mesmo "monta o caminho, 404 se não existe" em quatro
rotas), `require_free()` (o 409 de "já existe um trabalho aberto") e
`require_api_key()`. O `state = {"curseforge": None}` — um dicionário de uma
chave só, usado como célula mutável para o fecho — virou atributo do contexto.

**Como foi verificado.** Os corpos das rotas não foram redigitados: saíram do
arquivo antigo por fatiamento de linhas, com renomeação mecânica dos nomes
capturados. Depois, um script comparou o inventário de rotas (método + caminho)
com a lista tirada do monólito: **44 rotas, nenhuma sumiu, nenhuma duplicou**.
Os 153 testes e uma bateria de fumaça contra o servidor real fecham a conta.

---

## 5s. Nome em inglês, prosa em português (v0.23)

O repositório sempre teve uma convenção implícita: **identificadores em inglês,
comentários e textos de tela em português**. O `settings.py` era o único módulo
que a violava por inteiro (`Campo`, `gravar`, `estado`, `mascarar`), e por
tabela o `/api/settings` era o único endpoint com chaves JSON em português —
`{"chave": ..., "rotulo": ...}` ao lado de um `/api/state` que devolve
`{"name": ..., "size_mb": ...}`.

Agora `Field`, `write()`, `state()`, `mask()`, e o payload em inglês
(`key`, `label`, `help`, `type`, `value`, `default`, `minimum`, `maximum`,
`step`, `group`, `is_set`). **Duas exceções deliberadas**: os valores de `group`
("acesso", "pastas", "desempenho"…) continuam em português porque são o texto
do cabeçalho que aparece na tela; e o `app.js` continua com nomes em português,
que é a convenção dele — o que mudou foram as chaves da API que ele consome.

A troca foi feita sobre os *tokens* do Python, não com busca e substituição no
texto: trocar "valor" por "value" com regex teria estragado toda a prosa. Um
script comparou os 165 trechos de comentário e docstring antes e depois — só
mudaram os literais que são chave de JSON.

---

## 5t. O `.flake8` deixou de ser enfeite (v0.23)

O arquivo `.flake8` existia desde a v0.9, mas o flake8 **não estava instalado**:
ninguém conseguia rodá-lo, nem o editor. Em compensação, o `tools/check_all.py`
tinha uma varredura de colunas escrita à mão, que via só o comprimento das
linhas.

Agora o flake8 é dependência de desenvolvimento e entrou na bateria; a varredura
artesanal saiu. O ganho não é estético: na primeira execução ele achou um
`W292` que a varredura não via, e as duas mutações de teste (`import` sem uso,
variável sem uso) provam que ele pega o que ninguém pegava.

---

## 5. Não usamos fingerprint (murmur2) do CurseForge

**Decisão:** rejeitado, apesar de ser o método mais preciso que existe.

**Por quê:** `POST /v1/fingerprints` exige calcular o hash murmur2 do **conteúdo** do
jar, ou seja, baixar todos os mods do pack (centenas de MB) antes de saber se eles
existem no CurseForge. O ganho não compensa: a comparação por nome de arquivo já
resolve ~92% do pack de teste em segundos.

---

## 6. Quantas chamadas gastar por mod

**Decisão:** limites com teto, configuráveis por variável de ambiente:

- busca: 3 páginas × 50 resultados (`M2CF_SEARCH_PAGES`);
- `latestFiles` (que já vem de graça na resposta da busca) é varrido **primeiro**,
  para todos os candidatos;
- listagem de arquivos: só para os 8 melhores candidatos (`M2CF_MAX_CANDIDATES`),
  filtrada pela versão do Minecraft do pack;
- histórico **completo** de arquivos (até 20 páginas) apenas para os 3 melhores
  candidatos — alguns projetos têm milhares de arquivos.

**Por quê:** sem teto, um único mod não encontrado poderia gastar centenas de
requisições. Com esses limites o pack de teste converte em ~20s na primeira execução.

---

## 7. Arquivos que não são mods

**Decisão:** tudo que está no índice do `.mrpack` fora de `mods/`
(`resourcepacks/`, `shaderpacks/`, `datapacks/`, ...) é **baixado e colocado em
`overrides/`** no caminho original, sem tentar match no CurseForge.

**Por quê:** o formato do CurseForge só referencia projetos por `projectID/fileID`
de mods; resource/shader packs raramente têm equivalente e o `overrides/` resolve
100% dos casos, sem risco. A versão anterior simplesmente descartava esses arquivos.

---

## 8. `overrides/` e `client-overrides/` do mrpack

**Decisão:** ambos são extraídos e **mesclados** em `overrides/` no zip final
(com proteção contra *zip-slip*).

**Por quê:** o CurseForge não tem separação client/server no manifest; mesclar
preserva configs, mundos e teclas. A versão anterior nem copiava esses arquivos.

---

## 9. Manifest gerado

**Decisão:**

- `required: true` para todos os mods encontrados;
- `author: ""` (o `.mrpack` não carrega autor);
- `modlist.html` é gerado junto (o CurseForge App exibe essa lista);
- entradas duplicadas de `(projectID, fileID)` são removidas.

**Por quê:** manifest mínimo, válido e sem inventar metadado que não existe na origem.

---

## 10. Downloads

**Decisão:** downloads em paralelo (mesmo pool de threads), com retry exponencial
e **verificação de SHA1** quando o mrpack informa o hash. Arquivo já baixado e com
hash correto não é baixado de novo.

**Por quê:** integridade e reexecução barata.

---

## 11. Cache (SQLite, com payloads enxutos)

**Decisão:** cache persistente em `.cache/curseforge.sqlite3`, com namespaces
(`search`, `files`, `mod`, `mr_version`, `mr_project`, `mr_recent`).
`--no-cache` ignora, `clear-cache` apaga (inclusive o formato antigo).

**Por quê:** a segunda execução do mesmo pack cai de ~17s para ~8s, e reconverter
depois de ajustar algo não gasta cota de API.

**Por que saiu do JSON único** (v0.6.1): aquele formato precisava ler e reescrever
o arquivo **inteiro** a cada gravação. Com dois modpacks convertidos ele chegou a
**46 MB**, o que custava 0,6 s para abrir e 0,4 s + 46 MB de escrita **por flush** —
e a interface web abre um cache por requisição. Com SQLite (WAL,
`synchronous=NORMAL`), ler e gravar uma chave é O(1).

**E por que ele estava tão grande:** guardávamos a resposta crua da API, com
descrição, screenshots, categorias, hashes, dependências e módulos de cada arquivo —
nada disso é usado. Agora tudo passa por `slim_project`/`slim_file` antes de entrar
no cache (e antes de circular pelo código, o que também reduz a memória).

Medido, mesmo conteúdo: **46 MB → 6,1 MB**. Reanalisar o Prominence II (406 mods)
com cache quente caiu para **~1 s**.

Falhar aqui nunca derruba a conversão: erro de SQLite **ou de sistema de arquivos**
desliga o cache e a conversão segue (um teste cobre exatamente isso — foi ele que
achou o `OSError` que escapava do `except sqlite3.Error`).

---

## 12. Paralelismo

**Decisão:** `ThreadPoolExecutor` com 6 workers (`M2CF_WORKERS`), em vez de asyncio.

**Por quê:** o gargalo é I/O de rede; threads dão o mesmo ganho com um código bem
mais simples, e `httpx.Client` é thread-safe. Falha em um mod nunca derruba a
conversão inteira — o mod vira `override` e o motivo entra no relatório.

---

## 13. Encoding do console (bug real encontrado)

**Decisão:** `sys.stdout`/`sys.stderr` são reconfigurados para UTF-8 (`errors="replace"`)
e o log do matcher é blindado por `try/except`.

**Por quê:** vários projetos do CurseForge têm emoji no nome (ex.: **"Jade 🔍"**). No
console do Windows (cp1252) isso levantava `UnicodeEncodeError` **dentro da thread de
match**, e o mod era descartado para `overrides` por um problema de *impressão*. Com a
correção, o pack de teste subiu de 42 para 45 mods convertidos.

---

## 14. Sem lista manual de aliases

**Decisão:** não existe (e não deve existir) um mapa fixo tipo
`"fabric-api" -> projectID 306612`.

**Por quê:** o nome real vem da API do Modrinth e a confirmação é por arquivo. O
tratamento especial que existia para Fabric API foi removido — ele podia devolver
o arquivo **errado** (pegava sempre `latestFiles[0]`, de qualquer versão).

---

## 15. Limpeza feita no repositório

- `src/mrpack2curseforge/models.py` — removido (era cópia de `domain.py`).
- `src/tests/*.py` (`teste.py`, `teste_api_key.py`, `test_env.py`, `test_mods.py`) —
  removidos; viraram testes de verdade em `tests/` (pytest, sem rede).
- `output/` (resultado antigo de execução) — removido; a saída agora é `output_modpacks/`.
- `.env` movido de `src/mrpack2curseforge/.env` para a **raiz** do projeto
  (`config.py` procura em cwd, raiz e pacote, nessa ordem).
- `src/tests/teste_1.mrpack` movido para `input_modpacks/teste_1.mrpack`.

> ⚠️ **Atenção:** o arquivo removido `src/tests/teste_api_key.py` tinha uma chave da
> API do CurseForge escrita direto no código (a mesma do `.env`). Se esse repositório
> já esteve em algum lugar público, **rotacione a chave** em
> <https://console.curseforge.com/>.

---

## 16. Limitações conhecidas (aceitas)

- Mods que não estão no CurseForge, ou cuja versão exata não foi publicada lá,
  sempre vão para `overrides` — é o comportamento correto, não uma falha. O
  relatório diz qual dos dois casos é (§4b).
- Se o CurseForge publicou o mesmo mod com um nome de arquivo diferente do Modrinth,
  o mod vai para `overrides` (conservador por escolha). O diagnóstico costuma
  detectar esse caso e marcá-lo como `version-unavailable`.
- O diagnóstico usa o **nome** do projeto candidato; um repost/fork com nome quase
  igual pode ser apontado como "o projeto no CurseForge". Como o diagnóstico não
  altera o manifest, o risco é apenas de rótulo no relatório.
- Nomes de arquivo dentro do `overrides/` do mrpack original com codificação exótica
  podem ser decodificados como cp437 pelo `zipfile` (limitação do formato ZIP).
- O filtro por loader existe na API, mas não é aplicado na busca: alguns projetos
  multi-loader ficavam de fora. A confirmação por nome de arquivo já cobre esse risco.
