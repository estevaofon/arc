---
title: Instalação
description: Como instalar o Aru via pip ou a partir do código fonte
---

# Instalação

O Aru é distribuído via PyPI como o pacote `aru-code` e precisa de Python 3.11 ou superior.

## Via pip

```bash
pip install aru-code
```

Depois da instalação, o comando `aru` fica disponível globalmente no seu terminal.

## Providers opcionais

O provider padrão é a Anthropic (Claude) e já vem incluído. Para usar outros providers, instale os extras correspondentes:

=== "Todos os providers"

    ```bash
    pip install "aru-code[all-providers]"
    ```

=== "OpenAI"

    ```bash
    pip install "aru-code[openai]"
    ```

=== "Ollama"

    ```bash
    pip install "aru-code[ollama]"
    ```

=== "Groq"

    ```bash
    pip install "aru-code[groq]"
    ```

## A partir do código fonte

```bash
git clone https://github.com/estevaofon/aru.git
cd aru
pip install -e ".[dev]"
```

A instalação em modo editável (`-e`) permite rodar o Aru e modificar o código ao mesmo tempo — útil para contribuições ou debugging.

## Configurando a chave de API

O Aru usa **Claude Sonnet 4.6** por padrão. Você precisa de uma [chave da Anthropic](https://console.anthropic.com/).

Crie um arquivo `.env` na raiz do seu projeto:

```env
ANTHROPIC_API_KEY=sk-ant-sua-chave-aqui
```

Ou defina como variável de ambiente no seu shell:

=== "Linux / macOS"

    ```bash
    export ANTHROPIC_API_KEY=sk-ant-sua-chave-aqui
    ```

=== "Windows (PowerShell)"

    ```powershell
    $env:ANTHROPIC_API_KEY = "sk-ant-sua-chave-aqui"
    ```

!!! tip "Usando outro provider?"
    Veja a página de [Modelos e Providers](../configuracao/modelos.md) para configurar OpenAI, Ollama, Groq, OpenRouter ou DeepSeek.

## Verificando a instalação

```bash
aru --version
```

Se tudo estiver correto, você verá o número da versão instalada. Em seguida, rode `aru` para entrar na REPL interativa.
