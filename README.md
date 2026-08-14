# Indexado Vs Fixo — Monitorização OMIE

Script CLI + workflow n8n para monitorizar o mercado indexado de eletricidade (OMIE) e comparar com o melhor tarifário fixo.

## Como usar (recomendado: Astral UV)

```bash
uv venv
source .venv/bin/activate
uv sync
uv run main.py            # resumo na consola
uv run main.py -o json    # saída JSON (ideal para alimentar automações)
```

O script descarrega os dados OMIE (via CSV consolidado do Tiago Felícia, com fallback aos ficheiros diários OMIE) e gera a análise automaticamente.

## Configuração

Edita o dicionário `CONFIG` no topo de `main.py`:

| Parâmetro | Valor atual (fatura jul/ago 2026) | Notas |
|---|---|---|
| `consumo_kwh` | 614 | kWh faturados na última fatura |
| `dias_faturacao` | 31 | dias do período de faturação |
| `potencia_kva` | 6.90 | potência contratada |
| `ciclo` | "Simples" | "Simples" \| "Bi-horário" \| "Tri-horário" |
| `tarifario_atual_nome` | "EDP \| Eletricidade · Digital · Campanha -20%" | procurado por nome parcial no CSV |
| `tar_energia_kwh` | 0.0607 | TAR energia simples ERSE 2026 |
| `tar_potencia_dia` | 0.3436 | TAR potência 6.9 kVA |
| `tse` | 0.0020666 | Financiamento TSE (Constantes.csv) |

### Indexado (G9 | Smart Dynamic SPOT 8!)

Fórmula oficial (do simulador do Tiago Felícia):

```
preço_kwh = OMIE_€/kWh * G9_FA * perdas + G9_CGS + G9_AC + TAR_energia
```

- `g9_fa` = 1.02 (fator de ajuste G9, Constantes.csv `G9_K1`)
- `margem_kwh` = 0.0155 = `G9_CGS` (0.01) + `G9_AC` (0.0055)
- `tse_incluido` = true → o TSE já está incluído (não somar)
- `potencia_dia` = 0.6242 (6.9 kVA, Indexados.csv; já inclui TAR potência)

Também calcula a referência **EDP | Eletricidade Indexada Média** (OMIE/1000 × 1.164 × 1.1 + 0.0185) com potência 0.4607 €/dia.

As **perdas de rede** são obtidas dinamicamente do `OMIE_PERDAS_CICLOS.csv` (média do período), com fallback para 1.1488.

## Análise da fatura (2 jul – 1 ago 2026)

| Item | Valor |
|---|---|
| Consumo Simples | 614 kWh |
| Energia bruta | 0.1671 €/kWh → com -20% = **0.1337 €/kWh** |
| Potência | 0.578 €/dia → com -20% = **0.4624 €/dia** |
| Total eletricidade (s/IVA) | **96.42 €** |
| Acesso às redes (TAR) | Energia 37.27€ + Potência 10.65€ + CIEG 27.31€ |

O script reproduz este valor (~96.43€), validando a lógica de cálculo.

---

## Workflow n8n (automação)

O ficheiro `n8n/analise_omie.json` contém o workflow **"Análise OMIE - Melhor Fixo Auto (v2)"**.

**Como usar:**

1. Importa `n8n/analise_omie.json` no teu n8n.
2. Altera o URL do webhook (nó "Notificação") para o teu endpoint.
3. Ativa o workflow.

### Melhorias da v2 face à v1

- **31 requests OMIE → 1 request único** ao `OMIE_PERDAS_CICLOS.csv` (OMIE + perdas de rede juntos, intervalos de 15 min).
- **Constantes corrigidas** para as oficiais do Tiago Felícia: TSE 0.0020666 (era 0.0026), margem G9 0.0155 (era 0.0150), perdas dinâmicas (era fixo 1.0), potência indexado 0.6242 (era a TAR pura).
- **Comparação com o tarifário atual** (EDP Digital -20%) — a v1 só comparava fixo vs indexado.
- **Referência EDP Indexada Média** no resumo.
- **Cálculo inclui valores OMIE ≤0** (preços zero/negativos são reais) — antes eram descartados, inflacionando a média.
- Saída JSON com `resumo` pronto para enviar como mensagem (ex: webhook Discord/Telegram).

### Nós do workflow

```
Diariamente (06:00) → HTTP CSV OMIE+Perdas → HTTP CSV Tarifários → Cálculos → Notificação
```

### Exemplo de resultado (14/08/2026)

- OMIE média 30 dias: **123.37 €/MWh** | Perdas rede: 1.1488
- Tarifário atual (EDP Digital -20%): **96.43€**
- Melhor fixo (EDP Oferta Jovem ≤35): **84.61€** (−35% 3 meses) → *105.13€* após promoção (−15%)
- Indexado G9: **154.90€** | EDP Indexada Média: **159.90€**
- Recomendação: **EDP Oferta Jovem (≤35 anos)** — mas só compensa durante os 3 meses promocionais
