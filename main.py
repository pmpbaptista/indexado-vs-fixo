import requests
import csv
import io
import sys
import unicodedata
import argparse
from datetime import date, timedelta

# ==============================================================================
# ⚙️ CONFIGURAÇÃO
# ==============================================================================

CONFIG = {
    # ── Consumo e contrato (valores da fatura EDP de jul/ago 2026) ──
    "consumo_kwh": 614,      # kWh reais faturados
    "dias_faturacao": 31,    # dias do período da fatura
    "potencia_kva": 6.90,
    "ciclo": "Simples",      # "Simples" | "Bi-horário" | "Tri-horário"
    # Tarifário fixo atual (encontrado no CSV do Tiago Felícia)
    "tarifario_atual_nome": "EDP | Eletricidade · Digital · Campanha -20%",

    # ── Janela da média OMIE ──
    "dias_omie": 30,

    # ── Filtros para escolha do melhor fixo ──
    "filtros": {
        "segmento": "Residencial",   # "Residencial" | "Empresarial" | ""
        "faturacao": "eletrónica",   # palavra parcial (ex: "eletrónica", "papel")
        "pagamento": "Débito",       # palavra parcial (ex: "Débito", "Multibanco")
    },

    # ── Tarifas reguladas ERSE 2026 ──
    "tar_energia_kwh": 0.0607,   # €/kWh — tarifa simples BTN
    "tar_potencia_dia": 0.3436,  # €/dia — para 6.9 kVA
    "tse": 0.0020666,            # €/kWh — Financiamento TSE (Constantes.csv Tiago Felícia)

    # ── Tarifário INDEXADO (G9 | Smart Dynamic SPOT 8!) ──
    # Fórmula oficial (calculos.py Tiago Felícia + Indexados.csv):
    #   preço_kwh = OMIE_€/kWh * G9_FA * perdas + G9_CGS + G9_AC + TAR_energia (+ TSE se não incluído)
    # G9_CGS + G9_AC = margem do comercializador
    "indexado": {
        "nome": "G9 | Smart Dynamic SPOT 8!",
        "g9_fa": 1.02,           # fator de ajuste G9 (Constantes.csv G9_K1)
        "margem_kwh": 0.0155,    # G9_CGS (0.01) + G9_AC (0.0055) (defaults em calculos.py)
        "tse_incluido": True,    # CSV Indexados: financiamento_tse_incluido=True
        "potencia_dia": 0.6242,  # 6.9 kVA, Simples (Indexados.csv; tar_incluida_potencia=True → preço final)
    },
    # Perdas de rede por ciclo (valores do CSV OMIE_PERDAS_CICLOS)
    "fator_perdas": 1.1488,      # média anual Simples (BTN). Atualizado dinamicamente se possível

    # ── Fontes de dados (Tiago Felícia) ──
    "url_tarifarios": "https://huggingface.co/spaces/tiagofelicia/simulador-tarifarios-eletricidade/resolve/main/data/csv/Tarifarios_fixos.csv",
    "url_indexados": "https://huggingface.co/spaces/tiagofelicia/simulador-tarifarios-eletricidade/resolve/main/data/csv/Indexados.csv",
    "url_perdas": "https://huggingface.co/spaces/tiagofelicia/simulador-tarifarios-eletricidade/resolve/main/data/csv/OMIE_PERDAS_CICLOS.csv",
    "url_constantes": "https://huggingface.co/spaces/tiagofelicia/simulador-tarifarios-eletricidade/resolve/main/data/csv/Constantes.csv",
}

# ==============================================================================
# ⚡ OBTENÇÃO DINÂMICA OMIE
# ==============================================================================


def obter_media_omie():
    """Média OMIE dos últimos dias via CSV consolidado do Tiago Felícia
    (15-min intervals, inclui OMIE + perdas). Fallback: ficheiros diários OMIE."""
    # 1) Tenta CSV consolidado (robusto, 1 request)
    try:
        res = requests.get(CONFIG["url_perdas"], timeout=20)
        if res.status_code == 200:
            rows = list(csv.reader(io.StringIO(res.text)))
            hdr = rows[0]
            idx = {c.strip().lstrip("\ufeff"): i for i, c in enumerate(hdr)}
            col_data = idx.get("Data")
            col_omie = idx.get("OMIE")
            if col_data is not None and col_omie is not None:
                hoje = date.today()
                precos = []
                for r in rows[1:]:
                    if len(r) <= max(col_data, col_omie):
                        continue
                    try:
                        ds = r[col_data].strip()
                        d = date(int(ds[6:10]), int(ds[0:2]), int(ds[3:5]))
                    except Exception:
                        continue
                    if d <= hoje and (hoje - d).days <= CONFIG["dias_omie"]:
                        try:
                            precos.append(float(r[col_omie]))
                        except Exception:
                            continue
                if precos:
                    return sum(precos) / len(precos), len(precos)
    except Exception:
        pass

    # 2) Fallback: ficheiros diários OMIE
    print("⚠️  CSV consolidado indisponível — a usar ficheiros diários OMIE.")
    hoje = date.today()
    precos = []
    headers = {"User-Agent": "Mozilla/5.0"}

    for i in range(CONFIG["dias_omie"] + 1):
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


def obter_perdas_reais(ciclo="Simples"):
    """Tenta obter as perdas de rede reais do período pelo CSV do Tiago Felícia.
    Retorna (fator, valores_de_hoje). Se falhar, devolve o valor do CONFIG."""
    try:
        res = requests.get(CONFIG["url_perdas"], timeout=20)
        if res.status_code != 200:
            return CONFIG["fator_perdas"], 0
        rows = list(csv.reader(io.StringIO(res.text)))
        hdr = rows[0]
        idx = {c.strip().lstrip("\ufeff"): i for i, c in enumerate(hdr)}
        col_omie = idx.get("OMIE")
        col_perd = idx.get("Perdas")
        if col_omie is None or col_perd is None:
            return CONFIG["fator_perdas"], 0

        hoje = date.today()
        perdas = []
        for r in rows[1:]:
            if len(r) <= max(col_omie, col_perd):
                continue
            try:
                ds = r[0].strip()
                d = date(int(ds[6:10]), int(ds[0:2]), int(ds[3:5]))  # MM/DD/YYYY
            except Exception:
                continue
            if d <= hoje and (hoje - d).days <= CONFIG["dias_omie"]:
                try:
                    perdas.append(float(r[col_perd]))
                except Exception:
                    continue
        if perdas:
            return (sum(perdas) / len(perdas)), len(perdas)
    except Exception:
        pass
    return CONFIG["fator_perdas"], 0


# ==============================================================================
# 📊 PROCESSAMENTO
# ==============================================================================


def executar_analise(output_json=False):
    media_omie, num_precos = obter_media_omie()
    fator_perdas, num_perdas = obter_perdas_reais()
    if num_perdas:
        print(f"🔎 Perdas reais do período: {fator_perdas:.4f} ({num_perdas} valores)", file=sys.stderr)
    else:
        print(f"🔎 Perdas (constante): {fator_perdas:.4f}", file=sys.stderr)

    try:
        res = requests.get(CONFIG["url_tarifarios"], timeout=20)
        csv_linhas = res.text.splitlines()
    except Exception:
        print("❌ Erro ao descarregar base de dados de tarifários.")
        return

    reader = csv.reader(csv_linhas)
    try:
        header = next(reader)
    except StopIteration:
        print("❌ CSV vazio.")
        return

    def norm(s):
        return (s or "").strip().lower()

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
        "site": col("site_adesao"),
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

            ciclo_row = (
                norm_text(row[C["ciclo"]])
                if C["ciclo"] != -1 and C["ciclo"] < len(row)
                else ""
            )
            if ciclo_chave and ciclo_chave not in ciclo_row:
                continue

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
                and norm_text(f.get("faturacao")) not in faturacao
            ):
                continue
            if (
                f.get("pagamento")
                and f.get("pagamento").strip()
                and norm_text(f.get("pagamento")) not in pagamento
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
            descTotal = descFatura * (CONFIG["dias_faturacao"] / 30.0)

            custoEnergia = energiaFinal * CONFIG["consumo_kwh"]
            custoPot = potFinal * CONFIG["dias_faturacao"]
            custoTotal = custoEnergia + custoPot - descTotal

            fixos.append(
                {
                    "nome": nome_csv,
                    "comercializador": row[C["comercializador"]].strip()
                    if C["comercializador"] != -1 and C["comercializador"] < len(row)
                    else "",
                    "site": (
                        row[C["site"]].strip()
                        if C["site"] != -1 and C["site"] < len(row)
                        else ""
                    ),
                    "energia_kwh": energiaFinal,
                    "potencia_dia": potFinal,
                    "custo": custoTotal,
                    "tar_en_incl": tarEnIncl,
                    "tar_pot_incl": tarPotIncl,
                    "tse_incl": tseIncl,
                }
            )

            if CONFIG["tarifario_atual_nome"].lower() in nome_csv.lower():
                if not meu_atual or custoTotal < meu_atual["custo"]:
                    meu_atual = {"nome": nome_csv, "custo": custoTotal}

        except Exception:
            continue

    if fixos:
        fixos_sorted = sorted(fixos, key=lambda x: x["custo"])
        melhor_fixo = fixos_sorted[0]
    else:
        melhor_fixo = None

    # Detetar custo pós-promoção do melhor fixo (ex: Oferta Jovem -35% 3 meses → depois -15%).
    # O CSV lista a mesma oferta em linhas separadas; procuramos a variante "Depois dos 3 meses".
    pos_promo = None
    if melhor_fixo:
        melhor_base = (melhor_fixo["nome"].split("(")[0] or melhor_fixo["nome"]).strip().lower()
        for f in fixos:
            if f["nome"] == melhor_fixo["nome"]:
                continue
            f_base = (f["nome"].split("(")[0] or f["nome"]).strip().lower()
            if (
                f_base
                and f_base == melhor_base
                and "depois" in f["nome"].lower()
            ):
                pos_promo = {
                    "nome": f["nome"],
                    "energia_kwh": f["energia_kwh"],
                    "potencia_dia": f["potencia_dia"],
                    "custo": f["custo"],
                }
                break

    def r4(v):
        return round(v, 4)

    def r2(v):
        return round(v, 2)

    # ================= Cálculo Indexado (G9 | Smart Dynamic SPOT 8!) =================
    idx = CONFIG["indexado"]
    # Fórmula oficial: OMIE_€/kWh * G9_FA * perdas + margem + TAR_energia + (0 se TSE incluído)
    p_kwh_idx = (
        (media_omie / 1000) * idx.get("g9_fa", 1.02) * fator_perdas
        + idx.get("margem_kwh", 0.0155)
        + CONFIG["tar_energia_kwh"]
        + (0 if idx.get("tse_incluido", False) else CONFIG["tse"])
    )
    # Potência do indexado (Indexados.csv 6.9 kVA — já inclui TAR potência)
    pot_idx = idx.get("potencia_dia", CONFIG["tar_potencia_dia"])
    custo_idx = (CONFIG["consumo_kwh"] * p_kwh_idx) + (
        CONFIG["dias_faturacao"] * pot_idx
    )

    # ================= Referência: EDP Indexada Média (mesmo comercializador) =================
    # Fórmula oficial: OMIE_€/kWh * EDP_M_Perdas * EDP_M_K1 + EDP_M_K2
    p_kwh_edp = (
        (media_omie / 1000) * 1.164 * 1.1 + 0.0185
        + CONFIG["tar_energia_kwh"]
    )
    # Potência EDP Média 6.9 kVA (Indexados.csv 0.4607 €/dia — já inclui TAR potência)
    pot_edp = 0.4607
    custo_edp_idx = (CONFIG["consumo_kwh"] * p_kwh_edp) + (
        CONFIG["dias_faturacao"] * pot_edp
    )

    # ================= Break-even =================
    custo_fixo = melhor_fixo["custo"] if melhor_fixo else 0

    if custo_fixo > 0 and CONFIG["consumo_kwh"] > 0:
        breakeven_kWh = (
            (custo_fixo - CONFIG["dias_faturacao"] * pot_idx)
            / CONFIG["consumo_kwh"]
            - idx.get("margem_kwh", 0.0155)
            - CONFIG["tar_energia_kwh"]
            - (0 if idx.get("tse_incluido", False) else CONFIG["tse"])
        ) / (idx.get("g9_fa", 1.02) * fator_perdas)
    else:
        breakeven_kWh = 0
    breakeven_MWh = r2(breakeven_kWh * 1000)

    # ================= Comparações =================
    custo_atual = meu_atual["custo"] if meu_atual else None

    poupanca = r2(abs(custo_fixo - custo_idx))
    fixo_mais_barato = custo_fixo <= r2(custo_idx)

    # Recomendação global: entre o atual, o melhor fixo e o indexado
    opcoes = []
    if custo_atual is not None:
        opcoes.append(("atual", custo_atual))
    if melhor_fixo:
        opcoes.append(("melhor_fixo", custo_fixo))
    opcoes.append(("indexado", custo_idx))
    melhor_tipo, melhor_custo = min(opcoes, key=lambda x: x[1])

    if melhor_tipo == "atual":
        recomendacao = "Manter o tarifário atual"
    elif melhor_tipo == "melhor_fixo":
        recomendacao = melhor_fixo["nome"]
    else:
        recomendacao = idx["nome"]

    # Poupanca relativamente ao que paga hoje
    poupanca_vs_atual = None
    if custo_atual is not None:
        poupanca_vs_atual = r2(abs(custo_atual - min(x[1] for x in opcoes)))

    resumo = f"📊 *ANÁLISE OMIE* ({date.today().strftime('%d/%m/%Y')})\n"
    resumo += "━━━━━━━━━━━━━━━━━━━━\n\n"
    resumo += f"⚡ *MERCADO OMIE (últimos {CONFIG['dias_omie']} dias):*\n"
    resumo += f"• Média: *{r2(media_omie)} €/MWh*\n"
    resumo += f"• Perdas rede: {fator_perdas:.4f} | Break-even: {breakeven_MWh} €/MWh\n\n"

    if custo_atual is not None:
        resumo += f"📄 *TARIFÁRIO ATUAL:*\n• {meu_atual['nome']}\n• Custo estimado: *{r2(custo_atual)}€*\n\n"

    fonte = "automatico (CSV Tiago Felícia)" if melhor_fixo else "N/A"
    resumo += f"🔒 *MELHOR FIXO ({fonte}):*\n"
    if melhor_fixo:
        resumo += f"• {melhor_fixo['nome']}\n"
        resumo += f"• Energia: {r4(melhor_fixo['energia_kwh'])} €/kWh | Potência: {r4(melhor_fixo['potencia_dia'])} €/dia\n"
        resumo += f"• Custo estimado: *{r2(melhor_fixo['custo'])}€*\n"
        if pos_promo:
            resumo += f"• ⚠️ Após promoção ({pos_promo['nome'].split('·')[-1].strip()}): {r4(pos_promo['energia_kwh'])} €/kWh → *{r2(pos_promo['custo'])}€*\n"
        resumo += "\n"

    resumo += f"📈 *INDEXADO ({idx['nome']}):*\n"
    resumo += f"• Preço kWh: {r4(p_kwh_idx)} €/kWh | Potência: {r4(pot_idx)} €/dia\n"
    resumo += f"• Custo estimado: *{r2(custo_idx)}€*\n\n"
    resumo += f"📈 *REF. EDP Indexada Média:* {r2(custo_edp_idx)}€\n\n"
    resumo += "━━━━━━━━━━━━━━━━━━━━\n"

    if melhor_tipo == "atual":
        label = f"🏆 ATUAL é a melhor opção — poupas {poupanca_vs_atual}€ vs 2ª melhor"
    elif melhor_tipo == "melhor_fixo":
        label = f"🏆 FIXO mais barato — poupa {poupanca}€ vs indexado"
    else:
        label = f"🏆 INDEXADO mais barato — poupa {poupanca}€"
    resumo += f"💡 *{label}*\n"
    resumo += f"   Recomendação: *{recomendacao}*"

    output = {
        "data_analise": date.today().strftime("%d/%m/%Y"),
        "media_omie_eur_mwh": r2(media_omie),
        "perdas_rede": round(fator_perdas, 4),
        "breakeven_eur_mwh": breakeven_MWh,
        "omie_acima_breakeven": media_omie > breakeven_MWh,
        # Atual
        "tarifario_atual_nome": meu_atual["nome"] if meu_atual else "",
        "custo_atual_eur": r2(custo_atual) if custo_atual is not None else 0,
        "poupanca_vs_atual_eur": poupanca_vs_atual if poupanca_vs_atual is not None else 0,
        # Melhor fixo
        "melhor_fixo_nome": melhor_fixo["nome"] if melhor_fixo else "",
        "melhor_fixo_comercializador": melhor_fixo["comercializador"]
        if melhor_fixo
        else "",
        "melhor_fixo_site": melhor_fixo["site"] if melhor_fixo else "",
        "melhor_fixo_fonte": "automatico (CSV Tiago Felícia)" if melhor_fixo else "",
        "melhor_fixo_energia_kwh": r4(melhor_fixo["energia_kwh"]) if melhor_fixo else 0,
        "melhor_fixo_potencia_dia": r4(melhor_fixo["potencia_dia"])
        if melhor_fixo
        else 0,
        "custo_melhor_fixo_eur": r2(custo_fixo),
        "melhor_fixo_pos_promo_custo_eur": r2(pos_promo["custo"])
        if pos_promo
        else None,
        "melhor_fixo_pos_promo_energia_kwh": r4(pos_promo["energia_kwh"])
        if pos_promo
        else None,
        # Indexado
        "indexado_nome": idx["nome"],
        "indexado_preco_kwh": r4(p_kwh_idx),
        "custo_indexado_eur": r2(custo_idx),
        "indexado_edp_media_custo_eur": r2(custo_edp_idx),
        # Comparação
        "poupanca_eur": poupanca,
        "recomendacao": recomendacao,
        "fixo_mais_barato": fixo_mais_barato,
        "num_precos_omie": num_precos,
        "consumo_kwh": CONFIG["consumo_kwh"],
        "dias_faturacao": CONFIG["dias_faturacao"],
        "resumo": resumo,
    }

    import json

    if output_json:
        print(json.dumps([output], ensure_ascii=False, indent=2))
        return output

    print(resumo.replace("*", ""))

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
