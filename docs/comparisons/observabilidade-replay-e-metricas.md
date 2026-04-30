# Observabilidade: Replay Estruturado e Métricas Agregadas

> Análise focada de um sub-critério da observabilidade onde Aru tem pontuação mais baixa (6.5) na comparação técnica com Claude Code e OpenCode. Documenta o que cada uma das três ferramentas tem hoje, o gap específico de Aru, e o caminho pra fechar.

## O que são essas duas coisas

São duas capacidades distintas que entram juntas no critério **observabilidade**:

### 1. Replay estruturado

**Conceito:** salvar cada passo da execução do agente (mensagem, tool call, resposta, erro, decisão) em formato bem definido e **reabrir depois** pra inspecionar passo-a-passo. Não é só "guardar o histórico do chat" — é poder responder perguntas como:

- *"No turn 5, qual era exatamente o estado do contexto que o agente viu antes de decidir chamar `apply_patch`?"*
- *"Esse turn falhou. Re-executa só ele com o mesmo input e me mostra onde divergiu."*
- *"Mostra o diff de `MessageHistory` entre antes e depois da compactação."*
- *"Qual subagente foi spawnado por qual e em que ordem?"*

Tem três níveis típicos:

| Nível | O que faz |
|---|---|
| **Trace plano** | Tudo joga num `.log` ou `.jsonl`. Você pode `grep`/`jq` mas não há semântica. |
| **Trace estruturado** | Eventos tipados (com schema), referências cruzadas entre eles (ex.: `tool_id` linkando call/result). Permite navegar por filtros. |
| **Replay executável** | Re-roda passos com snapshot de estado, permite branch ("e se eu mudar isso aqui no turn 5, o que acontece?"). |

### 2. Métricas agregadas

**Conceito:** agregar os dados de muitas sessões em uma visão sumária. Não é "quanto custou esta sessão" (isso aparece no status bar) — é:

- *"Custo por dia nos últimos 30 dias"*
- *"Top 10 ferramentas mais usadas, e qual tem maior taxa de erro"*
- *"Tokens médios e medianos por sessão"*
- *"Quanto cada modelo (Sonnet, Qwen, GLM) consumiu individualmente"*
- *"Latência p95 do `Bash` vs `apply_patch`"*

## O que cada uma tem hoje

### OpenCode — tem ambos, simples e funcional

**Replay/debug**: comandos dedicados (`packages/opencode/src/cli/cmd/`):
- `stats` — agregação completa
- `debug/snapshot.ts` — `track`, `patch <hash>`, `diff <hash>` permitem inspecionar snapshots de filesystem por hash
- `debug/agent.ts`, `debug/lsp.ts`, `debug/ripgrep.ts`, `debug/skill.ts` — debug por subsistema
- `export`/`import` — sessão inteira como artefato portável
- Sessões persistidas em **SQLite** (`Database.use((db) => db.select().from(SessionTable).all())`), então você pode escrever queries SQL ad-hoc

**Stats agregadas** (`cli/cmd/stats.ts`, ~410 linhas):

```
┌──── OVERVIEW ────┐    ┌──── COST & TOKENS ────┐    ┌──── TOOL USAGE ────┐
│ Sessions   142   │    │ Total Cost  $47.32     │    │ read    ████   42% │
│ Messages   2,847 │    │ Avg/Day     $1.58      │    │ bash    ███    28% │
│ Days       30    │    │ Median tok  18.2K      │    │ edit    ██     19% │
└──────────────────┘    └────────────────────────┘    └────────────────────┘
```

Filtros por `--days N`, `--project`, `--models`, `--tools`. Roda em batches de 20 sessões em paralelo pra ser rápido.

### Claude Code — vai mais longe

`src/services/analytics/` tem **stack inteira de observabilidade**:
- `firstPartyEventLogger.ts` + `firstPartyEventLoggingExporter.ts` — eventos tipados exportados
- `datadog.ts` — integração nativa com Datadog
- `sink.ts` + `sinkKillswitch.ts` — interface de sinks plugável com kill switch via GrowthBook
- `growthbook.ts` — feature flags pra rolouts graduais
- `diagnosticTracking.ts` — diagnósticos do app

Mais `services/compact/` tem 11 arquivos só pra eventos de compactação (que tipo de compaction rodou, quantos tokens economizados, qual hook disparou, etc.). Tudo exportável.

### Aru — tem a fundação, mas falta a casa

A **fundação está sólida** — `aru/events.py` tem **17 eventos Pydantic tipados** com schema validado:
- `TurnEndEvent` carrega `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `duration_ms`
- `ToolCompletedEvent` tem `tool_name`, `duration_ms`, `error`
- `SubagentCompleteEvent` tem `status` (ok/error/cancelled), tokens, `duration_ms`
- `MetricsUpdatedEvent` mantém cumulativos da sessão

Isso é melhor que muita ferramenta — a tipagem é mais limpa que a do OpenCode (que usa Zod inline).

**O que falta:**

| Falta | Detalhe |
|---|---|
| Sink de disco estruturado | Eventos saem pelo bus pra plugins; não há um `JsonlSink` built-in escrevendo `~/.aru/traces/<session-id>.jsonl` por padrão. O único trace persistido é `trace.json` por subagente em `aru/tools/delegate.py:143`. |
| Comando `aru stats` | Os eventos têm os números (tokens, duration, error), mas nada agrega depois. Não há `aru stats --days 30 --tools` equivalente. |
| Comando `aru debug snapshot` | Não há equivalente ao `debug/snapshot.ts` da OpenCode pra inspecionar checkpoints (apesar de Aru ter checkpoints em `aru/checkpoints.py`). |
| Replay walker | Sessões salvam em disco mas não há comando "abre essa sessão e me deixa navegar turn-by-turn vendo o que o LLM viu". |
| Per-tool aggregates | `ToolCompletedEvent` tem `error`. Mas em lugar nenhum você consegue ver "BashTool falhou em 12% dos calls esta semana". |

## Por que isso importa

O motivo do peso disso ser 5% e não 15% é que **na maioria das sessões você não precisa**. Você abre, conversa, fecha. Mas três cenários onde dói faltar:

1. **Otimizar custos** — quando você paga via API e está usando muito, querer saber *qual ferramenta está estourando seu budget* sem instalar Datadog é razoável.
2. **Debugar comportamento estranho** — "ontem o agente entrou em loop, hoje não". Sem replay estruturado, você só tem o chat scrollback, que perde o estado interno.
3. **Iterar no design da própria ferramenta** — pra quem constrói Aru, saber "qual tool tem maior tempo médio? maior taxa de erro? em quais sessões `apply_patch` falha?" é o que guia decisões de melhoria. Hoje, esse loop não está fechado.

## Custo de fechar o gap

Aru já tem 80% do trabalho feito (`events.py` é forte). Faltariam coisas pequenas e bem delimitadas:

1. Um **`JsonlSink` plugin** built-in escrevendo `~/.aru/traces/<session>.jsonl` (~50 linhas) — escuta o bus, serializa o `model_dump()` de cada evento, escreve linha por linha.
2. Comando **`aru stats`** (~200 linhas) — lê o jsonl ou SQLite (se você tiver), agrupa por dia/tool/model, formata como o OpenCode faz.
3. Comando **`aru replay <session-id>`** (~300 linhas) — TUI minimal navegando turns: setas pra mover, Enter pra expandir tool calls, mostra estado de contexto antes/depois.
4. Persistência em SQLite em vez de jsonl puro — abre porta pra queries ad-hoc.

Provavelmente uma semana de trabalho focado pra subir aquele 6.5 pra ~8.0 sem grande risco arquitetural — porque os eventos já estão bem tipados, é só dar pernas pros consumidores.

## Referências

- `aru/events.py` — schemas Pydantic dos 17 eventos tipados (fundação existente)
- `aru/tools/delegate.py:143` — único `trace.json` persistido hoje (per-subagent)
- `aru/checkpoints.py` — checkpoints existem mas não têm comando de inspeção
- OpenCode `packages/opencode/src/cli/cmd/stats.ts` — referência pra comando agregado
- OpenCode `packages/opencode/src/cli/cmd/debug/snapshot.ts` — referência pra debug/inspect
- Claude Code `src/services/analytics/` — referência pra sink plugável + integração externa
