---
title: Ferramentas Integradas
description: As 11 ferramentas nativas disponíveis para os agentes do Aru
---

# Ferramentas Integradas

O Aru vem com 11 ferramentas nativas organizadas em quatro categorias. Cada ferramenta passa pelo sistema de [permissões](../configuracao/permissoes.md) antes de executar.

## Operações de arquivo

### `read_file`

Lê arquivos com suporte a range de linhas e detecção de binários. Arquivos grandes são truncados a ~30 KB para proteger o contexto.

**Parâmetros:** `path`, `offset`, `limit`

### `read_files`

Versão em batch do `read_file` — lê múltiplos arquivos em uma única chamada paralela. Útil quando o agente precisa inspecionar vários arquivos relacionados sem pagar round-trips por cada um.

### `write_file`

Escreve conteúdo em um arquivo, criando diretórios conforme necessário. Sempre passa pelo gate de permissão `write`.

**Parâmetros:** `path`, `content`

### `edit_file`

Edição find-and-replace em arquivos existentes. Falha se o texto a ser substituído não for único, o que força o agente a fornecer contexto suficiente.

**Parâmetros:** `path`, `old_string`, `new_string`, `replace_all`

## Busca e descoberta

### `glob_search`

Encontra arquivos por padrão glob, respeitando `.gitignore`. Resultados ordenados por data de modificação.

**Exemplos:** `src/**/*.ts`, `tests/test_*.py`

### `grep_search`

Busca por conteúdo em arquivos usando ripgrep. Suporta regex, filtros por tipo de arquivo e modos de output (linhas, só nomes, contagem).

### `list_directory`

Lista o conteúdo de um diretório, filtrado pelo `.gitignore`. Útil pra o agente entender a estrutura antes de mergulhar em arquivos específicos.

### `rank_files`

Ranqueia arquivos por relevância usando um score multi-fator:

```text
score = 0.50 × name_match + 0.30 × structural + 0.20 × recency
```

Útil quando o agente precisa de uma lista curta dos arquivos mais prováveis de serem relevantes para uma tarefa.

## Shell e web

### `bash`

Executa comandos shell com gate de permissão. Output truncado a 10 KB. Prefixos seguros (leitura, git status, grep, pytest, etc.) são auto-permitidos por padrão.

### `web_search`

Busca na web via DuckDuckGo. Retorna título, URL e snippet dos resultados.

### `web_fetch`

Baixa uma URL e converte o HTML em texto legível. Útil para ler documentação, issues do GitHub, blog posts, etc.

## Avançado

### `delegate_task`

Cria sub-agentes autônomos para paralelizar trabalho. Cada sub-agente tem janela de contexto própria e retorna só o resultado final. Permite registrar agentes customizados no modo `subagent` como opções de delegação.

**Parâmetros:** `task`, `agent` (opcional), `files` (opcional)

## Truncamento e limites

- **Shell output:** 10 KB (excesso é cortado)
- **Leitura de arquivo:** 30 KB
- **Busca:** paginada por ripgrep, sem limite rígido

## Estendendo

Você pode adicionar suas próprias ferramentas via:

- **[Ferramentas customizadas](customizadas.md)** — arquivos Python em `.aru/tools/`
- **[Plugins](../plugins/index.md)** — sistema completo de hooks com registro de tools
- **[MCP](../configuracao/mcp.md)** — servidores MCP externos
