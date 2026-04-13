---
title: Permissões
description: Sistema granular de allow/ask/deny para controlar ferramentas
---

# Permissões

O Aru usa um sistema de permissões granular onde cada ação de ferramenta resolve para um dos três resultados:

- **`allow`** — executa sem perguntar
- **`ask`** — pede confirmação (uma vez / sempre / não)
- **`deny`** — bloqueia silenciosamente

## Configuração básica

Configure permissões por categoria de ferramenta com glob patterns:

```json
{
  "permission": {
    "*": "ask",
    "read": "allow",
    "glob": "allow",
    "grep": "allow",
    "list": "allow",
    "edit": {
      "*": "allow",
      "*.env": "deny"
    },
    "write": {
      "*": "allow",
      "*.env": "deny"
    },
    "bash": {
      "*": "ask",
      "git *": "allow",
      "npm *": "allow",
      "pytest *": "allow",
      "rm -rf *": "deny"
    },
    "web_search": "allow",
    "web_fetch": "allow",
    "delegate_task": "allow"
  }
}
```

## Categorias disponíveis

| Categoria | Casado contra | Padrão |
|-----------|---------------|--------|
| `read` | caminho do arquivo | `allow` |
| `edit` | caminho do arquivo | `ask` |
| `write` | caminho do arquivo | `ask` |
| `bash` | string do comando | prefixos seguros = `allow`, resto = `ask` |
| `glob` | — | `allow` |
| `grep` | — | `allow` |
| `list` | — | `allow` |
| `web_search` | — | `allow` |
| `web_fetch` | URL | `allow` |
| `delegate_task` | — | `allow` |

## Precedência de regras

As regras usam ordenação **last-match-wins**. Coloque o catch-all `"*"` primeiro e padrões específicos depois:

```json
{
  "edit": {
    "*": "allow",
    "*.env": "deny",
    "*.env.example": "allow"
  }
}
```

Neste exemplo, todos os arquivos podem ser editados, exceto `.env`, com `.env.example` liberado como exceção.

## Shorthands

Permitir tudo (equivalente a `--dangerously-skip-permissions`):

```json
{ "permission": "allow" }
```

Valor string aplicado a todos os padrões da categoria:

```json
{ "permission": { "read": "allow", "edit": "ask" } }
```

## Padrões seguros

Sem nenhuma configuração em `aru.json`, o Aru aplica padrões seguros:

- **Ferramentas read-only** (`read`, `glob`, `grep`, `list`) → `allow`
- **Ferramentas mutantes** (`edit`, `write`) → `ask`
- **Bash** → ~40 prefixos seguros auto-permitidos (`ls`, `git status`, `grep`, etc.), resto → `ask`
- **Arquivos sensíveis** (`*.env`, `*.env.*`) → `deny` para read/edit/write (exceto `*.env.example`)

## Pulando permissões (uso arriscado)

Em ambientes controlados (CI, sandbox, worktree), você pode pular completamente o sistema de permissões:

```bash
aru --dangerously-skip-permissions
```

!!! warning "Cuidado"
    Esta flag autoriza qualquer ação, incluindo `rm -rf`, escrita em arquivos sensíveis e comandos shell arbitrários. Use apenas em ambientes isolados.

## Memória de "sempre"

Quando você escolhe "sempre" em um prompt de permissão, a decisão é lembrada apenas dentro da sessão atual. Agentes customizados têm sua própria memória isolada — aprovações dentro de um agente não vazam para o escopo global.
