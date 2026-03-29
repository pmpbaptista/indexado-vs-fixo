# Indexado Vs Fixo — Monitorização OMIE

Script CLI para monitorizar o mercado indexado de eletricidade (OMIE) para consumidores domésticos.

## Como usar (recomendado: Astral UV)

1. Cria o ambiente virtual com UV:

```bash
uv venv
source .venv/bin/activate
```

2. Instala as dependências:

```bash
uv sync
```

3. Executa o script:

```bash
uv run main.py
```

O script descarrega os dados OMIE e gera a análise automaticamente.

## Configuração

Edita os parâmetros no dicionário `CONFIG` no topo do ficheiro `main.py` para ajustar consumo, dias, potências e taxas.

---

## Workflow n8n (automação)

O ficheiro `n8n/analise_omie.json` contém um workflow n8n para automação da análise OMIE.

**Como usar:**

1. Importa o ficheiro `n8n/analise_omie.json` no teu n8n.
2. Altera o URL do webhook (nó "Notificação") para o endpoint desejado (exemplo: o teu serviço de notificações ou chat).
3. Ativa o workflow para receber análises automáticas.
