---
title: Skills
description: Personas reutilizáveis no formato agentskills.io
---

# Skills

Skills são **personas** ou **modos de trabalho** reutilizáveis, seguindo o formato [agentskills.io](https://agentskills.io). Elas se diferenciam de agentes customizados por serem mais leves: uma skill é basicamente um system prompt aplicado a um agente existente, sem modelo ou tools próprios.

## Estrutura

Cada skill mora em `.agents/skills/<nome>/SKILL.md`:

```text
.agents/
└── skills/
    └── review/
        └── SKILL.md
```

## Exemplo

```markdown
---
name: review
description: Revisor de código crítico e objetivo
---

Você é um revisor de código sênior. Para cada arquivo ou diff apresentado:

1. Identifique bugs e edge cases não tratados
2. Aponte problemas de segurança (injeção, secrets, autorização)
3. Sugira melhorias de legibilidade quando o ganho for claro
4. Seja objetivo — não elogie por elogiar

Nunca modifique arquivos. Apenas reporte.
```

## Como ativar

Skills podem ser listadas com `/skills` e ativadas como parte do contexto de uma mensagem. A skill é injetada no system prompt do agente para aquele turno.

## Discovery

Skills são procuradas em múltiplas pastas, com as posteriores sobrescrevendo as anteriores:

1. `.agents/skills/` — projeto
2. `.claude/skills/` — projeto (compat)
3. `~/.agents/skills/` — global
4. `~/.claude/skills/` — global

## Skills vs. Agentes customizados

| | Skill | Agente Customizado |
|---|-------|---------------------|
| **Modelo próprio** | ❌ | ✅ |
| **Tools próprias** | ❌ | ✅ |
| **Permissões próprias** | ❌ | ✅ |
| **System prompt próprio** | ✅ | ✅ |
| **Invocação** | Contexto | `/name`, `@name`, `delegate_task` |

Use **skills** quando você só precisa mudar o tom/foco do agente (revisor, professor, documentador). Use **agentes customizados** quando você precisa de controle fino sobre modelo, ferramentas ou permissões.
