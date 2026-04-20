# Plano: Tier 2 — Features de produto que expandem capacidade

**Criado:** 2026-04-20
**Status:** Proposta — implementação integral em uma passada após Tier 1 mergeado
**Depende de:** Tier 1 (`feat/tier1-tech-debt`) — plugin hook observability destrava debug de hooks novos; MCP health lifecycle é referência pro lifecycle do LSP.
**Objetivo:** Subir o teto de capacidade do Aru com 5 features de produto que mudam o tipo de tarefa que ele consegue atacar (refactor multi-arquivo seguro, navegação semântica, paralelismo real, memória entre sessões, plugin ecosystem destravado). Total ~43–57h numa branch única com 5 commits/PRs.

---

## 1. Contexto

Tier 1 pagou dívida interna (reliability, observability). Tier 2 é sobre **expandir o que Aru consegue fazer**. Inspiração direta em OpenCode (`packages/opencode/src/tool/apply_patch.ts`, `src/lsp/`, `src/worktree/`) e Claude Code (`src/types/hooks.ts`, `src/services/SessionMemory/`, `src/memdir/`).

Por que essas 5 especificamente (em vez de voice, cron, IDE, vim, etc.):

| Feature | Teto de capacidade que destrava |
|---|---|
| LSP integration | Refactoring multi-arquivo baseado em semântica (grep alucina) |
| apply_patch | Refactor atômico que falha todo ou aplica todo — não meio quebrado |
| Worktree | Paralelismo real entre sub-agentes sem corrupção de branch |
| Hooks expandidos | Plugin ecosystem funcional (hoje plugin só wrappa tool) |
| Auto-memory | Continuidade entre sessões (facts sobre o projeto sobrevivem) |

### Mapa dos alvos

| # | Feature | Arquivos principais | Inspiração | Esforço |
|:-:|---|---|---|:-:|
| 1 | Worktree como primitivo | `aru/tools/worktree.py` (new), `runtime.py`, `commands.py` | CC `EnterWorktreeTool`, OC `src/worktree/` | 6–8h |
| 2 | `apply_patch` tool atômica | `aru/tools/apply_patch.py` (new), integra com `checkpoints.py` | OC `tool/apply_patch.ts` | 8–10h |
| 3 | Hooks lifecycle expandido | `aru/plugins/hooks.py`, `runner.py`, `cli.py`, tools | CC `types/hooks.ts` | 5–7h |
| 4 | Auto-memory extraction | `aru/memory/` (new package), `session.py`, `runner.py` | CC `services/SessionMemory/`, `memdir/` | 8–10h |
| 5 | LSP integration (Python + TS) | `aru/lsp/` (new package), 4 tools em `tools/lsp.py` (new) | OC `src/lsp/` | 16–22h |

### Ordem de execução sugerida

Ramp up de easy para hard, com dependências fracas entre stages:

1. **Stage 1 (Worktree)** — easy; dependência zero; unblocks testar paralelismo dos outros
2. **Stage 2 (apply_patch)** — medium; usa `checkpoints.py` já existente para rollback; independente
3. **Stage 3 (Hooks expandidos)** — medium; emite eventos que Stage 1 (worktree.create) já pode consumir, e Stage 5 (lsp.diagnostics) também
4. **Stage 4 (Auto-memory)** — medium; depende de Stage 3 (hook `post_turn` pra disparar extração)
5. **Stage 5 (LSP)** — hard; vai sozinho; maior payoff mas também maior surface area

### Não-objetivos

1. **Worktree per-subagent automatic** (`delegate_task(worktree=True)`) — exige refactor cwd-aware em todos os tools. Fora de escopo; pode vir em Tier 2.5 depois que LSP estiver estável. Stage 1 cobre só a primitiva manual (`/worktree enter`).
2. **LSP para todas as linguagens** — Stage 5 cobre Python (pylsp) e TypeScript (typescript-language-server). Rust/Go/etc. via config mas sem integração testada.
3. **Code formatter auto-run pós-edit** — complementar ao apply_patch mas fora do escopo Tier 2. Plug em `post_file_mutation` hook (Stage 3) quando vier.
4. **Memory sharing entre projetos** — auto-memory é per-project (`~/.aru/projects/<hash>/memory/`), mirroring CC.
5. **Remote LSP / cloud** — só stdio local.
6. **Apply_patch UI preview interativo** — aplicação mostra diff textual; sem select-to-approve. YOLO ou ask permission simples.
7. **Hook system visual editor / config UI** — hooks seguem em código Python puro.

---

## 2. Stage 1 — Worktree como primitivo (~6–8h)

**Arquivos:**
- `aru/tools/worktree.py` (novo)
- `aru/runtime.py` (campo `worktree_path`, helpers `enter_worktree`/`exit_worktree`)
- `aru/commands.py` (comando `/worktree`)
- `aru/cli.py` (dispatch do `/worktree`, prompt UI quando dentro de worktree)
- `aru/display.py` (indicador visual na status bar)
- `tests/test_worktree.py` (novo)

### Problema

Hoje quando o usuário quer trabalhar em duas features em paralelo, tem que alternar branches (destrói estado não-commitado) ou manualmente criar worktrees fora do Aru. Não há suporte de primeira classe — o REPL sempre opera em `os.getcwd()`.

### Shape da solução

**Scope Tier 2:** primary pode entrar e sair de worktrees manualmente; o REPL mostra qual worktree está ativa na status bar; tools respeitam a cwd scoping.

1. Novo campo em `RuntimeContext`:
   ```python
   worktree_path: str | None = None  # absolute path when inside; None = main
   worktree_branch: str | None = None
   ```

2. Helpers em `runtime.py`:
   ```python
   def enter_worktree(path: str, branch: str | None = None) -> None:
       ctx = get_ctx()
       ctx.worktree_path = path
       ctx.worktree_branch = branch
       os.chdir(path)
       ctx.read_cache.clear()
       invalidate_walk_cache()

   def exit_worktree() -> None:
       # Returns to the project root saved at session start
       ctx = get_ctx()
       if ctx.worktree_path is None:
           return
       os.chdir(ctx.session.project_root)
       ctx.worktree_path = None
       ctx.worktree_branch = None
       ctx.read_cache.clear()
       invalidate_walk_cache()
   ```

3. `Session` ganha `project_root: str` capturado em `Session.__init__` — ponto de retorno canônico.

4. Tool `worktree_info()` (read-only) para o agent consultar qual worktree está ativa (bonus: alguns prompts beneficiam de saber).

5. Comando `/worktree` em `commands.py`:
   ```
   /worktree                     list all worktrees for this repo
   /worktree create <branch>     git worktree add ../aru-worktrees/<branch> -b <branch>
   /worktree enter <branch>      chdir into worktree + mark ctx.worktree_*
   /worktree exit                chdir back to project root
   /worktree remove <branch>     git worktree remove ../aru-worktrees/<branch>
   ```
   Estrutura do path: default `<project-parent>/.aru-worktrees/<branch>`. Configurável via `aru.json` `worktree.base_dir`.

6. Status bar (`display.py`): quando `ctx.worktree_path` setado, prepend `🌿 <branch>` em amarelo antes do resto.

7. Permissões: criar/remover worktree é `git` subcomando — já coberto pela regra de bash com `git` seguro. Documentar que `remove` força o worktree fechar (bem-comportado).

### Edge cases

- Worktree já existe quando `create` roda → prompt "Use existing?" (y/n)
- User tenta `/worktree exit` fora de worktree → "Not inside a worktree."
- `session_id` precisa persistir entre enter/exit — worktree NÃO é uma sessão nova, é um cwd temporário da sessão atual. Docstring deixa claro.
- Windows: `git worktree` funciona; paths com espaço precisam quote (já gerenciado via subprocess.list form).
- `atexit`: se o REPL fechar dentro de worktree, não remover automaticamente — user pode ter commits não pushados. Só sair (chdir volta).

### Não-objetivo explícito

- `delegate_task(worktree="feat/x")` — sub-agente em worktree própria. Exige cwd passado por kwarg em todos os tools (file_ops, search, shell) — refactor largo. Fora do escopo Tier 2; plantar nota no código para retomar em Tier 2.5 ou 3.

### Testes

- `tests/test_worktree.py`:
  - `/worktree create branch-x` cria pasta `../.aru-worktrees/branch-x`, adiciona ao `git worktree list`
  - `/worktree enter branch-x` chdir bem-sucedido, `ctx.worktree_path` preenchido
  - `/worktree exit` volta para project root; `ctx.worktree_path` None
  - `read_file("file.py")` dentro da worktree lê do arquivo da worktree, não do root
  - `/worktree enter` fora de um repo git → mensagem de erro clara
  - `/worktree list` lista todas as worktrees do repo

---

## 3. Stage 2 — `apply_patch` tool atômica (~8–10h)

**Arquivos:**
- `aru/tools/apply_patch.py` (novo, ~250 LOC)
- `aru/tools/apply_patch_prompt.txt` (novo, ~70 linhas — copiar formato de OC `apply_patch.txt`)
- `aru/tools/registry.py` (inclui apply_patch no `ALL_TOOLS`)
- `aru/checkpoints.py` (expor API para rollback-em-lote de múltiplos arquivos)
- `tests/test_apply_patch.py` (novo)

### Problema

`edit_files(edits=[...])` é best-effort: se o edit 8 de 15 falhar (pattern não bate, permission denied), edits 1-7 já estão aplicados. Refactor fica em estado inconsistente — agent tem que rodar mais uma rodada para desfazer, e às vezes isso falha também. Não há atomicidade nem rollback explícito.

### Shape da solução

1. Formato de patch tipo OC (stripped-down V4A) — clear, LLM-friendly:

   ```
   *** Begin Patch
   *** Add File: src/new.py
   +def hello():
   +    return "hi"
   *** Update File: src/old.py
   @@ def greet():
   -    print("Hi")
   +    print("Hello, world!")
   *** Update File: src/moved.py
   *** Move to: src/renamed.py
   @@ def foo():
   -    pass
   +    return 42
   *** Delete File: src/obsolete.py
   *** End Patch
   ```

2. Two-phase application em `aru/tools/apply_patch.py`:

   **Phase 1 — Parse + validate (pure, no disk mutation):**
   - `parse_patch(text) -> Patch` → estrutura `Patch` com lista ordenada de operações `FileOp`
   - Tipos: `AddFile(path, content)`, `UpdateFile(path, hunks, move_to=None)`, `DeleteFile(path)`
   - Validar upfront:
     - `AddFile`: path não existe (ou existe e pattern é sobrescrita explícita? prefiro falhar — segurança)
     - `UpdateFile`: path existe; cada hunk tem context suficiente; context bate no arquivo atual (não "stale patch")
     - `DeleteFile`: path existe
     - `Move to:` target não conflita
   - Erro em validação aborta ANTES de tocar em disco. Mensagem: `"Patch validation failed at hunk N: <reason>"`

   **Phase 2 — Apply (transactional):**
   - Checkpoint ANTES de cada arquivo mutado via `ctx.checkpoint_manager.track_edit(path)` (já existe)
   - Aplicar operações em ordem
   - Se qualquer operação falha (I/O error, permission denied mid-batch), chamar `_rollback(applied_so_far)` que usa checkpoint para restaurar
   - Retornar resumo estruturado ou erro

3. API do tool:
   ```python
   @tool
   def apply_patch(patch: str) -> str:
       """Apply a multi-file patch atomically. All or nothing."""
       try:
           parsed = parse_patch(patch)
       except PatchParseError as e:
           return f"Parse error: {e}"
       except PatchValidationError as e:
           return f"Validation error (no files modified): {e}"

       applied_files: list[str] = []
       try:
           for op in parsed.operations:
               _checkpoint_file(op.target_path)
               _apply_op(op)
               applied_files.append(op.target_path)
       except Exception as e:
           _rollback(applied_files)
           return f"Apply error at {op.target_path}: {e}. {len(applied_files)} files rolled back."

       _notify_file_mutation()
       return _format_success_summary(parsed)
   ```

4. Permission gate: cada `_apply_op` chama `resolve_permission(tool_name="apply_patch", ...)` com pattern do target path. Bloqueio em qualquer arquivo aborta TUDO (antes de qualquer disk write) via validation error.

5. Docstring longa em `apply_patch.__doc__` com o spec completo do formato + exemplo, seguindo o padrão OC. Na prática importa o texto de `apply_patch_prompt.txt` em runtime.

### Edge cases

- **Stale patch** (context não bate porque arquivo mudou): detectar em validation; pedir ao agente pra re-read e gerar novo patch.
- **Move + update simultâneo**: `*** Update File: a.py` seguido de `*** Move to: b.py` + hunks. Validar que b.py não conflita, então aplicar update no arquivo original, renomear. Rollback = renomear de volta + restore do original.
- **Permission negada em 1 de N arquivos**: validation rejeita TODO o patch antes de tocar em disco.
- **Line endings**: preservar (CRLF/LF) do arquivo original em UpdateFile. AddFile default LF salvo se for em diretório Windows-only.

### Testes

- `tests/test_apply_patch.py`:
  - Parse do formato básico (add/update/delete) roundtrip
  - Update com hunk simples aplica corretamente
  - Update com context que não bate → `PatchValidationError`, nenhum file escrito
  - Falha no arquivo 3 de 5 → arquivos 1-2 são revertidos via checkpoint, mensagem conta
  - Move: rename + update aplicado; rollback restaura nome + conteúdo
  - Delete: arquivo removido; rollback restaura
  - Permission denied em um dos arquivos → validation rejects before disk touch
  - Integração com `ctx.checkpoint_manager`: depois de `apply_patch` bem-sucedido, `/undo` restaura todos os N arquivos

---

## 4. Stage 3 — Hooks lifecycle expandido (~5–7h)

**Arquivos:**
- `aru/plugins/hooks.py` (expandir `VALID_HOOKS`)
- `aru/runner.py` (emit hooks novos)
- `aru/cli.py` (emit hooks novos)
- `aru/tools/_shared.py` (emit `file.changed` em `_notify_file_mutation`)
- `aru/tools/worktree.py` (emit `worktree.*` — Stage 1 leave hook pontos prontos)
- `aru/context.py` (emit `compact.*`)
- `aru/tools/delegate.py` (emit `subagent.*`)
- `docs/plugin-hooks.md` (novo — referência rápida)
- `tests/test_plugin_hooks_v2.py` (novo)

### Problema

Hoje `VALID_HOOKS` em `plugins/hooks.py` tem uma dúzia de eventos básicos. Plugin custom não consegue:
- Bloquear ou transformar tool calls antes da execução (`PreToolUse` em CC)
- Reagir a compaction pra salvar estado (`PreCompact`/`PostCompact`)
- Auditar permission decisions (`PermissionDenied`)
- Saber que cwd mudou (worktree enter/exit)
- Disparar trabalho após cada file mutation (útil pra linter auto, memory extraction)

CC (`src/types/hooks.ts`) lista ~25 eventos. Não preciso copiar todos; os de maior payoff são:

### Eventos a adicionar

| Evento | Tipo | Payload | Caso de uso principal |
|---|---|---|---|
| `pre_tool_use` | fire (interceptor) | `{tool_name, args, agent_id}` | Plugin audita / bloqueia / muta args |
| `post_tool_use` | publish | `{tool_name, args, result, duration, agent_id}` | Logging / metrics |
| `post_tool_use_failure` | publish | `{tool_name, args, error, agent_id}` | Alertas |
| `file.changed` | publish | `{path, mutation_type: read/write/edit/delete}` | Auto-linter, memory trigger |
| `cwd.changed` | publish | `{old_cwd, new_cwd, reason}` | Plugins sincronizam cache |
| `worktree.create` | publish | `{path, branch}` | IDE extension notifica |
| `worktree.remove` | publish | `{path, branch}` | Cleanup |
| `pre_compact` | fire | `{history_tokens, threshold}` | Plugin salva contexto antes |
| `post_compact` | publish | `{history_tokens_before, history_tokens_after, summary}` | Audit |
| `subagent.start` | publish | `{task_id, agent_name, parent_id, task}` | Progress UI |
| `subagent.complete` | publish | `{task_id, status, duration, tokens_in, tokens_out}` | Metrics |
| `permission.request` | fire | `{tool_name, args, decision}` | Plugin muta decision |
| `permission.denied` | publish | `{tool_name, args, reason, feedback}` | Audit |
| `turn.start` | publish | `{turn_number, user_message}` | Per-turn background tasks |
| `turn.end` | publish | `{turn_number, assistant_response, tokens_in, tokens_out}` | Auto-memory trigger |

### Shape da solução

1. Expandir `VALID_HOOKS` em `plugins/hooks.py` com os nomes acima. Validar que todo call site usa um nome válido (assertion).

2. Emissão:
   - `pre_tool_use`/`post_tool_use*`: no tool-call loop em `runner.py:530` (startup) e `:562` (complete). Já tem `tool.called`/`tool.completed` (manter como aliases deprecated? ou renomear direto?). **Decisão:** adicionar os novos nomes `pre_tool_use`/`post_tool_use` e manter os velhos como aliases que disparam juntos por uma release — deprecate com warning em release seguinte.
   - `file.changed`: em `tools/_shared.py:_notify_file_mutation`. Já é call site central.
   - `cwd.changed`: em `runtime.enter_worktree`/`exit_worktree`, e em qualquer outro lugar que faça `os.chdir` (raro).
   - `worktree.*`: em `aru/tools/worktree.py`.
   - `pre_compact`/`post_compact`: em `context.should_compact`/`apply_compaction`.
   - `subagent.*`: em `tools/delegate.py`.
   - `permission.*`: em `permissions.resolve_permission` e `permissions.prompt_for_permission`.
   - `turn.*`: em `runner.prompt` (início/fim).

3. Interceptor vs publish. **Interceptors** (`fire`) podem modificar `data` in-place e raise para bloquear. `publish` é fan-out read-only.
   - `pre_tool_use`: fire — plugin pode levantar `PermissionError` para bloquear, ou modificar `data["args"]`.
   - `pre_compact`: fire — plugin pode rejeitar compaction levantando `RuntimeError`.
   - `permission.request`: fire — plugin pode mudar `data["decision"]` de "ask" para "allow"/"deny".
   - Resto: publish.

4. Documentar em `docs/plugin-hooks.md`: tabela com evento, tipo (fire/publish), payload schema, exemplo mínimo de plugin.

### Edge cases

- Plugin que raise em `pre_tool_use` precisa resultar em tool denial limpo, não crash — wrap no call site e transformar em "PERMISSION DENIED (plugin X): <reason>". Já temos tratamento pra `PermissionError`.
- Aliasing `tool.called` → `pre_tool_use`: emit ambos nos call sites por um release; logger WARN quando um plugin subscribe ao nome velho.

### Testes

- `tests/test_plugin_hooks_v2.py`:
  - Plugin com `pre_tool_use` handler: muta args in-place, tool recebe mutados
  - Plugin com `pre_tool_use` que raise `PermissionError`: tool retorna string "PERMISSION DENIED"; outras tools continuam funcionando
  - Plugin com `pre_compact` que raise: compaction é pulada, turn continua
  - Plugin com `file.changed` subscriber: recebe `path` e `mutation_type` após write_file
  - Plugin com `subagent.complete` subscriber: recebe payload com duration e tokens
  - Emissão de eventos alias (`tool.called` + `pre_tool_use`) ambos disparam até remoção

---

## 5. Stage 4 — Auto-memory extraction (~8–10h)

**Arquivos:**
- `aru/memory/` (novo package)
  - `__init__.py`
  - `store.py` — leitura/escrita de arquivos de memory
  - `extractor.py` — sub-agent que roda a extração
  - `loader.py` — load MEMORY.md índex no startup do agent
- `aru/runner.py` (hook `turn.end` → trigger extract se config ativo)
- `aru/config.py` (config `memory.auto_extract: bool`, `memory.model_ref: str`)
- `aru/session.py` (referência ao project memory dir)
- `aru/agents/base.py` (MEMORY.md content injetado nas instructions quando disponível)
- `tests/test_memory.py` (novo)

### Problema

Aru não tem memória persistente entre sessões. Toda vez que o usuário começa um REPL novo, esquece:
- Preferências ("sempre use pytest, não unittest")
- Project state ("freezing non-critical merges after 2026-03-05")
- Corrections passadas ("não mocke DB em testes — tem incidente do último trimestre")

CC tem isso (`src/services/SessionMemory/`, `src/memdir/`) via auto-extraction em background. É uma feature que se paga em 2-3 sessões.

### Shape da solução

1. **Storage layout (per-project):**
   ```
   ~/.aru/projects/<hash_of_project_root>/memory/
   ├── MEMORY.md              # índex plaintext; 1 linha por memory
   ├── user_preference.md     # one memory per file, with YAML frontmatter
   ├── feedback_tests.md
   └── project_migration.md
   ```

   Formato dos arquivos individuais espelha CC:
   ```markdown
   ---
   name: No mocking DB in tests
   description: Integration tests must hit real DB, not mocks.
   type: feedback
   ---
   Integration tests must use a real database, not mocks.
   **Why:** prior incident where mock/prod divergence masked broken migration.
   **How to apply:** when adding new integration tests, wire to test_db fixture.
   ```

   MEMORY.md é só um índex one-liner:
   ```
   # Memory Index

   - [No mocking DB in tests](feedback_tests.md) — tests must hit real DB
   - [Merge freeze starts 2026-03-05](project_migration.md) — mobile release cut
   ```

2. **Config em `aru.json`:**
   ```json
   {
     "memory": {
       "auto_extract": true,
       "model_ref": "anthropic/claude-haiku-4-5",
       "min_turn_tokens": 500
     }
   }
   ```
   Default: `auto_extract: false` — opt-in. User ativa explicitamente para reduzir custo até a feature estar sólida.

3. **Extraction pipeline (em `aru/memory/extractor.py`):**
   - Subscribed ao hook `turn.end` do Stage 3
   - Se `min_turn_tokens` não atingido, skip (extração custa; turn pequeno dificilmente tem fact worthy)
   - Spawna sub-agente com modelo pequeno (Haiku/qwen3.6-plus) com prompt do tipo:
     ```
     From the following user+assistant turn, identify 0-3 facts worth remembering across sessions.
     ONLY extract:
     - User preferences/workflow rules ("use pytest", "prefer X over Y")
     - Project-level state (deadlines, active refactors, incidents)
     - Feedback/corrections the user gave
     Do NOT extract:
     - Code patterns visible in the codebase
     - Ephemeral conversation state
     - Anything already in MEMORY.md

     Current MEMORY.md:
     <loaded index>

     Turn:
     <user message>
     <assistant response>

     Output a JSON list [{name, description, type, body}] or [] if nothing worth saving.
     ```
   - Fire-and-forget: `asyncio.create_task` pra não bloquear o próximo prompt do user
   - Escrever cada fact novo em `<project-memory>/<slug>.md` + apêndice em MEMORY.md

4. **Loading no startup:**
   - Em `aru/memory/loader.py`: `load_memory_index(project_root) -> str` lê MEMORY.md (primeira 150 linhas, como CC faz) e retorna texto formatado pra injeção.
   - Em `agents/base.py`: adicionar seção `## Project memory` ao `BASE_INSTRUCTIONS` quando existe MEMORY.md.

5. **CLI controls em `commands.py`:**
   ```
   /memory                      list current memories (cat MEMORY.md)
   /memory show <name>          cat specific memory file
   /memory delete <name>        remove a specific memory
   /memory clear                clear all memory for this project (with confirm)
   /memory extract-now          force extraction on last N turns ignoring min_turn_tokens
   ```

### Edge cases

- Extraction falha silently (model error, timeout) — NÃO bloqueia próximo turn do user. Logger warn no plugin-errors ring buffer.
- Duplicate extractions: extractor recebe MEMORY.md atual e é instruído a skip se já tem. Não é 100%, mas evita spam.
- Token cost: user pode ver via `/cost` — adiciono linha "memory extraction: X tokens, $Y" como sub-line.
- Memory corruption: se `YAML frontmatter` malformado, skip esse arquivo no load (com warn) — não crasha Aru.

### Testes

- `tests/test_memory.py`:
  - Write + read roundtrip de memory file com frontmatter
  - MEMORY.md index mantém uma linha por memory
  - `load_memory_index` corta em 150 linhas como cap
  - Extraction prompt retorna JSON vazio → nada escrito
  - Extraction prompt retorna 2 facts → 2 arquivos + 2 linhas no MEMORY.md
  - Deletar memory via `/memory delete` remove arquivo E linha do índex
  - Corruption: frontmatter malformado → skip com log
- Integration mock: hook `turn.end` dispara extractor; mock model retorna dict fixo; verify file written.

---

## 6. Stage 5 — LSP integration (~16–22h)

**Arquivos:**
- `aru/lsp/` (novo package)
  - `__init__.py`
  - `client.py` — LSP client genérico (stdio, JSON-RPC)
  - `manager.py` — spawn/track servers por linguagem
  - `protocol.py` — tipos mínimos (Position, Location, Hover, etc.)
- `aru/tools/lsp.py` (novo — 4 tools)
- `aru/tools/registry.py` (incluir em `ALL_TOOLS`)
- `aru/config.py` (config `lsp.<lang>.command`)
- `tests/test_lsp.py` (novo — com mock LSP server)

### Problema

Aru hoje entende código só via grep/read. Para:
- "Onde função X é chamada?" — grep encontra string matches; perde shadowing, overloads, imports indiretos
- "Qual o tipo dessa variável?" — não tem jeito
- "Rename classe Y pra Z em todos os arquivos" — grep+replace alucina em comments/strings, quebra imports

LSP resolve tudo isso com **informação semântica do compiler/parser oficial da linguagem**.

### Escopo Tier 2

1. **Linguagens:** Python (pylsp) + TypeScript (typescript-language-server). Outras via config mas não testadas.
2. **4 operações (tools):**
   - `lsp_definition(file, line, col)` → localização da definição
   - `lsp_references(file, line, col)` → todas as referências
   - `lsp_hover(file, line, col)` → docstring + tipo
   - `lsp_diagnostics(file)` → errors/warnings atuais

### Shape da solução

1. **Protocol layer (`lsp/protocol.py`):**
   - Minimal types: `Position`, `Range`, `Location`, `Hover`, `Diagnostic`
   - JSON-RPC message framing (Content-Length header + JSON body)

2. **Client (`lsp/client.py`):**
   - Async class wrapping stdio process — similar ao mcp_client mas mais simples
   - `initialize(root_uri)` → capabilities handshake
   - `textDocument/didOpen`, `textDocument/didChange` pra sincronizar estado
   - Request/response ID matching via `asyncio.Future` dict

3. **Manager (`lsp/manager.py`):**
   - Per-language singleton clients
   - Lazy spawn on first tool call
   - Health states análogos ao MCP (healthy/failed/unavailable) — reuso de pattern do Stage 5 Tier 1
   - Detecta linguagem por extension (`.py` → Python, `.ts`/`.tsx` → TS, etc.)

4. **Config em `aru.json`:**
   ```json
   {
     "lsp": {
       "python": { "command": "pylsp", "args": [] },
       "typescript": { "command": "typescript-language-server", "args": ["--stdio"] }
     }
   }
   ```
   Sem config → LSP desativado, tools retornam "LSP not configured — install pylsp and add to aru.json".

5. **4 tools em `tools/lsp.py`:**
   ```python
   async def lsp_definition(file_path: str, line: int, column: int) -> str:
       """Find the definition of the symbol at file:line:column (0-indexed)."""
       client = await _get_client_for(file_path)
       if client is None:
           return "[LSP not available for this file type.]"
       await _ensure_document_open(client, file_path)
       result = await client.request("textDocument/definition", {
           "textDocument": {"uri": _path_to_uri(file_path)},
           "position": {"line": line, "character": column},
       })
       return _format_locations(result)
   ```
   Similar para references, hover, diagnostics.

6. **File sync strategy:**
   - Minimal: `didOpen` em cada tool call; leave open after (LSP remembers). `didChange` quando Aru edita o arquivo (integrar via hook `file.changed` do Stage 3)
   - Garante que LSP server sempre tem view atual do código.

7. **Integração com apply_patch (Stage 2):**
   - Após apply_patch bem-sucedido, emit `file.changed` pra cada arquivo afetado → LSP manager recebe e faz `didChange` 
   - Opcional: retornar `lsp_diagnostics()` dos arquivos tocados como parte do summary do apply_patch

### Edge cases

- LSP server crash mid-session: marcar `unavailable`, próximo tool call tenta respawn (mesma lógica do MCP Stage 5 Tier 1)
- `pylsp` não instalado → tool retorna mensagem instructiva; não crasha
- Arquivo fora do workspace (`/tmp/something.py`) → LSP server pode rejeitar; tratar error graciosamente
- Large file: alguns LSP servers têm cap de tamanho; documentar
- Windows paths: `file_path_to_uri` precisa lidar com drive letters (`file:///D:/...`)

### Testes

- `tests/test_lsp.py`:
  - Mock LSP server (fixture que simula stdio JSON-RPC) responde `textDocument/definition` → tool retorna location formatada
  - Mock retorna `null` (no definition found) → tool retorna "No definition found"
  - Mock crasha → tool retorna erro limpo, manager marca `unavailable`
  - `didChange` emitido após `write_file` (mock do hook file.changed)
  - Tool call em `.rs` sem config LSP → "LSP not configured for rust"
- Smoke manual: rodar real `pylsp` em um projeto Python pequeno, verificar 4 tools retornam dados plausíveis.

---

## 7. Testes consolidados

Novos test files (5):

| Stage | Test file | Casos |
|:-:|---|:-:|
| 1 | `tests/test_worktree.py` | 6 |
| 2 | `tests/test_apply_patch.py` | 10 |
| 3 | `tests/test_plugin_hooks_v2.py` | 6 |
| 4 | `tests/test_memory.py` | 8 |
| 5 | `tests/test_lsp.py` | 6 |

Total: ~36 novos casos.

Smoke tests manuais (não substitui unit tests, só valida fim-a-fim):

1. **Worktree**: `/worktree create feat-x`, editar em duas worktrees, confirmar que branches são isoladas. Windows testado também.

2. **apply_patch**: agente recebe prompt "rename `foo` para `bar` em 5 arquivos" → agente emite patch — aplica atômico — `/undo` restaura tudo.

3. **Hooks**: criar plugin `.aru/plugins/audit.py` que subscreve `pre_tool_use` para logar toda call. Rodar 1 turn, conferir log.

4. **Auto-memory**: ativar config, rodar 3 turns onde user dá feedback ("always use type hints"), fechar aru, abrir nova sessão, conferir MEMORY.md + prompt tem a preferência injetada.

5. **LSP**: arquivo `.py` simples, agente chama `lsp_definition` em símbolo externo, retorna path da source lib.

---

## 8. Riscos

| # | Risco | Mitigação |
|:-:|---|---|
| R1 | Worktree com path relativo confunde tools que assumem cwd atual | `os.chdir` já é feito em `enter_worktree`; não há paths relativos hardcoded fora disso (verificar com grep antes do commit) |
| R2 | apply_patch parse crasha em edge case não previsto (multi-byte, trailing whitespace) | Parser tem fase de validação; erro retorna mensagem estruturada. Tests cobrem 10+ formatos |
| R3 | Hook `pre_tool_use` lento (plugin custom roda DB call) bloqueia cada tool → UX ruim | Documentar no `docs/plugin-hooks.md` que interceptors devem ser rápidos (<50ms); opcional: timeout via `asyncio.wait_for` igual Tier 1 Stage 1 |
| R4 | Auto-memory extraction spawn tokens sem controle do user | Default `auto_extract: false` (opt-in); contador em `/cost`; log de cada extração com $ custo |
| R5 | Auto-memory extrai ruído / false positives polui memory | Prompt engineering + cap hard 50 memories (oldest evicted); `/memory delete` manual; thresholds de min_turn_tokens |
| R6 | LSP server comum (pylsp) tem bugs / performance ruim | Isolar via health tracking reused do MCP; fallback gracious ("LSP unavailable, falling back to grep"); user vê `/lsp status` |
| R7 | LSP didChange desync quando tool edita arquivo sem emitir hook | Stage 3 `file.changed` hook é obrigatório; review no merge que todo write/edit/apply_patch emite o evento |
| R8 | Conflito entre worktree ativa e MCP server que assume `process.cwd()` | MCP servers herdam env mas path pode ficar errado — documentar em AGENTS.md que MCP servers são spawned no project root, não worktree |
| R9 | Windows path casing em LSP (`C:\foo` vs `c:/foo`) | Normalizar URIs; tests rodam em CI Windows |
| R10 | apply_patch + checkpoint race se user faz Ctrl+C mid-apply | Checkpoints são tracked per-turn já hoje; Ctrl+C durante apply deixa estado parcial mas `/undo` restaura (checkpoint captura antes de CADA op) |

---

## 9. Estrutura de entrega (branch + commits)

Implementação integral numa branch única (`feat/tier2-product`), com 5 commits cirúrgicos (mesma filosofia do Tier 1). Cada commit = 1 stage completo + testes.

Ordem dos commits:

1. `feat(tools): worktree primitive` (Stage 1)
2. `feat(tools): atomic apply_patch` (Stage 2)
3. `feat(plugins): expanded hooks lifecycle` (Stage 3)
4. `feat(memory): auto-extraction from turns` (Stage 4)
5. `feat(lsp): Python + TypeScript language server integration` (Stage 5)

Rollback: `git revert <hash>` de qualquer commit individual. Stage 5 pode ser revertido sem afetar 1-4. Stage 4 depende semanticamente de 3 (hook `turn.end`) mas não sintaticamente — é safe-to-revert sozinho (vira no-op silenciosa).

## 10. Checklist de merge

- [ ] Stage 1 — `/worktree create/enter/exit/remove/list` + testes verdes + status bar
- [ ] Stage 2 — `apply_patch` tool em `ALL_TOOLS` + prompt doc + rollback via checkpoint + testes verdes
- [ ] Stage 3 — 15 novos eventos em `VALID_HOOKS` + emissão em todos call sites + `docs/plugin-hooks.md` + testes verdes
- [ ] Stage 4 — `aru/memory/` package + config opt-in + `/memory` commands + hook `turn.end` integrado + testes verdes
- [ ] Stage 5 — `aru/lsp/` package + 4 tools + health tracking + config doc em AGENTS.md + testes verdes
- [ ] `rg "VALID_HOOKS" aru/` — todos os dispatch sites usam nomes do enum (sem string literal fora-da-lista)
- [ ] Smoke manual dos 5 cenários passa
- [ ] AGENTS.md atualizado (worktree, apply_patch format, hooks reference pointer, memory config, lsp config)
- [ ] `docs/plugin-hooks.md` criado com tabela dos 15 eventos novos + exemplos
- [ ] Versão bump `0.34.0 -> 0.35.0` em `pyproject.toml` + `aru/__init__.py`

---

## 11. Pós-Tier-2 (futuro, NÃO neste plano)

Fica mapeado para não esquecer:

- **Cwd-aware tools refactor** (worktree Scope C) — `delegate_task(worktree=...)` com sub-agente em worktree própria. Refactor de file_ops/search/shell pra aceitar cwd explícito.
- **Format integration** — detecta prettier/black/rustfmt, roda pós-apply_patch. Fácil depois do hook `file.changed`.
- **LSP actions / code lens** — rename symbol, extract method, etc.
- **Multi-project memory** — memories compartilhados entre projetos (opt-in).
- **Memory search / query tool** — agente pode buscar em memories via tool dedicada em vez de só ler MEMORY.md inteiro.
