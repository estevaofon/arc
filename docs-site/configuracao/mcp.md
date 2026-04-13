---
title: MCP Servers
description: Integração com servidores Model Context Protocol
---

# MCP Servers

O Aru suporta [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) — um padrão aberto para conectar LLMs a ferramentas externas. Você pode carregar servidores MCP e suas ferramentas ficam disponíveis para os agentes como se fossem nativas.

## Configuração

Crie um arquivo `.aru/mcp_config.json` no seu projeto:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/allowed/path"]
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..."
      }
    }
  }
}
```

Cada entrada em `mcpServers` é um servidor MCP. O Aru sobe o processo, negocia as capabilities e registra todas as ferramentas expostas.

## Campos

| Campo | Obrigatório | Descrição |
|-------|-------------|-----------|
| `command` | Sim | Executável do servidor (ex: `npx`, `python`, `node`) |
| `args` | Sim | Lista de argumentos passados ao comando |
| `env` | Não | Variáveis de ambiente injetadas no processo |

## Verificando servidores ativos

Dentro da REPL:

```text
aru> /mcp
```

Lista todos os servidores conectados e as ferramentas que cada um expõe, com descrição e parâmetros.

## Servidores populares

Alguns servidores MCP que você pode querer experimentar:

- **`@modelcontextprotocol/server-filesystem`** — acesso a diretórios específicos
- **`@modelcontextprotocol/server-github`** — operações no GitHub (issues, PRs, repos)
- **`@modelcontextprotocol/server-slack`** — leitura e envio de mensagens no Slack
- **`@modelcontextprotocol/server-postgres`** — queries somente-leitura em Postgres
- **`@modelcontextprotocol/server-puppeteer`** — automação de browser

Veja o [catálogo oficial](https://github.com/modelcontextprotocol/servers) para a lista completa.

## Permissões e MCP

Ferramentas MCP passam pelo mesmo sistema de permissões das ferramentas nativas. Você pode configurá-las por nome no `aru.json`:

```json
{
  "permission": {
    "mcp_github_create_issue": "ask",
    "mcp_filesystem_*": "allow"
  }
}
```

!!! warning "Cuidado com servidores MCP não confiáveis"
    Servidores MCP rodam como processos locais com as mesmas permissões do seu usuário. Só carregue servidores de fontes confiáveis, principalmente os que acessam tokens ou dados sensíveis.
