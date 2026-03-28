# Indexado Vs Fixo — Monitorização OMIE para Consumidores Domésticos

Serviço de monitorização do mercado indexado de eletricidade (OMIE) para consumidores domésticos.
Com base em `main.py`, o programa é um script CLI que descarrega dados OMIE, calcula médias e gera um relatório/excel.

## Visão geral

- Calcula a média OMIE dos últimos dias e analisa o ponto de equilíbrio face a um tarifário fixo.

## Requisitos

- Python 3.10+
- pip ou uv
- `requests`, `openpyxl` (dependências usadas por `main.py`)
- Recomenda-se usar um virtualenv

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
```

## Instalação (pip / manual)

1. Clonar o repositório e activar o virtualenv:

```bash
git clone https://github.com/pmpbaptista/indexado-vs-fixo.git
cd index_monitor
source .venv/bin/activate
```

2. Instalar dependências necessárias (exemplo mínimo):

```bash
pip install --upgrade pip setuptools wheel
pip install requests openpyxl
```

Se preferires instalar o pacote localmente (quando houver `pyproject.toml` configurado):

```bash
pip install -e .
```

## Executar com `uv` (Astral UV manager)

Se utilizas o gestor `uv` da Astral, instala-o e executa o script sob o gestor para facilitar restarts e logs:

```bash
pip install uv
uv run -- python main.py
```

Nota: o comando acima executa `python main.py` gerido pelo `uv`. Ajusta flags do `uv` conforme o teu fluxo (background, logs, etc.).

## Executar diretamente (pip / python)

Executar directamente com Python:

```bash
source .venv/bin/activate
python main.py
```

Ou, se preferires executar como módulo (quando o package for instalado):

```bash
python -m index_monitor
```

## Variáveis / Configuração

O `main.py` contém constantes configuráveis no topo do ficheiro. Ajusta `MEU_CONSUMO` e outros parâmetros conforme necessário.
