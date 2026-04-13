---
title: Regras (AGENTS.md)
description: Como anexar instruções de projeto aos prompts dos agentes
---

# Regras (AGENTS.md)

Todo agente do Aru recebe um system prompt base + qualquer instrução de projeto que você fornecer. A forma mais simples de adicionar regras é criar um arquivo `AGENTS.md` na raiz do projeto.

## AGENTS.md

```markdown
# Meu Projeto

- Use TypeScript estrito, nunca `any`
- Testes ficam em `tests/` e usam Vitest
- Nunca commit arquivos `.env`
- Prefira funções pequenas (menos de 40 linhas)
```

Esse conteúdo é anexado ao prompt de **todos** os agentes automaticamente.

!!! tip "Compatibilidade com outras ferramentas"
    O Aru também lê `CLAUDE.md` se existir, para compatibilidade com Claude Code.

## Campo `instructions` no `aru.json`

Para regras que moram em outros arquivos, globs ou URLs remotas, use o campo `instructions`:

```json
{
  "instructions": [
    "CONTRIBUTING.md",
    "docs/coding-standards.md",
    "packages/*/AGENTS.md",
    "https://raw.githubusercontent.com/minha-org/shared-rules/main/style.md"
  ]
}
```

Cada entrada é resolvida assim:

| Formato | Exemplo | Comportamento |
|---------|---------|---------------|
| **Arquivo local** | `"CONTRIBUTING.md"` | Lê o arquivo relativo à raiz do projeto |
| **Glob pattern** | `"docs/**/*.md"` | Expande o padrão, respeita `.gitignore` |
| **URL remota** | `"https://example.com/rules.md"` | Fetch via HTTP (timeout 5s, cache por sessão) |

Todo o conteúdo resolvido é combinado e anexado ao system prompt junto do `AGENTS.md`.

## Limites de tamanho

Para evitar estourar o contexto:

- Arquivos individuais são limitados a **10 KB**
- O conteúdo combinado total é limitado a **50 KB**
- Arquivos faltando e URLs que falham são ignorados com um warning

## Ordem de composição do prompt

O system prompt final de cada agente é montado assim:

```text
1. Prompt base do agente (hardcoded)
2. AGENTS.md (e/ou CLAUDE.md)
3. Arquivos do campo instructions
4. Contexto do ambiente (git status, estrutura do projeto)
```

## Boas práticas

- **Seja específico.** "Use try/except em chamadas de rede" é melhor que "trate erros bem".
- **Evite duplicação.** Se o README já explica a arquitetura, o `AGENTS.md` não precisa repetir — apenas aponte para ele.
- **Documente invariantes não-óbvios.** "A coluna `user_id` em `sessions` é nullable para sessões anônimas" salva o agente de assumir o contrário.
- **Mantenha curto.** 50 KB parece muito, mas cada token no system prompt sai do seu orçamento de contexto.
