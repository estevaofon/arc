# arc 🤖

An intelligent coding assistant powered by Claude and Agno agents.

## 📋 About

**arc** is an AI coding assistant that uses multi-agent architecture to help with software engineering tasks. Built with [Agno](https://github.com/agno-agi/agno) and [Anthropic's Claude](https://www.anthropic.com/), it provides an interactive CLI for code manipulation, project analysis, and task automation.

### Specialized Agents

- **Planner** 🧠 — Analyzes code and creates detailed implementation plans
- **Executor** ⚡ — Executes code changes based on plans or direct instructions  
- **Arc (General)** 💬 — General-purpose agent for conversational tasks

## ✨ Features

- 📁 **File Operations** — Read, write, edit, and batch file updates
- 🔍 **Code Search** — Glob patterns and regex search across the codebase
- 🏃 **Shell Execution** — Run commands directly from the CLI
- 🗂️ **Directory Navigation** — Browse and analyze project structure
- 💬 **Smart Conversation** — Context-aware chat with history
- 📋 **Multi-line Paste** — Paste code blocks with automatic detection
- 🎯 **Task Planning** — Break down complex tasks into actionable steps
- ⚡ **Automated Execution** — Execute plans step by step
- 🔄 **Model Switching** — Toggle between Sonnet, Opus, and Haiku models

## 🚀 Installation

### Prerequisites

- Python 3.13 or higher
- Anthropic API key ([get one here](https://console.anthropic.com/))

### Quick Start

1. **Clone and navigate to the repository:**
```bash
git clone <repository-url>
cd arc
```

2. **Install using pip or uv:**
```bash
pip install -e .
# or with uv (recommended)
uv pip install -e .
```

3. **Configure your API key:**
```bash
cp .env.example .env
```

Edit `.env` and add your Anthropic API key:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

4. **Launch arc:**
```bash
arc
```

## 🎮 Usage

### Interactive CLI

Simply run `arc` to start the interactive session:

```bash
arc
```

The CLI supports **multi-line paste detection** — paste code freely, add a comment about it, and press Enter.

### Available Commands

| Command | Description |
|---------|-------------|
| **Natural language** | Just type normally — arc decides how to help |
| `/plan <task>` | Create a detailed implementation plan |
| `/exec [task]` | Execute the current plan or a specific task |
| `/model [name]` | Switch between models (sonnet, opus, haiku) |
| `! <command>` | Run a shell command directly |
| `/quit` or `/exit` | Exit arc |

### Available Models

- **sonnet** (default) — `claude-sonnet-4-5-20250929` — Best balance of speed and capability
- **opus** — `claude-opus-4-20250514` — Most capable, slower and more expensive
- **haiku** — `claude-haiku-3-5-20241022` — Fastest and most cost-effective

### Usage Examples

**Planning a feature:**
```
arc> /plan create a REST API with FastAPI to manage users with CRUD operations
```

The Planner will:
1. Analyze your project structure
2. Search for relevant patterns  
3. Create a step-by-step implementation plan
4. Ask if you want to execute it

**Executing tasks:**
```
arc> /exec
```
Executes the current plan step by step.

```
arc> /exec add input validation to the create_user endpoint
```
Executes a specific task directly.

**Running shell commands:**
```
arc> ! pytest tests/ -v
```

**Switching models:**
```
arc> /model opus
```

**Natural conversation:**
```
arc> refactor the authentication module to use JWT tokens
```

arc will analyze the request and execute it directly.

**Pasting code:**
1. Paste a multi-line code block (automatically detected)
2. Type a message like "optimize this function"
3. Press Enter

## 🏗️ Project Structure

```
arc/
├── arc/
│   ├── agents/
│   │   ├── planner.py      # Planning agent
│   │   └── executor.py     # Execution agent
│   ├── tools/
│   │   └── codebase.py     # File and codebase tools
│   ├── cli.py              # Interactive CLI with streaming
│   └── __init__.py
├── main.py                 # Entry point
├── pyproject.toml          # Project config & dependencies
├── .env.example            # Environment template
└── .gitignore
```

## 🛠️ Technologies

- **[Agno](https://github.com/agno-agi/agno)** — Agent framework with tool orchestration
- **[Anthropic Claude](https://www.anthropic.com/)** — LLM (Sonnet, Opus, Haiku)
- **[Rich](https://rich.readthedocs.io/)** — Beautiful terminal UI with markdown rendering
- **[prompt-toolkit](https://python-prompt-toolkit.readthedocs.io/)** — Advanced input with paste detection
- **[SQLAlchemy](https://www.sqlalchemy.org/)** — ORM for agent state persistence
- **[python-dotenv](https://pypi.org/project/python-dotenv/)** — Environment variable management

## 🔧 Configuration

arc uses the following environment variables:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Optional
DEFAULT_MODEL=sonnet  # sonnet, opus, or haiku
```

## 💡 Tips & Best Practices

- **Use `/plan` for complex tasks** — Let the Planner break down multi-step work
- **Use natural language for simple tasks** — The general agent can handle direct edits
- **Paste code freely** — Multi-line paste is detected automatically
- **Review plans before execution** — arc will ask for confirmation
- **Switch models based on needs** — Use Haiku for simple tasks, Opus for complex reasoning
- **Leverage conversation history** — arc remembers the last 40 messages
- **Use shell commands** — Run tests, git operations, etc. with `! <command>`

## 🔒 Security

- **Never commit `.env`** — It's already in `.gitignore`
- **Keep your API key private** — Don't share it or paste it in code
- **Review shell commands** — arc can execute arbitrary commands
- **Inspect file changes** — Especially when using `/exec` on unfamiliar code

## 🐛 Known Limitations

- Conversation history is limited to 40 messages (10 shown in context)
- Shell commands execute in the current working directory
- Large file operations may hit token limits
- Paste detection requires terminal support for bracketed paste mode

## 📚 Learn More

- [Agno Documentation](https://docs.agno.dev/)
- [Anthropic API Docs](https://docs.anthropic.com/)
- [Claude Models Guide](https://www.anthropic.com/claude)
- [prompt-toolkit Documentation](https://python-prompt-toolkit.readthedocs.io/)

## 🤝 Contributing

Contributions are welcome! To contribute:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes and test thoroughly
4. Commit with clear messages: `git commit -m 'Add amazing feature'`
5. Push to your fork: `git push origin feature/amazing-feature`
6. Open a Pull Request

## 📄 License

This project is an educational implementation for learning about AI agents and coding assistants.

## 📧 Support

For questions, bug reports, or feature requests, please open an issue in the repository.

---

Built with ❤️ using Claude and Agno