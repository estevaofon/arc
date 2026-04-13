---
title: Agentes
description: Visão geral dos agentes nativos do Aru
---

# Agentes

O Aru é construído em torno de **agentes** — instâncias de LLM com prompt, conjunto de ferramentas e papel específicos. A REPL orquestra esses agentes conforme o que você pede.

## Agentes nativos

| Agente | Papel | Ferramentas |
|--------|-------|-------------|
| **General** | Conversa e operações diretas | Todas (incluindo delegação) |
| **Planner** | Analisa o código e cria planos estruturados | Read-only + search + web |
| **Executor** | Implementa mudanças de código a partir de um plano | Todas (write, edit, bash) |
| **Explorer** | Busca rápida e read-only no codebase | Read-only + search + bash limitado |

## General Agent

É o agente padrão da REPL. Resolve tarefas diretas — perguntas, edits pequenos, refatorações localizadas — sem precisar de plano explícito. Tem acesso a todas as 11 ferramentas principais e pode delegar sub-tarefas via `delegate_task`.

## Planner Agent

Ativado via `/plan <tarefa>`. Lê o código em modo read-only e produz um plano estruturado em Markdown com:

- `## Summary` — resumo da mudança proposta
- `## Steps` — lista numerada de passos, cada um com contexto técnico e arquivos afetados

```text
aru> /plan refatore o módulo de auth para usar JWT tokens
```

O Planner usa Sonnet com 4K tokens de output. Ele **não** modifica arquivos — apenas produz o plano.

## Executor Agent

Implementa os passos gerados pelo Planner. Recebe um passo por vez, junto com o plano completo como contexto, e tem acesso a todas as ferramentas (write, edit, bash, etc.).

Normalmente você não invoca o Executor diretamente — ele roda automaticamente quando você confirma um plano.

## Explorer Agent

Agente rápido e read-only para exploração de codebase. Útil quando você quer uma resposta rápida sobre estrutura do projeto sem ocupar contexto do agente principal. É usado internamente pelo `delegate_task` quando a sub-tarefa é classificada como exploratória.

## Delegação

Qualquer agente com acesso à ferramenta `delegate_task` pode criar sub-agentes autônomos para paralelizar trabalho. Cada sub-agente recebe sua própria janela de contexto e retorna apenas o resultado final.

```text
Agent → delegate_task(task="encontre todos os usos de useMemo", agent="explorer")
      → sub-agente roda busca em paralelo
      → retorna lista compacta ao agente principal
```

Isso é valioso para:

- **Pesquisas amplas** que consumiriam muitos tokens na conversa principal
- **Tarefas independentes** que podem rodar em paralelo
- **Proteção do contexto** do agente principal contra ruído de resultados intermediários

## Próximo passo

- [Agentes Customizados](customizados.md) — Criar seus próprios agentes com `.agents/agents/`
- [Planejamento](planejamento.md) — Como o fluxo Planner → Executor funciona em detalhe
