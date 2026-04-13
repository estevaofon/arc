---
title: Sistema de Plugins
description: Plugins com hooks de ciclo de vida compatíveis com OpenCode
---

# Sistema de Plugins

Para mais controle do que ferramentas customizadas — por exemplo interceptar tool calls, mutar mensagens de chat, injetar env vars em comandos shell, ou bloquear permissões — use o sistema de plugins. Plugins são arquivos Python que retornam um objeto `Hooks`, espelhando o padrão de hooks do OpenCode.

## Exemplo

```python
# .aru/plugins/audit.py
from aru.plugins import Hooks, PluginInput

async def plugin(ctx: PluginInput, options: dict | None = None) -> Hooks:
    hooks = Hooks()

    @hooks.on("tool.execute.before")
    async def before_tool(event):
        print(f"[audit] running {event.tool_name} with {event.args}")

    @hooks.on("tool.execute.after")
    async def after_tool(event):
        print(f"[audit] {event.tool_name} → ok")

    @hooks.on("shell.env")
    async def inject_env(event):
        event.env["DEPLOY_TOKEN"] = "••••"

    # Plugins também podem registrar tools:
    def greet(name: str) -> str:
        """Say hello."""
        return f"hello, {name}"
    hooks.tools["greet"] = greet

    return hooks
```

Salve como `.aru/plugins/<nome>.py` e o Aru carrega automaticamente no startup.

## Interface do plugin

Todo arquivo de plugin deve exportar uma função `plugin(ctx, options)` (sync ou async) que retorna uma instância de `Hooks`.

| Parâmetro | Descrição |
|-----------|-----------|
| `ctx: PluginInput` | Contexto do plugin — acesso à config, sessão, agente atual |
| `options: dict \| None` | Opções passadas ao plugin via `aru.json` |

## Carregando plugins

Plugins vêm de três fontes:

### 1. Auto-discovery

Arquivos em `.aru/plugins/*.py`, `.agents/plugins/*.py`, e os mesmos caminhos em `~/`.

### 2. Config explícito

Lista no `aru.json`:

```json
{
  "plugins": [
    "my-package-plugin",
    ["./.aru/plugins/audit.py", { "verbose": true }]
  ]
}
```

A segunda forma passa opções ao plugin como argumento `options`.

### 3. Entry points

Pacotes instalados podem se registrar via o entry point group `aru.plugins`:

```toml
# pyproject.toml do seu pacote
[project.entry-points."aru.plugins"]
my-plugin = "my_package.plugin:plugin"
```

## Execução sequencial

Handlers rodam **sequencialmente** na ordem em que foram registrados, para que cada um possa mutar o evento antes do próximo ver. Handlers podem ser sync ou `async`.

## Bloqueando ações

Para bloquear uma ação (tool call, comando, permissão), levante `PermissionError` no handler:

```python
@hooks.on("tool.execute.before")
async def block_rm(event):
    if event.tool_name == "bash" and "rm -rf" in event.args.get("command", ""):
        raise PermissionError("rm -rf bloqueado por política do projeto")
```

## Próximo passo

- [Hooks disponíveis](hooks.md) — Referência completa de todos os hooks e seus eventos
