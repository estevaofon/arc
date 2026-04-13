---
title: CLI
description: Opções de linha de comando, slash commands e modos de execução do Aru
---

# CLI

O Aru oferece um REPL interativo completo, mas também pode ser usado em modo one-shot ou recebendo input via pipe.

## Modos de execução

### REPL interativo

```bash
aru
```

Abre a REPL padrão com streaming, histórico e permissões habilitadas.

### One-shot com ferramentas

```bash
aru "liste os arquivos Python grandes neste projeto"
```

Executa uma única tarefa usando todas as ferramentas (read, grep, bash, etc.) e termina.

### Modo `--print` (só texto)

```bash
aru --print "explique o que é um gerador em Python"
```

Resposta de texto puro, sem ferramentas — equivalente a uma chamada direta ao LLM. Útil para integração com scripts.

### Via pipe

```bash
echo "resuma este diff" | git diff | aru
```

O conteúdo do stdin entra como prompt.

## Opções de linha de comando

| Opção | Descrição |
|-------|-----------|
| `aru` | Inicia uma nova sessão interativa |
| `aru "prompt"` | Executa um prompt único com ferramentas |
| `aru --print "prompt"` | Executa em modo texto puro (sem ferramentas) |
| `aru --resume <id>` | Retoma uma sessão pelo ID |
| `aru --resume last` | Retoma a última sessão |
| `aru --list` | Lista sessões salvas |
| `aru --dangerously-skip-permissions` | Pula o sistema de permissões (uso arriscado) |
| `aru --version` | Mostra a versão instalada |

## Slash commands

Dentro da REPL, comandos começando com `/` controlam o Aru:

| Comando | Descrição |
|---------|-----------|
| `/plan <tarefa>` | Cria um plano de implementação detalhado |
| `/model [provider/model]` | Lista ou troca o modelo atual |
| `/mcp` | Lista os servidores MCP e ferramentas disponíveis |
| `/commands` | Lista comandos customizados |
| `/skills` | Lista skills disponíveis |
| `/agents` | Lista agentes customizados |
| `/sessions` | Lista sessões recentes |
| `/undo` | Remove o último turno do histórico |
| `/help` | Mostra todos os comandos |
| `/quit`, `/exit` | Sai do Aru |

## Sessões

Cada execução da REPL é persistida como JSON em `.aru/sessions/`. Você pode retomar uma sessão com:

```bash
aru --resume last          # última sessão
aru --resume abc123        # pelo ID
aru --list                 # lista todas
```

O histórico de mensagens, o plano atual, a configuração de modelo e as métricas de token são restaurados exatamente como estavam.

## Atalhos e input

- **`@arquivo`** — autocomplete de arquivos relativos ao CWD (também funciona com imagens)
- **`! comando`** — executa comando shell sem passar pelo LLM
- **`Ctrl+C`** — interrompe a geração atual
- **`Ctrl+D`** — sai da REPL
- **Multi-line paste** — o Aru detecta automaticamente paste de múltiplas linhas e formata como bloco
