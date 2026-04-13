---
title: Configuração
description: Como configurar o Aru via aru.json e ~/.aru/config.json
---

# Configuração

O Aru lê configuração de dois níveis, com as configurações de projeto sobrescrevendo as globais:

| Nível | Caminho | Propósito |
|-------|---------|-----------|
| **Global (usuário)** | `~/.aru/config.json` | Padrões para todos os projetos (modelo, aliases, permissões, providers) |
| **Projeto** | `aru.json` ou `.aru/config.json` | Configurações específicas do projeto |

A configuração global é carregada primeiro, e a configuração de projeto é **mesclada profundamente** por cima — valores escalares e listas são substituídos, objetos aninhados (como `permission`, `providers`, `model_aliases`) são mesclados recursivamente.

## Exemplo global (`~/.aru/config.json`)

```json
{
  "default_model": "anthropic/claude-sonnet-4-6",
  "model_aliases": {
    "sonnet": "anthropic/claude-sonnet-4-6",
    "opus": "anthropic/claude-opus-4-6"
  },
  "permission": {
    "read": "allow",
    "glob": "allow",
    "grep": "allow"
  }
}
```

## Exemplo de projeto (`aru.json`)

```json
{
  "default_model": "ollama/codellama",
  "permission": {
    "bash": { "pytest *": "allow" }
  }
}
```

O resultado: `default_model` vira `ollama/codellama`, `model_aliases` vêm do global, e `permission` mescla os dois níveis.

## Campos principais

| Campo | Descrição |
|-------|-----------|
| `default_model` | Modelo padrão no formato `provider/model` |
| `model_aliases` | Atalhos para `/model <alias>` |
| `providers` | Configuração de providers customizados (tokens, endpoints) |
| `permission` | Regras de permissão por ferramenta |
| `instructions` | Arquivos/URLs com regras extras anexadas ao system prompt |
| `plugins` | Lista explícita de plugins a carregar |
| `agent` | Overrides por agente customizado |

## Páginas relacionadas

- [Modelos e Providers](modelos.md) — Como configurar cada provider
- [Permissões](permissoes.md) — Sistema granular de allow/ask/deny
- [Regras (AGENTS.md)](regras.md) — Instruções de projeto anexadas aos prompts
- [MCP Servers](mcp.md) — Integração com Model Context Protocol
