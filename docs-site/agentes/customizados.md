---
title: Agentes Customizados
description: Como criar agentes com prompt, modelo e ferramentas próprias
---

# Agentes Customizados

Agentes customizados são arquivos Markdown com frontmatter YAML salvos em `.agents/agents/`. Cada agente roda com seu próprio system prompt, modelo e conjunto de ferramentas — diferente de comandos e skills, que reutilizam o General Agent.

## Exemplo

```markdown
---
name: Code Reviewer
description: Review code for quality, bugs, and best practices
model: anthropic/claude-sonnet-4-5
tools: read_file, grep_search, glob_search
max_turns: 15
mode: primary
---

You are an expert code reviewer. Analyze code for bugs, security,
performance, and readability. Do NOT modify files.
```

Salve como `.agents/agents/reviewer.md` e invoque com `/reviewer src/auth.py` ou `@reviewer ...`.

## Campos do frontmatter

| Campo | Obrigatório | Descrição |
|-------|-------------|-----------|
| `name` | Sim | Nome de exibição do agente |
| `description` | Sim | Quando usar (mostrado em `/agents` e no autocomplete) |
| `model` | Não | Referência provider/model (ex: `anthropic/claude-sonnet-4-5`). Padrão: modelo da sessão |
| `tools` | Não | Lista separada por vírgula (allowlist) ou objeto JSON (ex: `{"bash": false}`). Padrão: todas |
| `max_turns` | Não | Máximo de chamadas de ferramenta antes do agente parar. Padrão: 20 |
| `mode` | Não | `primary` (invocável via `/name`) ou `subagent` (só via `delegate_task`). Padrão: `primary` |
| `permission` | Não | Overrides de permissão (mesmo formato do `aru.json`) |

## Invocação

Três formas de invocar um agente customizado:

| Método | Sintaxe | Quando usar |
|--------|---------|-------------|
| **Slash command** | `/reviewer src/auth.py` | Invocação direta de agente `primary` |
| **@mention** | `@reviewer check this function` | Mencionar em qualquer lugar da mensagem |
| **delegate_task** | Automático (só subagents) | O LLM decide quando delegar |

```text
aru> /reviewer src/auth.py           # slash command (primary agents)
aru> @reviewer check the auth module  # @mention (primary ou subagent)
aru> /agents                          # lista todos os agentes customizados
```

!!! note "Slash commands só funcionam em primary agents"
    Subagents retornam um warning se invocados via `/`. Use `@name` ou deixe o LLM decidir via `delegate_task`.

## Discovery

Agentes são descobertos de múltiplos locais (os últimos sobrescrevem os primeiros):

1. `~/.agents/agents/` — global (disponível em todos os projetos)
2. `~/.claude/agents/` — global (compatível com Claude Code)
3. `.agents/agents/` — project-local
4. `.claude/agents/` — project-local

## Permissões por agente

Agentes podem sobrescrever regras globais de permissão. Os overrides substituem a categoria inteira — categorias não especificadas herdam do config global.

```markdown
---
name: Code Reviewer
description: Read-only code reviewer
permission:
  edit: deny
  write: deny
  bash:
    git diff *: allow
    grep *: allow
---
```

Você também pode definir permissões por agente no `aru.json` (sobrescreve o frontmatter):

```json
{
  "agent": {
    "reviewer": {
      "permission": { "edit": "deny", "write": "deny" }
    }
  }
}
```

Cada agente tem sua própria memória de "sempre" isolada — aprovações durante a execução do agente não carregam para o escopo global.

## Modo subagent

Agentes com `mode: subagent` podem ser chamados pelo LLM via `delegate_task(task, agent="name")`, mas não são invocáveis diretamente via `/name`. O nome e a descrição são injetados no tool description de `delegate_task`, então o LLM os descobre e usa quando apropriado.

Isso é útil para agentes especializados que você quer disponibilizar como "capacidades" do General Agent sem poluir o namespace de slash commands.
