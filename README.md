# arc

Um assistente de codificação inteligente powered by Claude e Agno agents.

## Destaques

- **Arquitetura Multi-Agente** — Agentes especializados para planejamento, execução e conversação
- **CLI Interativa** — Respostas em streaming, paste multi-linha, gerenciamento de sessões
- **16 Ferramentas Integradas** — Operações de arquivo, busca de código, shell, busca web, busca semântica, delegação de tarefas
- **Planejamento de Tarefas** — Quebra de tarefas complexas em etapas com execução automática
- **Flexibilidade de Modelos** — Alterne entre Sonnet (balanceado), Opus (poderoso) e Haiku (rápido)
- **Busca Semântica** — Busca de código baseada em embeddings com chromadb
- **Comandos e Skills Personalizados** — Estenda arc via diretório `.agents/`
- **Suporte MCP** — Integração com Model Context Protocol servers

## Início Rápido

```bash
# Instalar
pip install -e .

# Configurar
cp .env.example .env
# Edite .env e adicione: ANTHROPIC_API_KEY=sk-ant-sua-chave-aqui

# Executar
arc
```

**Requisitos:** Python 3.13+ e uma [chave de API da Anthropic](https://console.anthropic.com/)

## Uso

### Comandos

| Comando | Descrição |
|---------|-----------|
| Linguagem natural | Apenas digite — arc cuida do resto |
| `/plan <tarefa>` | Cria plano de implementação detalhado |
| `/model [nome]` | Alterna modelos (sonnet/opus/haiku) |
| `/mcp` | Lista servidores e ferramentas MCP disponíveis |
| `/commands` | Lista comandos personalizados |
| `/skills` | Lista skills disponíveis |
| `/sessions` | Lista sessões recentes |
| `/help` | Mostra todos os comandos |
| `! <comando>` | Executa comandos shell |
| `/quit` ou `/exit` | Sai do arc |

### Opções CLI

```bash
arc                                    # Inicia nova sessão
arc --resume <id>                      # Retoma sessão
arc --resume last                      # Retoma última sessão
arc --list                             # Lista sessões
arc --dangerously-skip-permissions     # Pula prompts de permissão
```

### Exemplos

```
arc> /plan criar uma REST API com FastAPI para gerenciar usuários

arc> refatorar o módulo de autenticação para usar tokens JWT

arc> ! pytest tests/ -v

arc> /model opus
```

## Agentes

| Agente | Papel | Ferramentas |
|--------|-------|-------------|
| **Planner** | Analisa codebase, cria planos de implementação estruturados | Ferramentas somente leitura, busca, web |
| **Executor** | Implementa mudanças de código baseadas em planos ou instruções | Todas as ferramentas incluindo delegação |
| **General** | Lida com conversação e operações simples | Todas as ferramentas incluindo delegação |

## Ferramentas

### Operações de Arquivo
- `read_file` — Lê arquivos com suporte a range de linhas e detecção binária
- `write_file` / `write_files` — Escreve arquivos únicos ou em lote
- `edit_file` / `edit_files` — Edições find-replace em múltiplos arquivos

### Busca & Descoberta
- `glob_search` — Encontra arquivos por padrão (respeita .gitignore)
- `grep_search` — Busca de conteúdo com regex e filtro de arquivos
- `list_directory` — Listagem de diretório com filtro gitignore
- `semantic_search` — Busca conceitual em linguagem natural via embeddings chromadb
- `rank_files` — Ranking de relevância de arquivos multi-fator (semântico, nome, estrutura, recência)

### Análise de Código
- `code_structure` — Extrai classes, funções, imports via AST tree-sitter
- `find_dependencies` — Analisa relacionamentos de imports entre arquivos

### Shell & Web
- `bash` — Executa comandos shell com gates de permissão
- `web_search` — Busca na web via DuckDuckGo
- `web_fetch` — Busca URLs e converte HTML para texto legível

### Avançado
- `delegate_task` — Gera sub-agentes autônomos para execução paralela de tarefas

## Configuração

Arc suporta configuração a nível de projeto através de:

### AGENTS.md
Coloque um arquivo `AGENTS.md` na raiz do seu projeto com instruções personalizadas que serão anexadas a todos os prompts do sistema dos agentes.

### Diretório .agents/

```
.agents/
├── commands/       # Comandos slash personalizados (nome do arquivo = nome do comando)
│   └── deploy.md   # Uso: /deploy <args>
└── skills/         # Skills/personas personalizadas
    └── review.md   # Carregado como instruções adicionais do agente
```

Arquivos de comando suportam frontmatter com `description` e a variável template `$INPUT` para argumentos.

### Suporte MCP (Model Context Protocol)

Arc pode carregar ferramentas de servidores MCP. Configure em `.arc/mcp_config.json`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/caminho/permitido"]
    }
  }
}
```

## Arquitetura

```
arc/
├── arc/
│   ├── cli.py              # CLI interativa com display em streaming (1306 LOC)
│   ├── config.py           # Carregador de configuração (AGENTS.md, .agents/)
│   ├── agents/
│   │   ├── planner.py      # Agente de planejamento (60 LOC)
│   │   └── executor.py     # Agente de execução (57 LOC)
│   └── tools/
│       ├── codebase.py     # 16 ferramentas principais (1043 LOC)
│       ├── ast_tools.py    # Análise de código tree-sitter (402 LOC)
│       ├── indexer.py      # Indexação semântica chromadb (332 LOC)
│       ├── ranker.py       # Ranking de relevância de arquivos (280 LOC)
│       ├── mcp_client.py   # Cliente MCP (145 LOC)
│       └── gitignore.py    # Filtro gitignore-aware (104 LOC)
├── .arc/                   # Dados locais (sessões, índice, embeddings)
└── pyproject.toml
```

## Construído Com

- **[Agno](https://github.com/agno-agi/agno)** — Framework de agentes com orquestração de ferramentas
- **[Anthropic Claude](https://www.anthropic.com/)** — Sonnet 4.5, Opus 4, Haiku 3.5
- **[chromadb](https://www.trychroma.com/)** — Embeddings de busca semântica
- **[tree-sitter](https://tree-sitter.github.io/)** — Análise de código baseada em AST
- **[Rich](https://rich.readthedocs.io/)** — UI de terminal
- **[prompt-toolkit](https://python-prompt-toolkit.readthedocs.io/)** — Manipulação avançada de input

## Desenvolvimento

```bash
# Instalar com dependências de desenvolvimento
pip install -e ".[dev]"

# Executar testes
pytest

# Executar testes com cobertura
pytest --cov=arc --cov-report=html
```

---

Construído com Claude e Agno