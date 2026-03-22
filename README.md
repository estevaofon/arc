# arc 🤖

An intelligent coding assistant powered by Claude and Agno agents.

## ✨ Highlights

- **🧠 Multi-Agent Architecture** — Specialized agents for planning, execution, and conversation
- **⚡ Interactive CLI** — Smart terminal with streaming responses and multi-line paste support
- **🔄 Model Flexibility** — Switch between Sonnet (balanced), Opus (powerful), and Haiku (fast)
- **🛠️ Complete Toolkit** — File operations, code search, shell execution, and project navigation
- **📋 Task Planning** — Break down complex tasks into actionable steps with automatic execution

## 🚀 Quick Start

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

## 🎮 Usage

### Commands

| Command | Description |
|---------|-------------|
| **Natural language** | Just type normally — arc handles it |
| `/plan <task>` | Create detailed implementation plan |
| `/exec [task]` | Execute plan or specific task |
| `/model [name]` | Switch models (sonnet/opus/haiku) |
| `! <command>` | Run shell commands |
| `/quit` or `/exit` | Exit arc |

### Examples

**Planning & execution:**
```
arc> /plan create a REST API with FastAPI to manage users
arc> /exec
```

**Direct task:**
```
arc> refactor the authentication module to use JWT tokens
```

**Shell commands:**
```
arc> ! pytest tests/ -v
```

**Code paste:**
1. Paste your code (auto-detected)
2. Add comment: "optimize this function"
3. Press Enter

## 🧠 Specialized Agents

- **Planner** — Analyzes codebase and creates detailed implementation plans
- **Executor** — Executes code changes based on plans or direct instructions
- **Arc (General)** — Handles conversational tasks and simple operations

## 🛠️ Built With

- **[Agno](https://github.com/agno-agi/agno)** — Agent framework with tool orchestration
- **[Anthropic Claude](https://www.anthropic.com/)** — Sonnet 4.5, Opus 4, and Haiku 3.5
- **[Rich](https://rich.readthedocs.io/)** — Beautiful terminal UI
- **[prompt-toolkit](https://python-prompt-toolkit.readthedocs.io/)** — Advanced input handling

## 💡 Tips

- Use `/plan` for complex multi-step tasks
- Use natural language for simple edits
- Review plans before executing (arc asks for confirmation)
- Switch models based on task complexity
- Leverage conversation history for context

---

Built with ❤️ using Claude and Agno