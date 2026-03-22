# arc

An intelligent coding assistant powered by Claude and Agno agents.

## Highlights

- **Multi-Agent Architecture** — Specialized agents for planning, execution, and conversation
- **Interactive CLI** — Streaming responses, multi-line paste, session management
- **16 Integrated Tools** — File ops, code search, shell, web search, semantic search, task delegation
- **Task Planning** — Break down complex tasks into steps with automatic execution
- **Model Flexibility** — Switch between Sonnet (balanced), Opus (powerful), and Haiku (fast)
- **Semantic Search** — Embedding-based code search with chromadb
- **Custom Commands & Skills** — Extend arc via `.agents/` directory

## Quick Start

```bash
# Install
pip install -e .

# Configure
cp .env.example .env
# Edit .env and add: ANTHROPIC_API_KEY=sk-ant-your-key-here

# Run
arc
```

**Requirements:** Python 3.13+ and an [Anthropic API key](https://console.anthropic.com/)

## Usage

### Commands

| Command | Description |
|---------|-------------|
| Natural language | Just type — arc handles it |
| `/plan <task>` | Create detailed implementation plan |
| `/model [name]` | Switch models (sonnet/opus/haiku) |
| `/commands` | List custom commands |
| `/skills` | List available skills |
| `/sessions` | List recent sessions |
| `! <command>` | Run shell commands |
| `/quit` or `/exit` | Exit arc |

### CLI Options

```bash
arc                                    # Start new session
arc --resume <id>                      # Resume session
arc --resume last                      # Resume last session
arc --list                             # List sessions
arc --dangerously-skip-permissions     # Skip permission prompts
```

### Examples

```
arc> /plan create a REST API with FastAPI to manage users

arc> refactor the authentication module to use JWT tokens

arc> ! pytest tests/ -v

arc> /model opus
```

## Agents

| Agent | Role | Tools |
|-------|------|-------|
| **Planner** | Analyzes codebase, creates structured implementation plans | Read-only tools, search, web |
| **Executor** | Implements code changes based on plans or instructions | All tools including delegation |
| **General** | Handles conversation and simple operations | All tools including delegation |

## Tools

### File Operations
- `read_file` — Read files with line-range support and binary detection
- `write_file` / `write_files` — Write single or batch files
- `edit_file` / `edit_files` — Find-replace edits across files

### Search & Discovery
- `glob_search` — Find files by pattern (respects .gitignore)
- `grep_search` — Regex content search with file filtering
- `list_directory` — Directory listing with gitignore filtering
- `semantic_search` — Natural language concept search via chromadb embeddings
- `rank_files` — Multi-factor file relevance ranking (semantic, name, structure, recency)

### Code Analysis
- `code_structure` — Extract classes, functions, imports via tree-sitter AST
- `find_dependencies` — Analyze import relationships between files

### Shell & Web
- `bash` / `run_command` — Execute shell commands with permission gates
- `web_search` — Search the web via DuckDuckGo
- `web_fetch` — Fetch URLs and convert HTML to readable text

### Advanced
- `delegate_task` — Spawn autonomous sub-agents for parallel task execution

## Configuration

Arc supports project-level configuration through:

### AGENTS.md
Place an `AGENTS.md` file in your project root with custom instructions that get appended to all agent system prompts.

### .agents/ Directory

```
.agents/
├── commands/       # Custom slash commands (filename = command name)
│   └── deploy.md   # Usage: /deploy <args>
└── skills/         # Custom skills/personas
    └── review.md   # Loaded as additional agent instructions
```

Command files support frontmatter with `description` and the `$INPUT` template variable for arguments.

## Architecture

```
arc/
├── arc/
│   ├── cli.py              # Interactive CLI with streaming display
│   ├── config.py           # Configuration loader (AGENTS.md, .agents/)
│   ├── agents/
│   │   ├── planner.py      # Planning agent
│   │   └── executor.py     # Execution agent
│   └── tools/
│       ├── codebase.py     # 16 core tools
│       ├── ast_tools.py    # Tree-sitter code analysis
│       ├── indexer.py      # Chromadb semantic indexing
│       ├── ranker.py       # File relevance ranking
│       └── gitignore.py    # Gitignore-aware filtering
├── .arc/                   # Local data (sessions, index, embeddings)
└── pyproject.toml
```

## Built With

- **[Agno](https://github.com/agno-agi/agno)** — Agent framework with tool orchestration
- **[Anthropic Claude](https://www.anthropic.com/)** — Sonnet 4.5, Opus 4, Haiku 3.5
- **[chromadb](https://www.trychroma.com/)** — Semantic search embeddings
- **[tree-sitter](https://tree-sitter.github.io/)** — AST-based code analysis
- **[Rich](https://rich.readthedocs.io/)** — Terminal UI
- **[prompt-toolkit](https://python-prompt-toolkit.readthedocs.io/)** — Advanced input handling

---

Built with Claude and Agno
