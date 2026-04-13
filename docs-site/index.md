---
title: Aru
description: Assistente de programação inteligente no terminal, com múltiplos agentes Claude
hide:
  - navigation
---

# Aru

**Aru** é um assistente de programação inteligente que roda no terminal. Você descreve a tarefa em linguagem natural e agentes especializados planejam, exploram o código e aplicam mudanças usando Claude.

![Aru demo](https://github.com/user-attachments/assets/e84d5139-ebaa-4d12-bbae-628fae7dbc7a)

## Destaques

<div class="grid cards" markdown>

-   :material-brain:{ .lg .middle } __Arquitetura Multi-Agente__

    ---

    Agentes especializados para planejamento, execução, exploração e conversação — cada um com seu próprio conjunto de ferramentas e prompt.

-   :material-console:{ .lg .middle } __CLI Interativo__

    ---

    REPL com respostas em streaming, suporte a multi-linha, histórico de sessões e mentions de arquivos com `@`.

-   :material-puzzle:{ .lg .middle } __11 Ferramentas Integradas__

    ---

    Leitura e edição de arquivos, busca em código, shell, web search e delegação de tarefas a sub-agentes.

-   :material-swap-horizontal:{ .lg .middle } __Multi-Provider__

    ---

    Anthropic, OpenAI, Ollama, Groq, OpenRouter, DeepSeek e outros providers customizados via `aru.json`.

-   :material-image-multiple:{ .lg .middle } __Suporte a Imagens__

    ---

    Anexe imagens com `@arquivo.png` para análise multimodal (Claude, GPT-4o, Gemini).

-   :material-cog-outline:{ .lg .middle } __Extensível__

    ---

    Comandos, skills, agentes, ferramentas Python e plugins com hooks compatíveis com OpenCode.

</div>

## Instalação

```bash
pip install aru-code
```

Configure sua chave da Anthropic em um `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-sua-chave-aqui
```

E execute:

```bash
aru
```

Pronto. Veja o [Início Rápido](comecando/inicio-rapido.md) para mais detalhes.

## Como funciona

```text
main.py → cli.run_cli() → REPL
                           ├─ General Agent   (conversa + ferramentas)
                           ├─ /plan → Planner (plano passo a passo)
                           └─ Executor        (implementa cada passo)
```

O agente geral resolve tarefas diretas. Quando você pede `/plan`, o Planner gera um plano em Markdown, e o Executor implementa cada passo com acesso a todas as ferramentas.

## Próximos passos

- [Instalação](comecando/instalacao.md)
- [Início Rápido](comecando/inicio-rapido.md)
- [CLI](comecando/cli.md)
- [Configuração](configuracao/index.md)
- [Agentes](agentes/index.md)
- [Ferramentas](ferramentas/index.md)
- [Plugins](plugins/index.md)
