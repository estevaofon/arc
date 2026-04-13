---
title: Início Rápido
description: Primeiros passos com o Aru — do primeiro prompt ao primeiro plano
---

# Início Rápido

Este guia assume que você já [instalou o Aru](instalacao.md) e configurou uma chave de API.

## Abrindo a REPL

```bash
aru
```

Você verá o logo do Aru e um prompt interativo. A partir daí basta descrever o que você quer em linguagem natural.

```text
aru> liste os arquivos Python neste projeto e me diga qual é o ponto de entrada
```

O agente geral lê a estrutura, roda os comandos necessários e responde em streaming.

## Usando @ para mencionar arquivos

Você pode referenciar arquivos com a sintaxe `@caminho`. O Aru detecta automaticamente e carrega o conteúdo no contexto:

```text
aru> explique o que o @aru/runner.py faz
aru> compare @tests/test_cli.py com @tests/test_runner.py
```

Imagens também funcionam via `@`. Formatos suportados: `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp`.

```text
aru> descreva o que aparece em @screenshot.png
aru> analise o diagrama em @docs/arch.png e sugira melhorias no código
```

!!! note "Modelos multimodais"
    Imagens exigem um modelo multimodal (Claude Opus/Sonnet, GPT-4o, Gemini). Modelos locais via Ollama podem não suportar.

## Planejando tarefas complexas

Para tarefas maiores, use `/plan`. O Planner Agent lê o código em modo read-only e produz um plano estruturado em Markdown:

```text
aru> /plan refatore o módulo de autenticação para usar JWT
```

Após revisar o plano, você pode pedir ao Aru para executar os passos. Cada passo é implementado pelo Executor Agent com acesso total às ferramentas.

## Executando comandos shell

Prefixe com `!` para rodar diretamente no shell:

```text
aru> ! pytest tests/ -v
aru> ! git status
```

O output é capturado e exibido no terminal, sem passar pelo LLM.

## Trocando de modelo durante a sessão

```text
aru> /model anthropic/claude-opus-4-6
aru> /model ollama/codellama
aru> /model openai/gpt-4o
```

Veja [Modelos e Providers](../configuracao/modelos.md) para a lista completa.

## Próximos passos

- [CLI](cli.md) — Todas as opções de linha de comando e slash commands
- [Configuração](../configuracao/index.md) — `aru.json`, permissões e regras
- [Agentes](../agentes/index.md) — Como os agentes do Aru funcionam e como criar os seus
