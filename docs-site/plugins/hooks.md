---
title: Hooks
description: Referência completa dos hooks de ciclo de vida disponíveis
---

# Hooks

O Aru expõe hooks em todos os pontos-chave do ciclo de vida de uma sessão. Cada hook recebe um `event` tipado que pode ser inspecionado ou mutado.

## Tabela de hooks

| Hook | Quando dispara | Uso típico |
|------|----------------|------------|
| `config` | Depois que a config é carregada | Ler/ajustar a config |
| `tool.execute.before` | Antes de qualquer tool rodar | Audit, bloquear, mutar args |
| `tool.execute.after` | Depois de qualquer tool rodar | Log, pós-processar resultados |
| `tool.definition` | Quando a lista de tools é resolvida | Modificar descrições/parâmetros |
| `chat.message` | Antes de uma mensagem do usuário ir ao LLM | Reescrever a mensagem |
| `chat.params` | Antes da chamada ao LLM | Ajustar `temperature`, `max_tokens` |
| `chat.system.transform` | Antes da chamada ao LLM | Modificar o system prompt |
| `chat.messages.transform` | Antes da chamada ao LLM | Modificar o histórico completo |
| `command.execute.before` | Antes de um slash command rodar | Bloquear ou reescrever |
| `permission.ask` | Antes de um prompt de permissão | Auto-allow/deny |
| `shell.env` | Antes de `bash` rodar | Injetar variáveis de ambiente |
| `session.compact` | Antes da compactação de contexto | Reagir à compactação |
| `event` | Qualquer evento publicado | Inscrição genérica |

## Exemplos por hook

### `config`

```python
@hooks.on("config")
async def tweak_config(event):
    # event.config é o AgentConfig carregado
    event.config.default_model = "anthropic/claude-sonnet-4-6"
```

### `tool.execute.before`

```python
@hooks.on("tool.execute.before")
async def audit(event):
    print(f"tool={event.tool_name} args={event.args}")
    if event.tool_name == "write_file" and ".env" in event.args.get("path", ""):
        raise PermissionError("Writes to .env are blocked")
```

### `chat.system.transform`

```python
@hooks.on("chat.system.transform")
async def add_brand(event):
    event.system_prompt += "\n\nAlways use metric units in examples."
```

### `chat.params`

```python
@hooks.on("chat.params")
async def lower_temperature(event):
    event.params["temperature"] = 0.2
```

### `shell.env`

```python
@hooks.on("shell.env")
async def inject_secrets(event):
    import os
    event.env["DEPLOY_TOKEN"] = os.environ["DEPLOY_TOKEN"]
```

### `permission.ask`

```python
@hooks.on("permission.ask")
async def auto_allow_tests(event):
    if event.tool == "bash" and event.target.startswith("pytest"):
        event.decision = "allow"
```

### `command.execute.before`

```python
@hooks.on("command.execute.before")
async def log_commands(event):
    print(f"user ran /{event.name} with args: {event.args}")
```

## Registrando tools em plugins

Além dos hooks, plugins podem registrar tools diretamente no `hooks.tools`:

```python
def uppercase(text: str) -> str:
    """Return text in uppercase."""
    return text.upper()

hooks.tools["uppercase"] = uppercase
```

É equivalente a dropar um arquivo em `.aru/tools/`, mas permite que a tool faça parte do pacote do plugin e carregue condicionalmente.

## Boas práticas

- **Use hooks síncronos quando possível.** Mesmo que o Aru suporte `async`, hooks síncronos são mais rápidos e têm semântica previsível.
- **Não bloqueie por padrão.** Hooks que levantam exceções sem motivo claro interrompem toda a sessão. Sempre deixe claro no erro por que a ação foi bloqueada.
- **Cuidado com mutações do histórico.** `chat.messages.transform` dá poder total sobre o histórico — remover mensagens pode confundir o LLM. Prefira `chat.system.transform` quando possível.
- **Registre efeitos.** Hooks que mutam state externo (envio de webhook, gravação em DB) devem logar o que fizeram — debugar plugins silenciosos é frustrante.
