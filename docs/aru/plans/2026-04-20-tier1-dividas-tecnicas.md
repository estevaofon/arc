# Plano: Tier 1 — Dívidas técnicas que já mordem

**Criado:** 2026-04-20
**Status:** Aprovado (v2 após revisão externa) — implementação integral em uma passada
**Depende de:** nada
**Objetivo:** Fechar 5 buracos de correção e observabilidade antes de investir em features novas (LSP, apply_patch, worktree, hooks expandidos). Total ~14–18h numa branch única com 5 commits cirúrgicos (rollback por commit, menos burocracia de review que 5 PRs).

### Histórico de revisão

**v2 (2026-04-20, pós-crítica externa):**
- Stage 1: default do timeout passa a `None` (opt-in por tool) — `_thread_tool` é usado por custom plugins via shim `tools/codebase.py`, 60s global quebra tools custom lentos legítimos.
- Stage 3: adicionado micro-passo pré-refactor (1-line fix em `delegate.py:445`) — pode resolver 70% do problema antes de investir no marker estruturado.
- Stage 4: clarificação — `list.append` é atômico via GIL; lock é para *iteração* (cleanup/snapshot), não para append. Docstring precisa deixar explícito. `custom_agent_defs` é reassinado uma vez em `delegate.py:601` e lido depois → efetivamente read-only, não precisa lock (anotado).
- Stage 5: estado `unavailable` deixa de ser absorvente — cooldown de 60s + half-open retry + catalog lifecycle atômico durante `/mcp restart`.
- Meta: 1 branch + 5 commits (alinhado com "integral sem validação entre stages").
- Smoke test MCP: config concreto (`command: "mcp-server-que-nao-existe"`).
- Esforço revisado: 12–16h → **14–18h**.

---

## 1. Contexto

Auditoria interna identificou 5 problemas que comprometem confiabilidade ou debugabilidade hoje. Nenhum é catastrófico isolado, mas todos desgastam a experiência em sessões longas ou com paralelismo. Pagando a dívida agora, qualquer feature nova do Tier 2/3 herda um chão estável — caso contrário a mesma corrosão reaparece em superfícies novas (novo tool silenciosamente trunca, novo hook silenciosamente morre, etc.).

### Mapa dos alvos

| # | Problema | Arquivos principais | Severidade | Esforço |
|:-:|---|---|:-:|:-:|
| 1 | `asyncio.to_thread` sem timeout — REPL trava em fs lento | `aru/tools/_shared.py` | Média | 2–3h |
| 2 | Hooks de plugin com `except/pass` silenciam erros | `cli.py`, `runner.py`, `plugins/manager.py` | Média | 2–3h |
| 3 | Truncation: 1 call site sem `source_tool` + metadata pobre | `aru/context.py`, `aru/tools/delegate.py`, `aru/tools/_shared.py` | Média | 1–3h (staged) |
| 4 | `fork_ctx()` sem lock p/ iteração de `subagent_instances` e `tracked_processes` | `aru/runtime.py` | Média | 2–3h |
| 5 | MCP sem tracking de saúde, sem cooldown, sem lifecycle de restart | `aru/tools/mcp_client.py` | Alta | 4–5h |

### Ordem de execução sugerida

Estágios são independentes; ordenados para que observabilidade venha antes do resto (fica mais fácil debugar as próximas etapas se hooks e truncation já estão transparentes):

1. **Stage 2** (hooks errors visíveis) — destrava diagnóstico do resto
2. **Stage 1** (timeouts) — impede travas enquanto você valida o resto
3. **Stage 3** (truncation estruturada)
4. **Stage 4** (fork_ctx thread-safe)
5. **Stage 5** (MCP health) — mais complexo e mais dependente dos outros

### Não-objetivos

1. Não reescrever o sistema de truncation — só enriquecer a metadata retornada ao modelo.
2. Não implementar reconexão automática de MCP servers (fora de escopo do Tier 1; cabe em um futuro "Tier 2 MCP UX").
3. Não introduzir novo framework de logging; reusar `logging` stdlib + ring buffer.
4. Não quebrar API pública de tools nem `RuntimeContext`.
5. Não adicionar teste-de-UI; cobertura é via pytest em unidades.

---

## 2. Stage 1 — Timeouts em tools threaded (~2–3h)

**Arquivo:** `aru/tools/_shared.py` (função `_thread_tool`, linhas 51–63)

### Problema

`_thread_tool` envolve funções síncronas em `asyncio.to_thread(sync_fn, *args, **kwargs)` sem timeout. Se um `read_file` num arquivo gigante em NFS, um `grep_search` em monorepo ou um I/O bloqueado por lock trava por minutos, o REPL inteiro fica preso — Ctrl+C demora a propagar e deixa thread órfã.

### Shape da solução

**Princípio:** backwards-compat por default. `_thread_tool` é importado por `tools/codebase.py` (shim público) e potencialmente usado por custom plugins — default global de 60s quebraria tools custom legítimos e lentos (ex. ranker em monorepo enorme, análise AST em vendor/). Opt-in explícito por site é mais seguro.

1. Estender assinatura de `_thread_tool` com default **`None`**:
   ```python
   def _thread_tool(sync_fn, *, timeout: float | None = None):
       @functools.wraps(sync_fn)
       async def wrapper(*args, **kwargs):
           coro = asyncio.to_thread(sync_fn, *args, **kwargs)
           if timeout is None:
               return await coro
           try:
               return await asyncio.wait_for(coro, timeout=timeout)
           except asyncio.TimeoutError:
               return (
                   f"[Tool timeout: {sync_fn.__name__} exceeded {timeout}s. "
                   f"Thread may still be running in background. "
                   f"Narrow the query or raise timeout explicitly.]"
               )
       return wrapper
   ```

2. Opt-in por tool nos sites de wrap em Aru (plugins custom permanecem sem timeout automático):
   - `tools/file_ops.py:449-454` — read/write/edit/list: `timeout=60`
   - `tools/registry.py:33` — rank_files: `timeout=45`
   - search (glob/grep em `tools/search.py`): envolver os async wrappers existentes; `timeout=45`
   - web (`tools/web.py`): `timeout=30` (cap externo sobre timeouts internos já existentes)
   - bash: **não aplicar** — `shell.py` gerencia subprocess timeout próprio
   - delegate: não aplicar — sub-agentes têm lifecycle diferente

3. Documentar em AGENTS.md:
   - `_thread_tool(fn, timeout=N)` é opt-in; default preserva comportamento atual.
   - `asyncio.to_thread` não aborta a thread subjacente (limitação Python) — o timeout só retorna controle ao REPL. Processo pode ter leak de recurso se a thread continuar. Aceito como tradeoff.

### Edge cases

- Retornar string de erro (não levantar exceção) para que o LLM consiga reagir no próximo turn.
- Verificar que `asyncio.wait_for` não gera `_GeneratorExit` em Python 3.13 (mudança de semântica em 3.11+) — validar no pytest.
- Default `timeout=None` garante que plugin custom que hoje envolve um sync lento via `_thread_tool` continua funcionando sem quebra.

### Testes

- `tests/test_thread_tool_timeout.py`:
  - Tool síncrona que dorme 2s com `timeout=1` → retorna string `[Tool timeout ...]`
  - Tool síncrona que completa em 0.1s com `timeout=1` → retorna resultado normal
  - Tool síncrona que dorme 5s **sem** timeout (default None) → completa normalmente (valida backwards-compat)
  - Exception no corpo do tool (não timeout) propaga normalmente
- Verificar que `read_file` num arquivo pequeno continua retornando conteúdo inteiro.

---

## 3. Stage 2 — Observabilidade de erros em hooks (~2–3h)

**Arquivos:**
- `aru/plugins/manager.py` (`publish`, linhas 184–213; `fire` se tiver except/pass)
- `aru/cli.py` (linhas 301–310, 477–483: call sites `session.start`/`session.end`)
- `aru/runner.py` (call sites `tool.execute.before/after`, `chat.message`)
- `aru/tools/delegate.py` (call sites `subagent.*`)

### Problema

1. Call sites envolvem `await _plugin_mgr.publish(...)` em `try/except Exception: pass`. Qualquer erro fora do próprio dispatch do manager some silencioso.
2. Dentro de `publish()` (manager.py:184), erros de subscriber já vão para `logger.error(...)`, **mas o logger `aru.plugins` não tem handler configurado** — mensagem nunca chega ao usuário.
3. Não há forma de inspecionar erros passados (ex. `/debug plugin-errors`).

### Shape da solução

**Parte A — configurar o logger root de plugins:**
- Em `aru/cli.py` durante `run_cli()`, adicionar handler ao logger `"aru.plugins"` que escreve em `stderr` com nível WARNING e formato curto.
- Em modo `--verbose`, nível DEBUG.
- Não adicionar handler global `aru.*` para não quebrar testes que capturam logs.

**Parte B — ring buffer de erros no PluginManager:**
- `PluginManager.__init__` cria `self._error_log: collections.deque = deque(maxlen=50)`.
- Cada `except Exception` em `publish`/`fire` anexa `{timestamp, event, plugin, error, traceback}` ao buffer antes de chamar `logger.error`.
- Expor `PluginManager.recent_errors() -> list[dict]`.

**Parte C — comando `/debug plugin-errors`:**
- Adicionar handler em `commands.py` que formata `ctx.plugin_manager.recent_errors()` numa tabela Rich.
- Sem erros: "No plugin errors this session."

**Parte D — remover `except Exception: pass` cegos:**
- Substituir em todos os call sites por `except Exception as e: logger.exception("publish %s failed: %s", event_type, e)`.
- Manter `try/except` (não deixa erro de hook quebrar turn do usuário), mas agora com log.

### Arquivos tocados

- `aru/plugins/manager.py` (+15 linhas)
- `aru/cli.py` (logger setup + remover except/pass)
- `aru/runner.py` (remover except/pass)
- `aru/tools/delegate.py` (idem)
- `aru/commands.py` (+ `/debug plugin-errors`)

### Testes

- `tests/test_plugin_errors.py`:
  - Plugin que levanta RuntimeError em subscriber `session.start` → `publish` completa sem exceção; error_log tem 1 entrada; logger `aru.plugins` emite record.
  - Ring buffer estica até 50 então rotaciona.
  - `/debug plugin-errors` imprime tabela com pelo menos uma linha quando há erros.

---

## 4. Stage 3 — Truncation: fix bug + metadata estruturada (~1–3h, staged)

**Arquivos:**
- `aru/tools/delegate.py` (linha 445 — fix 1-line)
- `aru/context.py` (`truncate_output`, `_build_truncation_hint`, linhas 385–493)
- `aru/tools/_shared.py` (`_truncate_output`, linha 45)

### Problema

`truncate_output` já salva full output em disco (OK) e já inclui `saved_path` no hint (OK). **Mas:**

1. `source_tool` nem sempre é passado — `delegate.py:445` chama `_truncate_output(f"{header} {final_text}")` sem `source_tool`, então hint cai no ramo genérico. **Este é 1-line fix.**
2. Hint é prosa não estruturada. LLM precisa parsear "[Truncated. Full output saved to: ...]" vs. se fosse um bloco delimitado previsível.
3. Falta a *extensão* total (bytes / linhas) quando a truncation ocorre — modelo não sabe se perdeu 10% ou 90%.
4. Falta o *range* do que foi mostrado (ex. "lines 1–300 + 1800–2000 of 2000 shown; 1500 omitted").

### Shape da solução — abordagem staged (YAGNI primeiro)

**Step 3a — Fix 1-line (15min):** Passar `source_tool="delegate_task"` em `delegate.py:445`.
```python
return _truncate_output(f"{header} {final_text}", source_tool="delegate_task")
```
Rodar a suite manual (Stage 2 logger já ajuda a ver). **Medir:** em sessões reais, com `source_tool` + `saved_path` já presentes no hint atual, quantos retries confusos o modelo faz?
- Se truncation mal-direcionada (re-query incorreta / loop) cair significativamente → parar aqui, arquivar 3b-3c como non-goal para Tier 1.
- Se ainda houver confusão sobre tamanho original ou range mostrado → avançar para 3b.

**Step 3b — Metadata estruturada (~2h, opcional):**

1. Definir um delimitador estruturado padronizado no começo do hint:

   ```
   <truncation
     tool="bash"
     source_file="/abs/path"
     original_bytes="542_123"
     original_lines="2000"
     shown_lines="1..300,1800..2000"
     omitted_lines="1500"
     saved_at="/abs/path/to/full.txt"
   />
   ```

   - Formato XML-like (LLM parseia bem) e o system prompt documenta o formato uma vez.
   - Campos `original_*` e `shown_lines` permitem o modelo decidir se re-consulta com `start_line`/`end_line` ou faz grep no saved_at.

2. Refatorar `_build_truncation_hint` para receber um dataclass `TruncationMetadata` em vez de muitos kwargs:
   ```python
   @dataclass
   class TruncationMetadata:
       source_tool: str = ""
       source_file: str = ""
       original_bytes: int = 0
       original_lines: int = 0
       shown_lines_ranges: list[tuple[int, int]] = field(default_factory=list)
       saved_path: str | None = None
   ```

3. Auditar call sites para passar `source_tool` consistentemente:
   - `delegate.py:445` → **já corrigido em Step 3a**
   - `shell.py:189,191` → já passa `"bash"`
   - `file_ops.py:120` → já passa source_file
   - `search.py:210,334` → já passa `"grep"`
   - `web.py:207,259` → já passa `"web_fetch"`

4. Documentar o formato `<truncation />` em `agents/base.py` (BASE_INSTRUCTIONS) em 3–5 linhas.

**Step 3c — opcional:** se 3b virar ganho claro mas ainda houver dúvida do LLM sobre *onde* truncou, adicionar `shown_lines_ranges` com head+tail range. Gated pela mesma medição da Step 3a.

### Não-objetivo

- Não alterar thresholds (TRUNCATE_MAX_BYTES / TRUNCATE_MAX_LINES). Esses estão bem calibrados.
- Não comprimir head + tail diferente. Só enriquecer marker.
- 3b/3c são opt-in pós-medição — se 3a resolve, fecha o stage.

### Testes

- `tests/test_context.py` (expandir):
  - Output com 3000 linhas → marker contém `original_lines="3000"` e `omitted_lines=...`
  - Output abaixo do limite → sem marker
  - Output só longo em bytes (poucas linhas, uma gigante) → marker indica bytes
- `tests/test_truncation_marker_parsing.py`:
  - Parse do marker com regex simples recupera todos os campos

---

## 5. Stage 4 — `fork_ctx()` thread-safe para paralelismo (~2–3h)

**Arquivo:** `aru/runtime.py` (função `fork_ctx`, linhas 199–237; `RuntimeContext`, linhas 83–172)

### Problema

`fork_ctx()` usa `copy.copy(original)` e depois reassina campos mutáveis seletivamente. O que **continua compartilhado por referência** após o fork:

| Campo | Tipo | Compartilhado? | Mutações em runtime | Risco real |
|---|---|:-:|---|---|
| `subagent_instances` | `dict[str, Agent]` | **sim** | write em `delegate.py` após spawn; `pop`/`clear` em cleanup | Sibling forks escrevendo concorrente; **iteração** em cleanup enquanto outro fork insere |
| `tracked_processes` | `list` | **sim** | `.append(proc)` em `shell.py:~34`; iter+remove em cleanup | **Append é atômico via GIL (CPython) — OK.** Risco é **iterar para cleanup enquanto outro thread faz append** |
| `custom_agent_defs` | `dict` | sim | Reassinado uma vez em `delegate.py:601` durante setup; leitura pura depois | **Read-only em runtime** — não precisa lock (anotar no docstring) |
| `subagent_counter` | `int` (imutável) + lock | sim + lock | `subagent_counter_lock` já protege | Já OK |
| `abort_event` | `threading.Event` | sim | `.set()` / `.clear()` | Por design (propaga cancel) |
| `console` / `display` / `live` | shared | sim | Rich gerencia própria sincronização | OK |

Cenário concreto: `delegate_task` com dois sub-agentes em `asyncio.gather`. Um termina e escreve `subagent_instances["A"] = agent`. O outro, em error handler, itera `subagent_instances.items()` para cleanup — itera dict que está sendo mutado → `RuntimeError: dictionary changed size during iteration`.

### Shape da solução

**Princípio:** lock cobre **iteração/snapshot**, não write individual. Append em list e set em dict são atômicos via GIL no CPython; o que quebra é iter+mutate concorrente.

1. Adicionar lock dedicado ao `subagent_instances`:
   ```python
   subagent_instances_lock: threading.Lock = field(default_factory=threading.Lock)
   ```
   - Compartilhado por referência no fork (propósito: cross-fork coordination).

2. Criar helpers em `runtime.py`:
   ```python
   def register_subagent_instance(task_id: str, agent: Any) -> None:
       # Write individual — GIL já protege, lock para consistência com reads que iteram
       ctx = get_ctx()
       with ctx.subagent_instances_lock:
           ctx.subagent_instances[task_id] = agent

   def get_subagent_instance(task_id: str) -> Any | None:
       ctx = get_ctx()
       with ctx.subagent_instances_lock:
           return ctx.subagent_instances.get(task_id)

   def clear_subagent_instance(task_id: str) -> None:
       ctx = get_ctx()
       with ctx.subagent_instances_lock:
           ctx.subagent_instances.pop(task_id, None)

   def snapshot_subagent_instances() -> dict[str, Any]:
       # Usado em cleanup que precisa iterar — devolve cópia imutável
       ctx = get_ctx()
       with ctx.subagent_instances_lock:
           return dict(ctx.subagent_instances)
   ```

3. Atualizar `tools/delegate.py` para usar estes helpers em vez de acesso direto ao dict. Qualquer `for k, v in ctx.subagent_instances.items()` vira `for k, v in snapshot_subagent_instances().items()`.

4. `tracked_processes`: criar `tracked_processes_lock` separado. **Append continua direto** (atômico via GIL, não precisa lock). Helper `snapshot_tracked_processes()` para cleanup/listagem que itera.

5. Docstring em `fork_ctx()` ganha tabela explicitando:
   - O que é compartilhado vs isolado e por quê
   - Quais mutações são atômicas (append em list, set em dict) → não requerem lock
   - Quais requerem lock (iteração, pop-and-return composto, snapshot)
   - `custom_agent_defs` é efetivamente read-only pós-setup → sem lock

### Não-objetivo

- Não mover `custom_agent_defs` para behind-lock — reassinado uma vez em setup; leituras subsequentes veem sempre o mesmo dict. Se algum dia virar hot-reloadable, revisita.
- Não trocar `threading.Lock` por `asyncio.Lock`. Locks são touched por tool threads (`asyncio.to_thread`) e pela loop principal — `threading.Lock` funciona nos dois, `asyncio.Lock` só na loop.

### Testes

- `tests/test_fork_ctx_concurrency.py`:
  - 10 forks em `asyncio.gather`, cada um chama `register_subagent_instance(unique_id, mock_agent)` — resultado tem 10 entradas distintas
  - Fork A registra "x", fork B lê "x" via get_subagent_instance → mesma instância
  - `clear_subagent_instance("y")` em fork B não afeta "x" registrado pelo A
- Existente: `tests/test_runtime.py` já deve cobrir fork básico; verificar que continua passando.

---

## 6. Stage 5 — MCP health tracking + cooldown + restart lifecycle (~4–5h)

**Arquivo:** `aru/tools/mcp_client.py` (linhas 67–143 principalmente)

### Problema

1. `_start_server` (linha 67) faz `except Exception: print(...)` e segue. Se o servidor crashar na inicialização, é removido silenciosamente do `self.sessions`, mas catalog pré-existente em reinício pode apontar pra sessão morta (não aplicável no boot, sim em reload future).
2. `_fetch` (linha 112) idem — tools daquele servidor simplesmente somem.
3. `call_tool` (linha 125) pega exceção, mas devolve string "Error executing ...". LLM não tem pista de que é **o servidor inteiro** que está morto vs um erro pontual da tool.
4. Sem jeito de listar status via `/mcp status`.

### Shape da solução

1. Adicionar estado de saúde por server com cooldown:

   ```python
   from typing import Literal

   HealthState = Literal["healthy", "initializing", "failed", "unavailable", "cooldown"]

   @dataclass
   class McpServerHealth:
       name: str
       state: HealthState
       last_error: str = ""
       last_error_at: float = 0.0         # time.time()
       last_success_at: float = 0.0
       consecutive_failures: int = 0
       cooldown_until: float = 0.0        # retry permitido a partir daqui
   ```

2. `McpSessionManager` ganha `self.health: dict[str, McpServerHealth] = {}`.
   - `_start_server` marca `"initializing"` antes, `"healthy"` após success, `"failed"` + last_error + timestamp no `except`.
   - `_fetch` idem.

3. **Circuit-breaker lite com half-open retry** (evita estado absorvente). Máquina de estados em `call_tool`:

   | Estado atual | Condição | Próximo estado | Comportamento |
   |---|---|---|---|
   | `healthy` | success | `healthy` | `consecutive_failures = 0` |
   | `healthy` | failure | `healthy` | `consecutive_failures++` |
   | `healthy` | `consecutive_failures ≥ 3` | `unavailable` | `cooldown_until = now + 60s` |
   | `unavailable` | `now < cooldown_until` | `unavailable` | retorna erro sem tentar |
   | `unavailable` | `now ≥ cooldown_until` | `cooldown` (half-open) | **permite 1 tentativa** |
   | `cooldown` | success | `healthy` | reset counters |
   | `cooldown` | failure | `unavailable` | `cooldown_until = now + 120s` (backoff dobrado) |
   | `failed` (startup) | — | `failed` | só `/mcp restart` recupera; não tem half-open automático |

   **Distinção importante:** `failed` = servidor nunca subiu (problema estrutural: binário ausente, config errada); `unavailable` = servidor subiu mas está flaky. Só o segundo tem cooldown automático.

4. `call_tool` (linha 125):
   - Consultar `self.health[server]` e decidir via tabela acima.
   - Mensagens de erro ao LLM são específicas:
     - `failed`: `"MCP server 'X' failed at startup: <last_error>. Use /mcp restart X or local tools."`
     - `unavailable` (cooldown ativo): `"MCP server 'X' unavailable, retrying in {cooldown_until - now}s. Use local tools or /mcp restart X."`
     - `unavailable` (cooldown expirou → half-open success fails): mensagem normal de erro da call.

5. `get_catalog_text()` (linha 144):
   - Omite servers com state != "healthy", mas inclui nota ao final:
     ```
     > Note: MCP server 'X' unavailable (cooldown: 42s) — N tools temporarily hidden.
     > Note: MCP server 'Y' failed to start — N tools unavailable.
     ```
   - Assim o LLM não vê tools fantasma mas sabe que algo está quebrado.

6. Novo comando `/mcp status` em `commands.py`:
   - Tabela: Server | State | Tools | Failures | Last Error | Cooldown
   - Usa Rich para colorir state (`healthy` verde, `cooldown` amarelo, `failed`/`unavailable` vermelho).

7. Novo comando `/mcp restart <name>` com **lifecycle atômico do catalog**:
   ```
   1. Acquire manager lock (evita call_tool concorrente ver estado inconsistente)
   2. Marcar health[name].state = "initializing"
   3. Remover entries do catalog cujo server == name
   4. Fechar sessão antiga (se existir) via exit_stack parcial
   5. Chamar _start_server(name, svr_config) — pode ir para "healthy" ou "failed"
   6. Se "healthy", rodar _fetch(name, session) e inserir entries no catalog
   7. Release manager lock
   ```
   - Janela de indisponibilidade é coberta por (2): qualquer `call_tool` durante o restart vê `initializing` e retorna "server restarting, retry".

### Não-objetivo

- Não implementar timer background que tentar reconectar automaticamente — cooldown é lazy (acionado na próxima call, não num thread separado). Evita complicar runtime async.
- Não implementar persistência de health entre sessões.
- Não implementar graceful shutdown de calls em voo durante `/mcp restart` — calls ativas terminam na sessão antiga (podem dar erro ao final), calls novas pegam a nova sessão.

### Testes

- `tests/test_mcp_health.py`:
  - Mock StdioServerParameters que sempre falha → `_start_server` marca state="failed" com last_error preenchido.
  - Mock que funciona → state="healthy" após initialize.
  - `call_tool` num server "failed" → retorna string com "failed at startup" sem tentar call.
  - 3 crashes seguidos em call_tool num server healthy → state vira "unavailable" com `cooldown_until ≈ now+60s`.
  - `call_tool` durante cooldown → retorna "unavailable, retrying in Ns"; não chama session.
  - Monkeypatch `time.time()` para avançar 61s → próxima call entra em half-open, tenta uma vez:
    - Se success → state volta para "healthy", contadores resetados.
    - Se failure → state continua "unavailable" com `cooldown_until ≈ now+120s` (backoff dobrado).
  - `get_catalog_text()` omite tools de server failed/unavailable + adiciona nota no final.
  - `/mcp restart X` remove entries do catalog antes de reconectar; chamada a tool daquele server durante restart retorna "restarting".

---

## 7. Testes consolidados

Novos test files (5):
- `tests/test_thread_tool_timeout.py` (Stage 1)
- `tests/test_plugin_errors.py` (Stage 2)
- `tests/test_truncation_marker_parsing.py` (Stage 3)
- `tests/test_fork_ctx_concurrency.py` (Stage 4)
- `tests/test_mcp_health.py` (Stage 5)

Expansões:
- `tests/test_context.py` → casos de metadata nova em truncation

Smoke test manual ao final (concreto, não mental):

1. **Timeout**: criar `.aru/tools/slow_tool.py` com função que dorme 120s, invocar via agent → prompt volta em ≤ timeout configurado (60s para tools file-like); `[Tool timeout ...]` aparece no histórico.

2. **Plugin error visibility**: criar `.aru/plugins/broken.py`:
   ```python
   from aru.plugins import Hooks
   def plugin(api):
       def on_session_start(payload):
           raise RuntimeError("deliberate test failure")
       api.subscribe("session.start", on_session_start)
   ```
   Bootar `aru` → `/debug plugin-errors` lista 1 entrada com `event=session.start`, `plugin=broken`, traceback.

3. **Truncation**: `bash "seq 1 5000"` → resultado contém marker estruturado; verificar que `_save_truncated_output` salvou arquivo em `.aru/truncated/`; modelo consegue referenciar arquivo salvo na próxima call.

4. **MCP broken**: adicionar em `.aru/mcp_servers.json`:
   ```json
   {
     "mcpServers": {
       "broken": { "command": "mcp-server-que-nao-existe-no-path", "args": [] }
     }
   }
   ```
   Bootar `aru` → startup mostra warning via logger; `/mcp status` mostra `broken` vermelho com "failed at startup"; catálogo do LLM não lista tools de `broken`; prompt pedindo tool MCP inexistente não gera loop de erro.

5. **MCP cooldown**: configurar server que responde mas falha em call (mock MCP server que retorna sempre erro). 3 calls consecutivas → `/mcp status` mostra `unavailable` com cooldown; aguardar 61s + nova call → vê "cooldown" → volta a tentar.

6. **Concurrent sub-agents**: `delegate_task` em fan-out de 5 sub-agentes. Se algum crashar, não deve gerar `RuntimeError: dictionary changed size during iteration` em cleanup.

Meta de cobertura: pytest com `--cov=aru` deve manter ≥ nível atual nos módulos tocados.

---

## 8. Riscos

| # | Risco | Mitigação |
|:-:|---|---|
| R1 | `asyncio.wait_for` em Python 3.13 pode retornar enquanto thread continua segurando fd/lock → leak de FS handle | Documentar no AGENTS.md; leak não quebra; thread pool padrão tem cap (40 workers) então não dá pra explodir |
| R2 | Logger config em cli.py pode conflitar com pytest caplog | Usar `logging.getLogger("aru.plugins").addHandler(...)` escopado; testes capturam normalmente |
| R3 | Campos novos no `<truncation />` marker se não documentados no system prompt — modelo ignora ou confunde | Atualizar BASE_INSTRUCTIONS em `agents/base.py` no mesmo PR |
| R4 | Lock em `subagent_instances` pode serializar operações que hoje são paralelas | Lock só cobre acesso ao dict (microsegundos); não segura durante execução do sub-agent |
| R5 | ~~MCP server "flaky" pode ficar permanentemente unavailable~~ (mitigado em v2) | Cooldown de 60s + half-open retry + backoff dobrado no re-fail; qualquer sucesso em half-open reseta para healthy |
| R6 | Auto-background de plugins/subagents pode ter outros `except/pass` que não localizei | Grep final antes do merge: `rg "except.*:\s*pass" aru/` — limpar todos que envolvam publish/hook |
| R7 | Cooldown de 60s pode ser curto demais para server com falha genuína mas temporária (deploy, restart do backend) — faria ping-pong healthy/unavailable | Backoff dobrado no re-fail já suaviza; se virar problema real, parametrizar em `aru.json` (non-goal para Tier 1) |
| R8 | Stage 4 adiciona overhead de lock em path quente (register sub-agent em fan-out de 50 jobs) | Locks são micro-seconds; overhead desprezível vs spawn de sub-agent (ms). Medir se perf degradar mensuravelmente |
| R9 | Stage 3 medição após 3a é subjetiva — "quantos retries confusos?" não tem threshold claro | Aceitar: Stage 3a é fixed, 3b é opt-in consciente. Se não houver clareza, default é parar em 3a |

---

## 9. Estrutura de entrega (branch + commits)

Implementação **integral em uma branch única** (`feat/tier1-tech-debt` ou similar), com 5 commits cirúrgicos. Cada commit = 1 stage completo + testes verdes. Razão: usuário prefere implementação integral sem validação entre stages; 5 PRs separados adicionam burocracia de review sem ganho real (stages são independentes mas são todos dívida do mesmo domínio).

Ordem dos commits (inversa da ordem de execução — cada um builda sobre o anterior em termos de confiança, não dependência técnica):

1. `refactor(plugins): add error observability to hooks` (Stage 2)
2. `feat(tools): opt-in timeouts for threaded tools` (Stage 1)
3. `feat(tools): structured truncation metadata` (Stage 3 — se 3a resolve, commit menor)
4. `fix(runtime): lock-protected iteration for shared sub-agent state` (Stage 4)
5. `feat(mcp): health tracking with cooldown and restart lifecycle` (Stage 5)

Rollback: `git revert <hash>` de qualquer commit individual. Nenhum commit depende de outro.

## 10. Checklist de merge

- [ ] Stage 2 — logger handler + ring buffer + `/debug plugin-errors` + testes verdes
- [ ] Stage 1 — `_thread_tool` com `timeout=None` default + opt-in nos sites Aru + testes verdes
- [ ] Stage 3a — fix 1-line em `delegate.py:445` (`source_tool="delegate_task"`)
- [ ] Stage 3b/3c — apenas se medição pós-3a justificar (anotar decisão no commit)
- [ ] Stage 4 — `subagent_instances_lock` + `tracked_processes_lock` + helpers + call sites migrados + testes verdes
- [ ] Stage 5 — `McpServerHealth` com cooldown + half-open retry + `/mcp status` + `/mcp restart` atômico + testes verdes
- [ ] `rg "except.*:\s*pass" aru/` — zero matches em call sites de hook/publish
- [ ] Smoke manual dos 6 cenários passa
- [ ] AGENTS.md atualizado nas seções pertinentes (tool timeouts opt-in, truncation marker format se 3b, plugin errors command, MCP health states/cooldown)
- [ ] Versão bump em `pyproject.toml` + `aru/__init__.py` (minor bump: 0.33.x → 0.34.0)
