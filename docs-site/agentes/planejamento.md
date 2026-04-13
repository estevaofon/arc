---
title: Planejamento
description: Como funciona o fluxo Planner → Executor do Aru
---

# Planejamento

O fluxo de planejamento do Aru separa **raciocínio** de **execução**. O Planner gera um plano estruturado em modo read-only, e o Executor implementa cada passo com acesso total às ferramentas.

## Quando usar `/plan`

Use `/plan` quando:

- A tarefa envolve **múltiplos arquivos** ou camadas
- Você quer **revisar a estratégia** antes de aplicar mudanças
- A mudança é **arquitetural** (migração, refatoração ampla, novo feature)
- Você precisa de um **registro** do raciocínio para depois

Para edits pequenos e localizados, o General Agent resolve direto sem precisar de plano.

## Fluxo completo

```text
Usuário: /plan adicionar autenticação JWT ao endpoint /api/users

  ↓ Planner Agent (Sonnet, read-only)

1. Lê arquivos relevantes (auth.py, users.py, settings.py)
2. Analisa dependências e padrões atuais
3. Produz plano em Markdown:

   ## Summary
   Adicionar middleware JWT e proteger /api/users

   ## Steps
   1. Instalar `pyjwt` no requirements
   2. Criar `auth/jwt_middleware.py` com decorador `@require_jwt`
   3. Adicionar `JWT_SECRET` ao `config.py`
   4. Aplicar `@require_jwt` em `users.py` nas rotas protegidas
   5. Adicionar testes em `tests/test_auth.py`

  ↓ Usuário confirma

Executor Agent (Sonnet, todas as ferramentas)
  - Passo 1: executa e reporta
  - Passo 2: executa e reporta
  - ...
```

## Plano como documento

Planos vivem no estado da sessão e ficam visíveis no status bar. Você pode:

- **Revisar** o plano antes de aprovar a execução
- **Editar** o plano manualmente pedindo ao agente para modificar passos específicos
- **Retomar** uma sessão e continuar de onde parou
- **Descartar** o plano e pedir um novo

## Contexto passado ao Executor

Cada passo é enviado ao Executor com:

- **O passo atual** (descrição + número)
- **O plano completo** como contexto
- **Histórico da conversa** (resumido se necessário)
- **Resultados dos passos anteriores**

Isso permite que o Executor entenda onde está no plano e tome decisões consistentes com os passos já aplicados.

## Limites

- Planner: **Sonnet, 4K tokens de output** — suficiente para planos bem estruturados
- Executor: **Sonnet, 8K tokens de output** — suficiente para edits substanciais por passo

Se uma tarefa individual for grande demais para um único passo, o Planner deve dividi-la em sub-passos. Se você perceber passos muito grandes, peça um plano mais granular.

## Dica: iteração do plano

Se o primeiro plano não for ideal, você pode pedir ajustes antes de executar:

```text
aru> /plan migrar banco de SQLite pra Postgres
aru> o passo 3 tá vago, quebra em sub-passos com os arquivos exatos
aru> adiciona um passo de rollback caso a migração falhe
```

O Planner regenera o plano incorporando os ajustes.
