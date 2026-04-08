# Analise de Otimizacao de Tokens do OpenCode

> Documento de referencia para aprimorar o projeto **Aru** com base nas estrategias de baixo consumo de tokens do **OpenCode**.

---

## 1. Visao Geral da Arquitetura

O OpenCode e um agente de codigo open-source (CLI/TUI) que suporta multiplos provedores LLM. Seu consumo baixo de tokens resulta de **7 pilares fundamentais** que operam em camadas complementares:

1. Truncamento agressivo em multiplas camadas
2. Sistema de compactacao/sumarizacao inteligente
3. Poda seletiva de resultados de ferramentas antigas
4. Prompt caching nativo por provedor
5. Prompts de sistema otimizados para brevidade
6. Delegacao de trabalho para sub-agentes
7. Diffs em vez de conteudo completo

---

## 2. Truncamento em Multiplas Camadas

### 2.1 Camada Global de Truncamento

**Arquivo**: `packages/opencode/src/tool/truncate.ts`

Toda saida de ferramenta passa por um servico centralizado de truncamento com limites rigidos:

```
MAX_LINES = 2000 linhas
MAX_BYTES = 50 KB (50 * 1024)
```

**Algoritmo**:
- Se a saida cabe nos limites -> retorna sem modificacao
- Se excede -> trunca direcionalmente (`head` ou `tail`)
- O conteudo completo e salvo em disco (`TRUNCATION_DIR`)
- Retorna preview + caminho do arquivo + hint para o LLM usar Grep/Read com offset

**Ponto-chave para Aru**: O hint instrui o LLM a **nao ler o arquivo inteiro**, mas sim usar ferramentas de busca com offset/limit. Isso evita que o LLM peca re-leitura do conteudo completo.

```typescript
// Se o agente tem acesso ao Task tool, sugere delegacao:
"Use the Task tool to have explore agent process this file"
// Senao, sugere ferramentas pontuais:
"Use Grep to search the full content or Read with offset/limit"
```

### 2.2 Truncamento na Ferramenta Read

**Arquivo**: `packages/opencode/src/tool/read.ts`

Multiplas camadas de protecao:

| Limite | Valor | Proposito |
|--------|-------|-----------|
| `DEFAULT_READ_LIMIT` | 2000 linhas | Maximo de linhas por leitura |
| `MAX_LINE_LENGTH` | 2000 chars | Trunca linhas individuais longas |
| `MAX_BYTES` | 50 KB | Limite absoluto de bytes |

**Comportamento critico**: Linhas individuais maiores que 2000 chars sao cortadas com sufixo `... (line truncated to 2000 chars)`. Isso previne que arquivos minificados (JS/CSS) ou logs com linhas enormes consumam tokens.

**Metadados retornados**: O output inclui `next offset` para continuar leitura e indica se o corte foi por bytes ou linhas - permitindo ao LLM fazer leituras incrementais inteligentes.

### 2.3 Truncamento na Ferramenta Grep

**Arquivo**: `packages/opencode/src/tool/grep.ts`

- Maximo de **100 matches** retornados
- Cada linha de match truncada em **2000 chars**
- Resultados ordenados por data de modificacao (mais recentes primeiro)
- Mensagem clara de quantos resultados foram omitidos

### 2.4 Licao para Aru

O Aru ja tem truncamento (500 linhas / 20KB), mas o OpenCode adiciona:
- **Truncamento por linha individual** (previne linhas enormes)
- **Hints contextuais** que ensinam o LLM a usar ferramentas incrementais
- **Salvamento em disco** do conteudo completo para acesso posterior
- **Truncamento direcional** (head vs tail) conforme o tipo de conteudo

---

## 3. Sistema de Compactacao (Context Compaction)

### 3.1 Deteccao de Overflow

**Arquivo**: `packages/opencode/src/session/overflow.ts`

Formula precisa para detectar quando compactar:

```
usable = model.limit.input - reserved
       OU
usable = model.limit.context - maxOutputTokens

overflow = total_tokens >= usable
```

Onde:
- `reserved` = config customizavel OU `min(20_000, maxOutputTokens)`
- `total_tokens` = input + output + cache.read + cache.write

**Ponto-chave**: O buffer reservado (`COMPACTION_BUFFER = 20_000`) garante que a compactacao aconteca **antes** de atingir o limite real, deixando espaco para o proprio processo de sumarizacao.

### 3.2 Processo de Compactacao

**Arquivo**: `packages/opencode/src/session/compaction.ts`

Quando o overflow e detectado:

1. **Localiza a ultima mensagem de usuario nao-compactada**
2. **Clona o historico** e aplica `stripMedia: true` (remove todas as midias)
3. **Converte midias em placeholders** de texto: `[Attached image/png: screenshot.png]`
4. **Envia para um agente "compaction"** dedicado com prompt estruturado
5. **O resumo segue template rigido**:

```markdown
## Goal
[O que o usuario esta tentando fazer]

## Instructions
[Instrucoes importantes do usuario]

## Discoveries
[Aprendizados notaveis durante a conversa]

## Accomplished
[O que foi feito, o que esta em progresso, o que falta]

## Relevant files / directories
[Lista estruturada de arquivos relevantes]
```

6. **Se a compactacao falha** (contexto grande demais ate para sumarizar) -> retorna erro
7. **Se sucede** -> re-envia a ultima mensagem do usuario para continuidade

### 3.3 Replay de Mensagem

Apos compactacao, o OpenCode **re-injeta a ultima pergunta do usuario** como nova mensagem, garantindo que o LLM continue exatamente de onde parou. Midias na mensagem re-injetada sao convertidas em placeholders de texto.

Se nao ha mensagem para replay, injeta: *"Continue if you have next steps, or stop and ask for clarification."*

### 3.4 Licao para Aru

O Aru ja tem compactacao, mas o OpenCode adiciona:
- **Template estruturado** com secoes fixas (Goal, Instructions, Discoveries, Accomplished, Files)
- **Agente dedicado** para compactacao (pode usar modelo diferente/mais barato)
- **Replay automatico** da ultima mensagem do usuario
- **Stripping de midia** antes da sumarizacao
- **Plugin hooks** para customizar o processo de compactacao

---

## 4. Poda Seletiva de Tool Outputs (Pruning)

### 4.1 Algoritmo de Poda

**Arquivo**: `packages/opencode/src/session/compaction.ts`, linhas 91-139

Este e um dos mecanismos **mais impactantes** e diferenciados do OpenCode. Opera **independentemente** da compactacao:

```
PRUNE_MINIMUM = 20_000 tokens  (minimo para acionar poda)
PRUNE_PROTECT = 40_000 tokens  (janela de protecao recente)
PRUNE_PROTECTED_TOOLS = ["skill"]  (ferramentas nunca podadas)
```

**Algoritmo passo a passo**:

1. Percorre mensagens de tras para frente
2. Pula os 2 primeiros turnos de usuario (protege contexto recente)
3. Para em mensagens que ja tem summary (checkpoint de compactacao anterior)
4. Para cada tool output completado:
   - Se a ferramenta e "skill" -> nunca poda (protegida)
   - Se ja tem `compacted` timestamp -> para (ja processado)
   - Estima tokens do output
   - Acumula total
   - Se total > `PRUNE_PROTECT` (40K) -> marca para poda
5. So persiste a poda se o total podado > `PRUNE_MINIMUM` (20K)
6. Marca cada parte podada com `compacted = Date.now()`

### 4.2 Como Outputs Podados Sao Tratados

Quando a mensagem e convertida para envio ao LLM (`message-v2.ts`):

```typescript
// Tool outputs com timestamp compacted sao substituidos por:
"[Old tool result content cleared]"
```

Isso significa que o LLM **sabe** que houve um resultado mas nao ocupa tokens com o conteudo antigo.

### 4.3 Licao para Aru

O Aru ja tem pruning, mas o OpenCode adiciona:
- **Ferramentas protegidas** que nunca sao podadas (ex: "skill")
- **Protecao por turnos** (ultimos 2 turnos sempre preservados, nao apenas por tokens)
- **Checkpoint awareness**: para de podar ao encontrar summaries anteriores
- **Threshold duplo**: so poda se tem material suficiente (>20K) E ultrapassa a janela de protecao (>40K)

---

## 5. Prompt Caching por Provedor

### 5.1 Estrategia de Cache

**Arquivo**: `packages/opencode/src/provider/transform.ts`, linhas 192-238

O OpenCode aplica cache markers em posicoes estrategicas:

```
Cached: primeiras 2 mensagens system + ultimas 2 mensagens da conversa
```

**Cache por provedor**:

| Provedor | Mecanismo |
|----------|-----------|
| Anthropic | `cacheControl: { type: "ephemeral" }` |
| OpenRouter | `cacheControl: { type: "ephemeral" }` |
| Bedrock | `cachePoint: { type: "default" }` |
| OpenAI-compat | `cache_control: { type: "ephemeral" }` |
| Copilot | `copilot_cache_control: { type: "ephemeral" }` |

**Session-level cache keys**: Para OpenAI/OpenRouter/Venice, usa `sessionID` como `promptCacheKey`, garantindo que o cache persista durante toda a sessao.

### 5.2 Otimizacao da Estrutura de Sistema para Cache

O OpenCode mantem propositalmente uma **estrutura de 2 partes** para system prompts:
- Parte 1 (header): Prompt base do agente (estavel entre mensagens)
- Parte 2: Contexto dinamico (instrucoes do usuario, plugins)

Se plugins nao alteram o header, as partes sao **re-unidas** para manter a estrutura de 2 blocos ideais para cache.

### 5.3 Licao para Aru

- Implementar cache markers especificos por provedor
- Estruturar system prompts em 2 blocos (estavel + dinamico) para maximizar cache hits
- Usar session ID como cache key para manter cache durante toda a conversa

---

## 6. Prompts de Sistema Otimizados para Brevidade

### 6.1 Diretivas de Concisao

**Arquivo**: `packages/opencode/src/session/prompt/default.txt`

O prompt do OpenCode contem **7 diretivas explicitas de brevidade**:

1. *"minimize output tokens as much as possible"*
2. *"fewer than 4 lines"* (exceto tool use e geracao de codigo)
3. *"should NOT answer with unnecessary preamble or postamble"*
4. *"One word answers are best"*
5. *"Avoid introductions, conclusions, and explanations"*
6. *"Do not add additional code explanation summary unless requested"*
7. *"Only address the specific query or task at hand"*

### 6.2 Exemplos In-Prompt

O prompt inclui **exemplos concretos** de respostas curtas:

```
user: 2 + 2
assistant: 4

user: is 11 a prime number?
assistant: Yes

user: what command should I run to list files?
assistant: ls
```

Estes exemplos funcionam como **few-shot learning** que calibra o LLM para respostas minimas.

### 6.3 Prompts Especificos por Modelo

O OpenCode tem prompts diferentes por familia de modelo:
- `anthropic.txt` - Otimizado para Claude (105 linhas)
- `gpt.txt` - Para OpenAI (107 linhas)
- `gemini.txt` - Para Google Gemini
- `beast.txt` - Para modelos de raciocinio pesado (o1/o3)
- `default.txt` - Fallback generico

Cada prompt e calibrado para as tendencias do modelo (ex: Claude tende a ser verboso, entao recebe mais diretivas de brevidade).

### 6.4 Licao para Aru

- Adicionar diretivas **explicitas e repetidas** de brevidade no system prompt
- Incluir **exemplos few-shot** de respostas curtas ideais
- Criar **prompts especificos por modelo** (Claude vs GPT vs Gemini tem verbosidades diferentes)
- A diretiva mais eficaz: *"fewer than 4 lines unless asked for detail"*

---

## 7. Delegacao para Sub-Agentes

### 7.1 Arquitetura de Agentes

**Arquivo**: `packages/opencode/src/agent/agent.ts`

O OpenCode tem agentes especializados com **escopos de ferramentas restritos**:

| Agente | Modo | Ferramentas | Proposito |
|--------|------|-------------|-----------|
| `build` | primary | Todas | Agente principal |
| `explore` | subagent | grep, glob, read, webfetch | Exploracao read-only |
| `plan` | primary | Todas exceto edit/write | Planejamento |
| `compaction` | hidden | Nenhuma | Sumarizacao |
| `general` | subagent | Todas | Trabalho paralelo |

### 7.2 Como Isso Economiza Tokens

O prompt do sistema instrui repetidamente:

```
"When doing file search, prefer to use the Task tool to reduce context usage"
"Use the Task tool with specialized agents when the task matches the agent's description"
```

Quando o agente principal delega uma busca para o `explore` agent:
1. O sub-agente recebe **seu proprio contexto limpo** (sem historico da conversa principal)
2. O sub-agente faz multiplas buscas e leituras
3. Retorna apenas o **resultado sintetizado** ao agente principal
4. Todo o conteudo intermediario (arquivos lidos, greps executados) **nunca entra no contexto principal**

### 7.3 Truncamento de Sub-Agente

Quando o truncamento detecta que o agente tem acesso ao Task tool:

```
"Use the Task tool to have explore agent process this file.
 Do NOT read the full file yourself - delegate to save context."
```

Isso cria um **ciclo virtuoso**: outputs grandes sao truncados -> hint diz para delegar -> delegacao mantem contexto limpo.

### 7.4 Licao para Aru

O Aru ja tem agentes (Planner, Executor, General), mas o OpenCode adiciona:
- **Agente `explore` dedicado** com escopo read-only para buscas
- **Hints de delegacao** nos truncamentos que instruem o LLM a usar sub-agentes
- **Isolamento de contexto**: cada sub-agente tem contexto limpo
- **Ferramentas restritas por agente**: menos ferramentas = schemas menores no contexto

---

## 8. Diffs em Vez de Conteudo Completo

### 8.1 Ferramenta Edit

**Arquivo**: `packages/opencode/src/tool/edit.ts`

A ferramenta de edicao usa `createTwoFilesPatch()` para gerar diffs unificados. O output para o LLM mostra apenas as linhas alteradas, nao o arquivo inteiro.

### 8.2 Snapshots com Patches

**Arquivo**: `packages/opencode/src/session/message-v2.ts`

Cada mensagem pode ter `PatchPart` com:
- `hash`: referencia git
- `files`: lista de arquivos modificados
- `diffs`: array de `FileDiff` objects

Isso permite rastrear mudancas sem armazenar conteudo completo de arquivos.

### 8.3 Licao para Aru

- Retornar **diffs unificados** ao LLM apos edicoes (nao o arquivo completo)
- Usar **patches** para rastreamento de mudancas entre mensagens

---

## 9. Controle de Output Tokens

### 9.1 Limite de Output

**Arquivo**: `packages/opencode/src/provider/transform.ts`

```typescript
OUTPUT_TOKEN_MAX = 32_000  // Hard cap padrao
maxOutputTokens = Math.min(model.limit.output, OUTPUT_TOKEN_MAX)
```

Isso previne que o LLM gere respostas absurdamente longas.

### 9.2 Modelos Menores para Operacoes Rapidas

Para operacoes auxiliares (geracao de titulo, sumarizacao):
- OpenAI: `reasoningEffort: "low"` ou `"minimal"`
- Google: `thinkingLevel: "minimal"` ou `thinkingBudget: 0`
- Bedrock: Desabilita reasoning

### 9.3 Licao para Aru

- Implementar **hard cap** de output tokens (32K e um bom padrao)
- Usar **modelos menores/mais baratos** ou **reasoning reduzido** para operacoes auxiliares como titulos e sumarizacoes

---

## 10. Estimativa Rapida de Tokens

### 10.1 Heuristica

**Arquivo**: `packages/opencode/src/util/token.ts`

```typescript
const CHARS_PER_TOKEN = 4
export function estimate(input: string) {
  return Math.max(0, Math.round((input || "").length / CHARS_PER_TOKEN))
}
```

O OpenCode **nao usa tokenizer real** para decisoes de gerenciamento de contexto. A heuristica de 4 chars/token e suficiente para:
- Decidir quando podar
- Decidir quando compactar
- Estimar tamanho de tool outputs

Isso evita dependencia de tokenizers especificos por modelo e e extremamente rapido.

### 10.2 Licao para Aru

- Usar heuristica simples (4 chars/token) para decisoes de gerenciamento, nao tokenizer real
- Reservar tokenizacao precisa apenas para billing/metricas se necessario

---

## 11. Stripping de Midia

### 11.1 Quando Midias Sao Removidas

| Momento | Acao |
|---------|------|
| Compactacao | Todas as midias removidas (`stripMedia: true`) |
| Replay de mensagem | Midias convertidas em `[Attached mime: filename]` |
| Provedor sem suporte | Midias extraidas para mensagem separada ou erro |
| Poda de tool outputs | Attachments removidos junto com output |

### 11.2 Licao para Aru

- Implementar **stripping agressivo de midia** durante compactacao
- Converter midias em placeholders textuais minimos
- Verificar suporte de midia por provedor antes de enviar

---

## 12. Filtragem de Mensagens Compactadas

### 12.1 filterCompacted()

**Arquivo**: `packages/opencode/src/session/message-v2.ts`

Ao construir o historico de mensagens para envio ao LLM:

1. Percorre mensagens de tras para frente
2. Para no ultimo `summary` completado (checkpoint de compactacao)
3. Retorna apenas mensagens **apos** o checkpoint

Isso garante que mensagens antigas ja sumarizadas **nunca** sao re-enviadas ao LLM.

### 12.2 Licao para Aru

- Implementar filtragem que **exclui** mensagens anteriores ao ultimo checkpoint de compactacao
- O summary substitui completamente o historico anterior

---

## 13. Transformacoes de Mensagem por Provedor

### 13.1 Otimizacoes Especificas

**Arquivo**: `packages/opencode/src/provider/transform.ts`

| Transformacao | Economia |
|---------------|----------|
| Remover mensagens com conteudo vazio | Elimina mensagens fantasma |
| Sanitizar tool IDs (Mistral: 9 chars) | Reduz overhead de metadados |
| Remover reasoning duplicado | Evita contar reasoning 2x |
| Filtrar modalidades nao suportadas | Previne envio inutil de midia |

### 13.2 Licao para Aru

- Aplicar transformacoes **pre-envio** especificas por provedor
- Remover conteudo vazio, metadados desnecessarios e duplicacoes

---

## 14. Resumo: Mapa de Implementacao para Aru

### Prioridade Alta (Maior Impacto)

| # | Estrategia | Status Aru | Acao |
|---|-----------|------------|------|
| 1 | Hints de delegacao nos truncamentos | Nao tem | Implementar hints que instruem LLM a delegar |
| 2 | Diretivas de brevidade no prompt | Parcial | Adicionar 7 diretivas + few-shot examples |
| 3 | Prompt caching por provedor | Nao tem | Implementar cache markers (system + ultimas 2 msgs) |
| 4 | Agente explore read-only | Nao tem | Criar agente dedicado para buscas |
| 5 | Diffs no output de Edit | Verificar | Retornar unified diff, nao arquivo completo |

### Prioridade Media

| # | Estrategia | Status Aru | Acao |
|---|-----------|------------|------|
| 6 | Truncamento por linha individual | Nao tem | Adicionar MAX_LINE_LENGTH = 2000 |
| 7 | Template estruturado de compactacao | Parcial | Adotar template com 5 secoes fixas |
| 8 | Replay de mensagem apos compactacao | Nao tem | Re-injetar ultima pergunta do usuario |
| 9 | Ferramentas protegidas na poda | Nao tem | Marcar ferramentas criticas como nao-podaveis |
| 10 | Prompts por modelo | Nao tem | Criar variantes para Claude/GPT/Gemini |

### Prioridade Baixa

| # | Estrategia | Status Aru | Acao |
|---|-----------|------------|------|
| 11 | Salvamento em disco de outputs truncados | Nao tem | Salvar full output + retornar path |
| 12 | Reasoning reduzido para ops auxiliares | Nao tem | Usar reasoning low/minimal para titulos |
| 13 | Hard cap de output tokens | Verificar | Implementar 32K cap |
| 14 | Estimativa 4 chars/token | Verificar | Simplificar se usando tokenizer pesado |
| 15 | Stripping de midia em compactacao | Verificar | Converter midias em placeholders |

---

## 15. Metricas de Referencia

O rastreamento de tokens do OpenCode por mensagem inclui:

```
input     - Tokens de entrada
output    - Tokens de saida
reasoning - Tokens de raciocinio
cache.read  - Tokens lidos do cache (economia real)
cache.write - Tokens escritos no cache
```

Recomendacao para Aru: implementar rastreamento similar para **medir o impacto** de cada otimizacao implementada, especialmente `cache.read` que indica economia real de tokens.
