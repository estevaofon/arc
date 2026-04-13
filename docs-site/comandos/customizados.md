---
title: Comandos Customizados
description: Crie slash commands próprios com arquivos Markdown
---

# Comandos Customizados

Comandos customizados são arquivos Markdown armazenados em `.agents/commands/`. O nome do arquivo vira o nome do comando — `.agents/commands/deploy.md` fica disponível como `/deploy`.

## Exemplo simples

```markdown
---
description: Roda lint, testes e sobe deploy para staging
---

Execute os seguintes passos em ordem:

1. Rode `npm run lint` e corrija qualquer erro encontrado
2. Rode `npm test` e garanta que todos os testes passam
3. Rode `./scripts/deploy.sh staging`
4. Reporte o status final e a URL do deploy
```

Salve como `.agents/commands/deploy.md` e use na REPL:

```text
aru> /deploy
```

O conteúdo do arquivo vira o prompt passado ao General Agent (ou ao agente indicado no frontmatter).

## Frontmatter

| Campo | Obrigatório | Descrição |
|-------|-------------|-----------|
| `description` | Recomendado | Aparece em `/commands` e no autocomplete |
| `agent` | Não | Usa um agente customizado específico para rodar o comando |
| `model` | Não | Força um modelo específico (ex: `anthropic/claude-opus-4-6`) |

## Argumentos

Comandos suportam argumentos no estilo OpenCode:

| Placeholder | Significado |
|-------------|-------------|
| `$ARGUMENTS` | String completa após o nome do comando |
| `$1`, `$2`, ... | Argumentos posicionais (1-indexado) |
| `$ARGUMENTS[0]`, `$ARGUMENTS[1]` | Acesso 0-indexado |

Exemplo:

```markdown
---
description: Cria um PR com título e descrição
---

Crie um pull request do branch atual:
- Título: $1
- Corpo: $ARGUMENTS[1:]
- Abra no navegador após criar
```

Uso:

```text
aru> /pr "Fix auth bug" "Resolves #123 by adding JWT middleware"
```

## Discovery

Comandos são descobertos de múltiplas pastas, com as posteriores sobrescrevendo as anteriores:

1. `~/.agents/commands/` — global
2. `~/.claude/commands/` — global (compatibilidade Claude Code)
3. `.agents/commands/` — projeto
4. `.claude/commands/` — projeto

Use `/commands` dentro da REPL para listar todos os comandos disponíveis no projeto atual.

## Dicas

- **Seja imperativo.** "Rode os testes e corrija erros" é mais eficaz que "verifique se os testes estão ok".
- **Liste passos explícitos.** Comandos de múltiplos passos funcionam melhor quando o agente sabe exatamente a sequência.
- **Combine com agentes.** Se você tem um agente customizado de review, use `agent: reviewer` no frontmatter do comando `/review`.
