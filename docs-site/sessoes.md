---
title: Sessões
description: Como o Aru persiste e retoma conversas
---

# Sessões

Toda execução da REPL do Aru é uma **sessão** — um conjunto de mensagens, plano atual, modelo selecionado e métricas de token que vive em um arquivo JSON dentro de `.aru/sessions/`.

## Por que sessões

- **Retomar trabalho.** Você pode sair do terminal e continuar de onde parou.
- **Recuperar contexto.** Se o Aru travar ou você fechar por acidente, o histórico está salvo em disco.
- **Auditar decisões.** Sessões ficam visíveis para você revisar depois o que o agente fez.
- **Paralelizar.** Você pode ter múltiplas sessões ativas em projetos diferentes.

## Localização

Sessões são salvas em `.aru/sessions/` no diretório do projeto. Cada sessão é um arquivo JSON nomeado com um ID único e timestamp.

```text
.aru/
└── sessions/
    ├── 20260413_143052_abc123.json
    ├── 20260413_091204_def456.json
    └── ...
```

## Listando

Via CLI:

```bash
aru --list
```

Ou dentro da REPL:

```text
aru> /sessions
```

Ambos mostram ID, timestamp, primeiro prompt e modelo usado.

## Retomando

```bash
aru --resume last          # última sessão
aru --resume abc123        # pelo ID (prefixo basta)
```

Ao retomar, o Aru restaura:

- **Histórico completo** de mensagens
- **Plano atual** (se você estava no meio de um `/plan`)
- **Modelo selecionado**
- **Métricas de token** acumuladas

A sessão continua exatamente como estava — você pode digitar a próxima mensagem como se nunca tivesse saído.

## Gerenciando espaço

Sessões podem crescer com o tempo, especialmente se você trabalha em conversas longas. Algumas estratégias:

- **Delete manualmente.** `rm .aru/sessions/arquivo.json` — sessões não têm dependências.
- **Use uma pasta global.** Se preferir não poluir o repo, monte um link simbólico de `.aru/sessions` para uma pasta fora do git.
- **Ignore no git.** Adicione `.aru/sessions/` ao `.gitignore` do projeto — eles são pessoais, não vão para o repositório.

## Formato do JSON

O arquivo de sessão é JSON estruturado (não documentamos o schema exato porque ele pode mudar entre versões). Se você precisar inspecionar ou manipular sessões programaticamente, use a API de `aru.session` em vez de parsear o JSON diretamente.

## Privacidade

!!! warning "Cuidado com secrets em sessões"
    Sessões salvam o histórico completo, incluindo qualquer conteúdo que você tenha colado ou que ferramentas tenham retornado. Se você rodou `! env` ou leu um arquivo com credenciais, essas strings ficam no JSON da sessão.
    
    Não commite `.aru/sessions/` no git, e considere limpar sessões antigas periodicamente se você trabalha com dados sensíveis.
