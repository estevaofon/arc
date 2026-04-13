---
title: Ferramentas Customizadas
description: Estenda o Aru com suas próprias ferramentas Python
---

# Ferramentas Customizadas

Você pode estender o Aru com suas próprias ferramentas Python. Drop um `.py` em `.aru/tools/` (projeto) ou `~/.aru/tools/` (global) — o Aru auto-descobre e registra todas as funções encontradas.

## Exemplo

```python
# .aru/tools/deploy.py
from aru.plugins import tool

@tool(description="Deploy the current branch to an environment")
def deploy(environment: str = "staging") -> str:
    """Runs the deploy script and returns the output."""
    import subprocess
    result = subprocess.run(
        ["./scripts/deploy.sh", environment],
        capture_output=True, text=True,
    )
    return result.stdout or result.stderr
```

O LLM vê cada função como uma tool de primeira classe — nome, descrição e parâmetros tipados são inferidos da assinatura.

## Regras

- **Decorator é opcional.** Um `def fn(...) -> str` com docstring também funciona. Use `@tool(...)` quando quiser uma descrição customizada ou quiser sobrescrever uma ferramenta nativa.
- **Parâmetros** são lidos dos type hints; defaults viram parâmetros opcionais.
- **Return type** deve ser `str` (ou algo stringificável) — o resultado é enviado de volta ao LLM como output da tool.
- **Override de nativas:** use `@tool(override=True)` se quiser substituir, por exemplo, o `bash` por uma implementação própria.
- **Sync e async:** funções síncronas e `async def` são ambas suportadas.

## Discovery paths

Caminhos (os últimos sobrescrevem os primeiros):

1. `~/.aru/tools/`
2. `.aru/tools/`
3. `~/.agents/tools/`
4. `.agents/tools/`

## Exemplo mínimo sem decorator

```python
# .aru/tools/wordcount.py

def word_count(text: str) -> str:
    """Conta palavras em um texto."""
    return str(len(text.split()))
```

Pronto — `word_count` já aparece para o agente como uma tool disponível, com descrição vinda da docstring.

## Exemplo async

```python
# .aru/tools/fetch_json.py
import httpx
from aru.plugins import tool

@tool(description="Fetches a JSON URL and returns the first 2000 chars")
async def fetch_json(url: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=10)
        return r.text[:2000]
```

## Permissões em ferramentas customizadas

Ferramentas customizadas passam pelo mesmo sistema de permissões. Você pode configurá-las por nome no `aru.json`:

```json
{
  "permission": {
    "deploy": "ask",
    "fetch_json": "allow"
  }
}
```

Se a ferramenta faz operações sensíveis (deploy, delete, chamada a API paga), comece com `ask` e só promova para `allow` depois de testar.

## Quando usar ferramentas customizadas vs. plugins

| | Tool customizada | Plugin |
|---|------------------|--------|
| **Registrar nova tool** | ✅ | ✅ |
| **Interceptar outras tools** | ❌ | ✅ |
| **Mudar prompt do sistema** | ❌ | ✅ |
| **Injetar env vars no bash** | ❌ | ✅ |
| **Bloquear permissões** | ❌ | ✅ |
| **Complexidade** | Baixa | Média |

Comece com **ferramentas customizadas**. Promova para **plugin** quando precisar de hooks do ciclo de vida.
