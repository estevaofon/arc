---
title: Modelos e Providers
description: Como usar Anthropic, OpenAI, Ollama, Groq, OpenRouter e DeepSeek no Aru
---

# Modelos e Providers

Por padrão o Aru usa **Claude Sonnet 4.6** (Anthropic). Você pode trocar de modelo a qualquer momento na REPL com `/model`, ou definir o padrão no `aru.json`.

## Providers suportados

| Provider | Comando | Chave (`.env`) | Instalação extra |
|----------|---------|----------------|------------------|
| **Anthropic** | `/model anthropic/claude-sonnet-4-6` | `ANTHROPIC_API_KEY` | — (incluído) |
| **Ollama** | `/model ollama/llama3.1` | — (local) | `pip install "aru-code[ollama]"` |
| **OpenAI** | `/model openai/gpt-4o` | `OPENAI_API_KEY` | `pip install "aru-code[openai]"` |
| **Groq** | `/model groq/llama-3.3-70b-versatile` | `GROQ_API_KEY` | `pip install "aru-code[groq]"` |
| **OpenRouter** | `/model openrouter/deepseek/deepseek-chat-v3-0324` | `OPENROUTER_API_KEY` | `pip install "aru-code[openai]"` |
| **DeepSeek** | `/model deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` | `pip install "aru-code[openai]"` |

Para instalar todos os providers de uma vez:

```bash
pip install "aru-code[all-providers]"
```

## Ollama (modelos locais)

Para rodar modelos localmente sem chave de API, instale o [Ollama](https://ollama.com/), suba o servidor e use qualquer modelo já baixado:

```bash
ollama serve                    # inicia o servidor Ollama
ollama pull codellama           # baixa um modelo
aru                             # entra na REPL
# dentro do aru:
/model ollama/codellama
```

## Modelo padrão

Você pode definir o provider/modelo padrão no `aru.json` para não precisar trocar manualmente em toda sessão:

```json
{
  "default_model": "openrouter/minimax/minimax-m2.7",
  "model_aliases": {
    "minimax": "openrouter/minimax/minimax-m2.5",
    "deepseek-v3": "openrouter/deepseek/deepseek-chat-v3-0324",
    "sonnet-4-6": "anthropic/claude-sonnet-4-6",
    "opus-4-6": "anthropic/claude-opus-4-6"
  }
}
```

O campo `default_model` define o modelo principal. Os `model_aliases` são atalhos que podem ser usados com `/model <alias>`.

## Providers customizados

Você pode configurar providers customizados com limites de token específicos:

```json
{
  "providers": {
    "deepseek": {
      "models": {
        "deepseek-chat-v3-0324": { "max_tokens": 16384 }
      }
    },
    "openrouter": {
      "models": {
        "minimax/minimax-m2.5": { "max_tokens": 65536 },
        "minimax/minimax-m2.7": { "max_tokens": 131072 }
      }
    }
  }
}
```

## Trocando de modelo em sessão

```text
aru> /model                            # lista modelos disponíveis
aru> /model sonnet-4-6                 # usa um alias
aru> /model anthropic/claude-opus-4-6  # formato completo
```

A troca é imediata — a próxima mensagem já usa o novo modelo, preservando o histórico da sessão.
