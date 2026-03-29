import requests
import csv
from datetime import date, timedelta

# ==============================================================================
# ⚙️ CONFIGURAÇÃO
# ==============================================================================

CONFIG = {
    "tarifario_atual_nome": "G9 | Vantagem+",
    # Se definires como None, o script vai buscar os valores ao CSV automaticamente
    "manual_preco_kwh": None,  # Ex: 0.1348
    "manual_preco_potencia": None,  # Ex: 0.4498 (preço por dia)
    "consumo_kwh": 700,
    "dias_historico": 30,
    "potencia_kva": 6.90,
    # Valores de referência para o Indexado e Fallbacks
    "tar_energia_kwh": 0.0607,
    "tar_potencia_dia": 0.3436,
    "tse": 0.0026,
    "margem_indexado": 0.0150,
    "url_tarifarios": "https://huggingface.co/spaces/tiagofelicia/simulador-tarifarios-eletricidade/resolve/main/data/csv/Tarifarios_fixos.csv",
}

# ==============================================================================
# ⚡ OBTENÇÃO DINÂMICA OMIE
# ==============================================================================


def obter_media_omie():
    print(f"🔎 A calcular média OMIE (últimos {CONFIG['dias_historico']} dias)...")
    hoje = date.today()
    precos = []
    headers = {"User-Agent": "Mozilla/5.0"}

    for i in range(CONFIG["dias_historico"] + 1):
        dStr = (hoje - timedelta(days=i)).strftime("%Y%m%d")
        url = f"https://www.omie.es/en/file-download?parents=marginalpdbcpt&filename=marginalpdbcpt_{dStr}.1"
        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                for linha in r.text.splitlines():
                    p = linha.strip().split(";")
                    if (
                        len(p) >= 5
                        and p[0].strip().isdigit()
                        and len(p[0].strip()) == 4
                    ):
                        precos.append(float(p[4].replace(",", ".")))
        except Exception:
            continue

    return sum(precos) / len(precos) if precos else 60.0


# ==============================================================================
# 📊 PROCESSAMENTO
# ==============================================================================


def executar_analise():
    media_omie = obter_media_omie()

    try:
        res = requests.get(CONFIG["url_tarifarios"], timeout=20)
        csv_linhas = res.text.splitlines()
    except Exception:
        print("❌ Erro ao descarregar base de dados de tarifários.")
        return

    reader = csv.reader(csv_linhas)
    next(reader)

    meu_atual = None
    melhor_fixo = None

    for row in reader:
        if len(row) < 11:
            continue
        try:
            nome_csv = row[1].strip()
            tipo_csv = row[3].strip().lower()
            pot_csv = float(row[8].replace(",", "."))

            if abs(pot_csv - CONFIG["potencia_kva"]) > 0.05:
                continue

            # Preços brutos do CSV
            p_en_csv = float(row[10].replace(",", "."))
            p_pot_csv = float(row[9].replace(",", "."))

            # Verificar se taxas estão incluídas na linha do CSV
            tar_en_inc = "true" in row[16].lower()
            tar_pot_inc = "true" in row[17].lower()
            tse_inc = "true" in row[18].lower() if len(row) > 18 else False

            # Lógica de Preço: Manual vs CSV
            # Se CONFIG for None, usa o do CSV. Se houver valor manual, usa o manual.
            val_en = (
                CONFIG["manual_preco_kwh"]
                if CONFIG["manual_preco_kwh"] is not None
                else p_en_csv
            )
            val_pot = (
                CONFIG["manual_preco_potencia"]
                if CONFIG["manual_preco_potencia"] is not None
                else p_pot_csv
            )

            # Cálculo Final (Soma taxas se não estiverem incluídas no valor)
            e_final = (
                val_en
                + (0 if tar_en_inc else CONFIG["tar_energia_kwh"])
                + (0 if tse_inc else CONFIG["tse"])
            )
            p_final = val_pot + (0 if tar_pot_inc else CONFIG["tar_potencia_dia"])

            custo_total = (e_final * CONFIG["consumo_kwh"]) + (
                p_final * CONFIG["dias_historico"]
            )
            info = {"nome": nome_csv, "custo": custo_total}

            # 1. Identificar o MEU (Pelo Nome)
            if CONFIG["tarifario_atual_nome"].lower() in nome_csv.lower():
                if not meu_atual or custo_total < meu_atual["custo"]:
                    meu_atual = info

            # 2. Identificar o MELHOR FIXO do mercado
            if "fixo" in tipo_csv:
                if not melhor_fixo or custo_total < melhor_fixo["custo"]:
                    melhor_fixo = info
        except Exception:
            continue

    # Cálculo Indexado
    p_kwh_idx = (
        (media_omie / 1000)
        + CONFIG["margem_indexado"]
        + CONFIG["tar_energia_kwh"]
        + CONFIG["tse"]
    )
    custo_idx = (CONFIG["consumo_kwh"] * p_kwh_idx) + (
        CONFIG["dias_historico"] * CONFIG["tar_potencia_dia"]
    )

    # --- OUTPUT ---
    c_atual = meu_atual["custo"] if meu_atual else 0
    c_fixo = melhor_fixo["custo"] if melhor_fixo else 0
    ganho = c_atual - custo_idx if c_atual > 0 else 0

    print(f"\n📊 ANÁLISE DE ELETRICIDADE ({date.today().strftime('%d/%m/%Y')})")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"⚡ MERCADO (OMIE):  {media_omie:.2f} €/MWh")
    print(f"🏠 ATUAL ({CONFIG['tarifario_atual_nome']}):")
    if meu_atual:
        print(f"   • Custo Mensal: {c_atual:.2f}€")
        print(f"   📉 Ganho vs Indexado: +{ganho:.2f}€")
    else:
        print("   • [Tarifário não encontrado no simulador]")

    print(f"\n🏆 MELHOR FIXO: {melhor_fixo['nome'] if melhor_fixo else 'N/A'}")
    print(f"   • Custo Mensal: {c_fixo:.2f}€")

    print("\n📈 INDEXADO (Média OMIE):")
    print(f"   • Custo Mensal: {custo_idx:.2f}€")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if ganho > 0:
        print(f"💡 RECOMENDAÇÃO: Mudar para Indexado poupa-te {ganho:.2f}€/mês.")
    else:
        print("💡 RECOMENDAÇÃO: O teu tarifário atual está otimizado.")


if __name__ == "__main__":
    executar_analise()
