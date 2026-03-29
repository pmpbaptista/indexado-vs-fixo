import requests
import csv
import unicodedata
import argparse
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
    # --- Config adicionais (alinhadas ao workflow n8n)
    "ciclo": "Simples",
    "filtros": {
        "segmento": "",
        "faturacao": "",
        "pagamento": "",
    },
    "fator_perdas": 1.0,
    "indexado": {"nome": "G9 | Smart Index"},
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

    return (sum(precos) / len(precos) if precos else 60.0, len(precos))


# ==============================================================================
# 📊 PROCESSAMENTO
# ==============================================================================


def executar_analise(output_json=False):
    media_omie, num_precos = obter_media_omie()

    try:
        res = requests.get(CONFIG["url_tarifarios"], timeout=20)
        csv_linhas = res.text.splitlines()
    except Exception:
        print("❌ Erro ao descarregar base de dados de tarifários.")
        return

    # --- Parse CSV com mapeamento de colunas (como em n8n/calculos.js)
    reader = csv.reader(csv_linhas)
    try:
        header = next(reader)
    except StopIteration:
        print("❌ CSV vazio.")
        return

    def norm(s):
        return (s or "").strip().lower()

    # mapa nome_lower -> indice (removendo acentos)
    def remove_accents(s):
        return "".join(
            ch
            for ch in unicodedata.normalize("NFD", s or "")
            if unicodedata.category(ch) != "Mn"
        )

    cab = [remove_accents(c.replace('"', "").strip().lower()) for c in header]

    def col(name):
        name = remove_accents(name.lower())
        for i, c in enumerate(cab):
            if name in c:
                return i
        return -1

    # índices relevantes (nomes baseados no CSV usado pelo workflow)
    C = {
        "comercializador": col("comercializador"),
        "nome": col("nome"),
        "tipo": col("tipo"),
        "ciclo": col("opcao_horaria"),
        "potKva": col("potencia_kva"),
        "energia": col("preco_energia_simples"),
        "potDia": col("preco_potencia_dia"),
        "tarEnIncl": col("tar_incluida_energia"),
        "tarPotIncl": col("tar_incluida_potencia"),
        "tseIncl": col("financiamento_tse"),
        "segmento": col("segmento"),
        "faturacao": col("faturacao"),
        "pagamento": col("pagamento"),
        "descFatura": col("desconto_fatura"),
    }

    def parse_bool(v):
        s = (v or "").strip().lower()
        return s in ("true", "sim", "1")

    def parse_num(v):
        try:
            return float((v or "0").replace(",", "."))
        except Exception:
            return 0.0

    def norm_text(s):
        try:
            return remove_accents((s or "").lower())
        except Exception:
            return ""

    meu_atual = None
    melhor_fixo = None

    ciclo_chave = norm_text(CONFIG.get("ciclo", "Simples")).split("-")[0].split()[0]

    fixos = []
    for row in reader:
        try:
            # garantir comprimento suficiente
            if len(row) < 3:
                continue

            tipo = (
                row[C["tipo"]] if C["tipo"] != -1 and C["tipo"] < len(row) else ""
            ).strip()
            if "fixo" not in tipo.lower():
                continue

            potKva = (
                parse_num(row[C["potKva"]])
                if C["potKva"] != -1 and C["potKva"] < len(row)
                else 0
            )
            if abs(potKva - CONFIG["potencia_kva"]) > 0.1:
                continue

            # ciclo/opcao horaria
            ciclo_row = (
                norm_text(row[C["ciclo"]])
                if C["ciclo"] != -1 and C["ciclo"] < len(row)
                else ""
            )
            if ciclo_chave and ciclo_chave not in ciclo_row:
                continue

            # filtros opcionais (segmento, faturacao, pagamento)
            seg = (
                norm_text(row[C["segmento"]])
                if C["segmento"] != -1 and C["segmento"] < len(row)
                else ""
            )
            faturacao = (
                norm_text(row[C["faturacao"]])
                if C["faturacao"] != -1 and C["faturacao"] < len(row)
                else ""
            )
            pagamento = (
                norm_text(row[C["pagamento"]])
                if C["pagamento"] != -1 and C["pagamento"] < len(row)
                else ""
            )

            f = CONFIG.get("filtros", {})
            if f.get("segmento") == "Residencial" and "domestico" not in seg:
                continue
            if f.get("segmento") == "Empresarial" and (
                "nao domestico" not in seg and "nao-domestico" not in seg
            ):
                continue
            if (
                f.get("faturacao")
                and f.get("faturacao").strip()
                and f.get("faturacao").strip().lower() not in faturacao
            ):
                continue
            if (
                f.get("pagamento")
                and f.get("pagamento").strip()
                and f.get("pagamento").strip().lower() not in pagamento
            ):
                continue

            nome_csv = (
                row[C["nome"]].strip()
                if C["nome"] != -1 and C["nome"] < len(row)
                else ""
            )

            energiaBruta = (
                parse_num(row[C["energia"]])
                if C["energia"] != -1 and C["energia"] < len(row)
                else 0
            )
            potDiaBruta = (
                parse_num(row[C["potDia"]])
                if C["potDia"] != -1 and C["potDia"] < len(row)
                else 0
            )
            if energiaBruta <= 0 or potDiaBruta <= 0:
                continue

            tarEnIncl = (
                parse_bool(row[C["tarEnIncl"]])
                if C["tarEnIncl"] != -1 and C["tarEnIncl"] < len(row)
                else False
            )
            tarPotIncl = (
                parse_bool(row[C["tarPotIncl"]])
                if C["tarPotIncl"] != -1 and C["tarPotIncl"] < len(row)
                else False
            )
            tseIncl = (
                parse_bool(row[C["tseIncl"]])
                if C["tseIncl"] != -1 and C["tseIncl"] < len(row)
                else False
            )

            # Extrai componente comercial (sem TAR)
            energiaComercial = (
                energiaBruta - CONFIG["tar_energia_kwh"] if tarEnIncl else energiaBruta
            )
            potComercial = (
                potDiaBruta - CONFIG["tar_potencia_dia"] if tarPotIncl else potDiaBruta
            )

            energiaFinal = (
                energiaComercial
                + CONFIG["tar_energia_kwh"]
                + (0 if tseIncl else CONFIG["tse"])
            )
            potFinal = potComercial + CONFIG["tar_potencia_dia"]

            descFatura = (
                parse_num(row[C["descFatura"]])
                if C["descFatura"] != -1 and C["descFatura"] < len(row)
                else 0
            )
            descTotal = descFatura * (CONFIG["dias_historico"] / 30.0)

            custoEnergia = energiaFinal * CONFIG["consumo_kwh"]
            custoPot = potFinal * CONFIG["dias_historico"]
            custoTotal = custoEnergia + custoPot - descTotal

            fixos.append(
                {
                    "nome": nome_csv,
                    "comercializador": row[C["comercializador"]].strip()
                    if C["comercializador"] != -1 and C["comercializador"] < len(row)
                    else "",
                    "energia_kwh": energiaFinal,
                    "potencia_dia": potFinal,
                    "custo": custoTotal,
                    "tar_en_incl": tarEnIncl,
                    "tar_pot_incl": tarPotIncl,
                    "tse_incl": tseIncl,
                }
            )

            # identificar meu atual
            if CONFIG["tarifario_atual_nome"].lower() in nome_csv.lower():
                if not meu_atual or custoTotal < meu_atual["custo"]:
                    meu_atual = {"nome": nome_csv, "custo": custoTotal}

        except Exception:
            continue

    # escolher melhor fixo
    if fixos:
        fixos_sorted = sorted(fixos, key=lambda x: x["custo"])
        melhor_fixo = fixos_sorted[0]
    else:
        melhor_fixo = None

    # utilitários de arredondamento (como n8n)
    def r4(v):
        return round(v, 4)

    def r2(v):
        return round(v, 2)

    # Cálculo Indexado
    p_kwh_idx = (
        (media_omie / 1000) * CONFIG.get("fator_perdas", 1.0)
        + CONFIG["margem_indexado"]
        + CONFIG["tar_energia_kwh"]
        + CONFIG["tse"]
    )
    custo_idx = (CONFIG["consumo_kwh"] * p_kwh_idx) + (
        CONFIG["dias_historico"] * CONFIG["tar_potencia_dia"]
    )

    # --- OUTPUT JSON (modelo n8n) ---
    custo_fixo = melhor_fixo["custo"] if melhor_fixo else 0

    # break-even (€/MWh) em formato MWh (n8n calcula em MWh e arredonda a 2 decimais)
    if custo_fixo > 0 and CONFIG["consumo_kwh"] > 0:
        breakeven_kWh = (
            (custo_fixo - CONFIG["dias_historico"] * CONFIG["tar_potencia_dia"])
            / CONFIG["consumo_kwh"]
            - CONFIG["margem_indexado"]
            - CONFIG["tar_energia_kwh"]
            - CONFIG["tse"]
        ) / CONFIG.get("fator_perdas", 1.0)
    else:
        breakeven_kWh = 0
    breakeven_MWh = r2(breakeven_kWh * 1000)

    poupanca = r2(abs(custo_fixo - custo_idx))
    fixo_mais_barato = custo_fixo <= r2(custo_idx)
    recomendacao = (
        melhor_fixo["nome"]
        if fixo_mais_barato
        else CONFIG.get("indexado_nome", "Indexado")
    )

    resumo = f"📊 *ANÁLISE OMIE* ({date.today().strftime('%d/%m/%Y')})\n"
    resumo += "━━━━━━━━━━━━━━━━━━━━\n\n"
    resumo += f"⚡ *MERCADO OMIE (últimos {CONFIG['dias_historico']} dias):*\n"
    resumo += f"• Média: *{r2(media_omie)} €/MWh*\n"
    resumo += f"• Break-even: {breakeven_MWh} €/MWh\n\n"
    resumo += "🔒 *MELHOR FIXO (automatico):*\n"
    if melhor_fixo:
        resumo += f"• {melhor_fixo['nome']}\n"
        resumo += f"• Energia: {r4(melhor_fixo['energia_kwh'])} €/kWh | Potência: {r4(melhor_fixo['potencia_dia'])} €/dia\n"
        resumo += f"• Custo estimado: *{r2(melhor_fixo['custo'])}€*\n\n"
    resumo += f"📈 *INDEXADO ({CONFIG.get('indexado', {}).get('nome', 'Indexado')}):*\n"
    resumo += f"• Preço kWh: {r4(p_kwh_idx)} €/kWh\n"
    resumo += f"• Custo estimado: *{r2(custo_idx)}€*\n\n"
    resumo += "━━━━━━━━━━━━━━━━━━━━\n"
    maisBaratoLabel = (
        f"🏆 FIXO mais barato — poupa {poupanca}€"
        if fixo_mais_barato
        else f"🏆 INDEXADO mais barato — poupa {poupanca}€"
    )
    resumo += f"💡 *{maisBaratoLabel}*\n"
    resumo += f"   Recomendação: *{recomendacao}*"

    output = {
        "data_analise": date.today().strftime("%d/%m/%Y"),
        "media_omie_eur_mwh": r2(media_omie),
        "breakeven_eur_mwh": breakeven_MWh,
        "omie_acima_breakeven": media_omie > breakeven_MWh,
        "melhor_fixo_nome": melhor_fixo["nome"] if melhor_fixo else "",
        "melhor_fixo_comercializador": melhor_fixo["comercializador"]
        if melhor_fixo
        else "",
        "melhor_fixo_fonte": "automatico (CSV Tiago Felícia)" if melhor_fixo else "",
        "melhor_fixo_energia_kwh": r4(melhor_fixo["energia_kwh"]) if melhor_fixo else 0,
        "melhor_fixo_potencia_dia": r4(melhor_fixo["potencia_dia"])
        if melhor_fixo
        else 0,
        "custo_melhor_fixo_eur": r2(custo_fixo),
        "indexado_nome": CONFIG.get("indexado", {}).get("nome", "Indexado"),
        "indexado_preco_kwh": r4(p_kwh_idx),
        "custo_indexado_eur": r2(custo_idx),
        "poupanca_eur": poupanca,
        "recomendacao": recomendacao,
        "fixo_mais_barato": fixo_mais_barato,
        "num_precos_omie": num_precos,
        "resumo": resumo,
    }

    import json

    if output_json:
        print(json.dumps([output], ensure_ascii=False, indent=2))
        return output

    # Caso contrário imprimir resumo formatado (humano)
    print(f"\n📊 ANÁLISE DE ELETRICIDADE ({output['data_analise']})")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"⚡ MERCADO (OMIE):  {output['media_omie_eur_mwh']:.2f} €/MWh")
    print(f"🏠 ATUAL ({CONFIG['tarifario_atual_nome']}):")
    if (
        output["melhor_fixo_nome"]
        and output["melhor_fixo_nome"]
        .lower()
        .find(CONFIG["tarifario_atual_nome"].lower())
        != -1
    ):
        print(f"   • Custo Mensal: {output['custo_melhor_fixo_eur']:.2f}€")
    else:
        if meu_atual:
            print(f"   • Custo Mensal: {meu_atual['custo']:.2f}€")
        else:
            print("   • [Tarifário não encontrado no simulador]")

    print(f"\n🏆 MELHOR FIXO: {output['melhor_fixo_nome'] or 'N/A'}")
    print(f"   • Custo Mensal: {output['custo_melhor_fixo_eur']:.2f}€")

    print("\n📈 INDEXADO (Média OMIE):")
    print(f"   • Custo Mensal: {output['custo_indexado_eur']:.2f}€")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if meu_atual and (meu_atual["custo"] - output["custo_indexado_eur"]) > 0:
        ganho_val = meu_atual["custo"] - output["custo_indexado_eur"]
        print(f"💡 RECOMENDAÇÃO: Mudar para Indexado poupa-te {ganho_val:.2f}€/mês.")
    else:
        print("💡 RECOMENDAÇÃO: O teu tarifário atual está otimizado.")

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Análise tarifários - output json se solicitado"
    )
    parser.add_argument(
        "-o", "--output", choices=["json"], help='Se "json", imprime o JSON de saída'
    )
    args = parser.parse_args()
    executar_analise(output_json=(args.output == "json"))
