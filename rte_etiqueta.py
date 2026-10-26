"""
Etiqueta Única RTE Rodonaves  ->  PDF (uma página por volume) para subir na Platinum.

Fluxo:  XML da NF-e  ->  API RTE (token + malha)  ->  código de 30 dígitos  ->  PDF

Uso (linha de comando):
    python rte_etiqueta.py nota.xml                # chama a API da RTE
    python rte_etiqueta.py nota.xml --mock         # usa o retorno de exemplo do PDF da RTE
    python rte_etiqueta.py nota.xml -o saida.pdf

Uso dentro do bot.py:
    from rte_etiqueta import gerar_etiqueta_pdf
    pdf_bytes = gerar_etiqueta_pdf(xml_bytes)

Variáveis de ambiente (arquivo .env na pasta do script, ou GitHub Secrets):
    RTE_USER, RTE_PASS          credenciais enviadas pela RTE
    RTE_AUTH_TYPE               padrão "dev" (confirmar com a RTE o valor de produção)
    RTE_COD_CLIENTE             código de cliente fornecido pela RTE (padrão 4204, da Luuna)
    RTE_TIPO                    "1" ou "6" (confirmar com a RTE)
    RTE_FRETE                   "1" = CIF (padrão), "2" = FOB
"""

import io
import os
import sys
import time
import argparse
import datetime as dt
import xml.etree.ElementTree as ET

import requests
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.graphics.barcode import code128

# --------------------------------------------------------------------------- .env
def _carregar_env():
    """Lê um arquivo .env (CHAVE=valor) na pasta do script, se existir.
    Variáveis já definidas no ambiente (ex.: GitHub Secrets) têm prioridade."""
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(caminho):
        return
    with open(caminho, encoding="utf-8-sig") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_env()

# --------------------------------------------------------------------------- config
URL_TOKEN = "https://unittocity-apigateway.rte.com.br/token"
URL_MALHA = ("https://unittocity-apigateway.rte.com.br/api/v1/unittocity/"
             "getWithHallsByDestinationAddressAndCustomers")

LARGURA_MM = 100          # tamanho da etiqueta da Platinum (confirmar)
ALTURA_MM = 150
TRANSPORTADORA = "RODONAVES TRANSPORTES E ENC"

# Retorno de exemplo que está no PDF da RTE (seção 9) — usado no modo --mock
MOCK_MALHA = {
    "Unit": {"Code": "721", "Description": "URUGUAIANA", "Abbreviation": "URU"},
    "City": {"UnitFederation": "RS", "Description": "Uruguaiana",
             "ZipCode": "97500511", "IbgeCityCode": 4322400},
    "OperationalSector": {"Code": "3012", "Abbreviation": "URU"},
    "UnitToUnitHalls": [
        {"Abbreviation": "_", "UnitCode": "091", "Order": 1},
        {"Abbreviation": "_", "UnitCode": "909", "Order": 2},
        {"Abbreviation": "_", "UnitCode": "901", "Order": 3},
        {"Abbreviation": "_", "UnitCode": "911", "Order": 4},
    ],
}

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}


# --------------------------------------------------------------------------- NF-e
def _t(node, path, default=""):
    el = node.find(path, NS) if node is not None else None
    return el.text.strip() if el is not None and el.text else default


def ler_nfe(xml_bytes):
    """Extrai do XML da NF-e só o que a etiqueta e a API precisam."""
    root = ET.fromstring(xml_bytes)
    inf = root.find(".//n:infNFe", NS)
    if inf is None:
        raise ValueError("XML não parece ser uma NF-e (infNFe não encontrado)")

    ide, emit, dest = inf.find("n:ide", NS), inf.find("n:emit", NS), inf.find("n:dest", NS)
    end = dest.find("n:enderDest", NS)

    volumes = sum(int(_t(v, "n:qVol", "0") or 0) for v in inf.findall("n:transp/n:vol", NS))

    data_saida = _t(ide, "n:dhSaiEnt") or _t(ide, "n:dhEmi")
    try:
        data_saida = dt.datetime.fromisoformat(data_saida[:19]).strftime("%d/%m/%Y")
    except ValueError:
        data_saida = dt.date.today().strftime("%d/%m/%Y")

    return {
        "nnf": _t(ide, "n:nNF"),
        "serie": _t(ide, "n:serie"),
        "emit_doc": _t(emit, "n:CNPJ") or _t(emit, "n:CPF"),
        "emit_nome": _t(emit, "n:xNome"),
        "dest_doc": _t(dest, "n:CNPJ") or _t(dest, "n:CPF"),
        "dest_nome": _t(dest, "n:xNome"),
        "logradouro": _t(end, "n:xLgr"),
        "numero": _t(end, "n:nro"),
        "complemento": _t(end, "n:xCpl"),
        "bairro": _t(end, "n:xBairro"),
        "cidade": _t(end, "n:xMun"),
        "uf": _t(end, "n:UF"),
        "cep": _t(end, "n:CEP"),
        "volumes": volumes or 1,
        "data_saida": data_saida,
    }


# --------------------------------------------------------------------------- API RTE
_token_cache = {"valor": None, "expira": 0}


def obter_token():
    """Token vale 8 h (expires_in 28800); reaproveita enquanto não vencer."""
    if _token_cache["valor"] and time.time() < _token_cache["expira"]:
        return _token_cache["valor"]
    r = requests.post(URL_TOKEN, timeout=30, data={
        "auth_type": os.getenv("RTE_AUTH_TYPE", "dev"),
        "grant_type": "password",
        "username": os.environ["RTE_USER"],
        "password": os.environ["RTE_PASS"],
    })
    r.raise_for_status()
    j = r.json()
    _token_cache["valor"] = j["access_token"]
    _token_cache["expira"] = time.time() + int(j.get("expires_in", 28800)) - 300
    return _token_cache["valor"]


def consultar_malha(nf):
    params = {
        "ReceiverCustomerTaxIdRegistration": nf["dest_doc"],
        "SenderCustomerTaxIdRegistration": nf["emit_doc"],
        "ZipCode": nf["cep"],
        "District": nf["bairro"],
        "AddressNumber": nf["numero"],
        "CityDescription": nf["cidade"],
        "UnitFederation": nf["uf"],
    }
    # Regras da documentação (dev.rodonaves.com.br): CPF/CNPJ com 11 a 14 dígitos,
    # CEP com 8, bairro e cidade com pelo menos 3 caracteres, UF com 2.
    if len(params["ZipCode"]) != 8:
        raise ValueError(f"CEP do destinatário inválido para a API RTE: {params['ZipCode']!r}")
    if len(params["District"]) < 3:
        raise ValueError(f"Bairro com menos de 3 caracteres, a API RTE recusa: {params['District']!r}")
    headers = {"Authorization": f"Bearer {obter_token()}", "accept": "application/json"}
    r = requests.get(URL_MALHA, params=params, headers=headers, timeout=30)   # GET com query string
    if r.status_code in (401, 403):
        raise PermissionError(f"API RTE recusou o acesso ({r.status_code}): {r.text[:200]}")
    r.raise_for_status()
    return r.json()


def extrair_dados_rte(malha):
    unit = (malha.get("Unit") or {}).get("Code") or ""
    if not unit:
        raise ValueError("API RTE não devolveu a unidade de destino (Unit.Code)")
    setor = malha.get("OperationalSector") or {}
    halls = sorted(malha.get("UnitToUnitHalls") or [], key=lambda h: h.get("Order", 0))
    por_ordem = {h.get("Order"): (h.get("UnitCode") or "") for h in halls}
    return {
        "unidade_destino": str(unit),
        "setor_codigo": str(setor.get("Code") or ""),        # código de barras
        "setor_sigla": str(setor.get("Abbreviation") or ""),  # visual (vazio fica vazio)
        "origem": por_ordem.get(1, ""),
        "ctc": [por_ordem.get(o, "") for o in (2, 3, 4)],
    }


# --------------------------------------------------------------------------- código de barras
def montar_codigo(nf, rte, volume):
    cod = (
        os.getenv("RTE_TIPO", "1")
        + os.getenv("RTE_COD_CLIENTE", "4204").zfill(4)
        + nf["serie"].zfill(2)
        + nf["nnf"].zfill(9)
        + str(volume).zfill(4)
        + rte["setor_codigo"].zfill(5)      # vazio -> 00000
        + rte["unidade_destino"].zfill(4)   # "721" -> "0721"
        + os.getenv("RTE_FRETE", "1")
    )
    if len(cod) != 30 or not cod.isdigit():
        raise ValueError(f"Código RTE inválido ({len(cod)} caracteres): {cod}")
    return cod


# --------------------------------------------------------------------------- PDF
def _tag(c, x, y, texto, tam=6.5):
    """Rótulo branco em fundo preto, como no modelo da RTE."""
    w = stringWidth(texto, "Helvetica-Bold", tam) + 2.4 * mm
    c.setFillColorRGB(0, 0, 0)
    c.rect(x, y, w, tam + 1.6 * mm, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", tam)
    c.drawString(x + 1.2 * mm, y + 0.85 * mm + 0.5, texto)
    c.setFillColorRGB(0, 0, 0)
    return w


def _texto_ajustado(c, x, y, texto, largura, fonte="Helvetica", tam=9, minimo=6):
    while tam > minimo and stringWidth(texto, fonte, tam) > largura:
        tam -= 0.5
    c.setFont(fonte, tam)
    c.drawString(x, y, texto)


def _pagina(c, nf, rte, volume, amostra):
    W, H = LARGURA_MM * mm, ALTURA_MM * mm
    m = 4 * mm
    L, R = m, W - m
    c.setLineWidth(1.2)

    # ---------------- bloco superior: logo | transportadora/origem/CTC/setor | volume/destino
    topo, base_sup = H - m, H - m - 36 * mm
    c.rect(L, base_sup, R - L, topo - base_sup)
    x_logo, x_dir = L + 22 * mm, R - 26 * mm
    c.line(x_logo, base_sup, x_logo, topo)
    c.line(x_dir, base_sup, x_dir, topo)
    meio = base_sup + 17 * mm
    c.line(x_logo, meio, x_dir, meio)

    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString((L + x_logo) / 2, base_sup + 14 * mm, "RTE")

    _tag(c, x_logo + 1.5 * mm, topo - 6 * mm, "Transportadora")
    _texto_ajustado(c, x_logo + 1.5 * mm, topo - 12 * mm, TRANSPORTADORA,
                    x_dir - x_logo - 3 * mm, "Helvetica", 9)

    w_col = (x_dir - x_logo) / 3
    cols = [
        ("Origem", rte["origem"]),
        ("CTC", " | ".join(c for c in rte["ctc"] if c)),   # CTC vazio não aparece
        ("Setor", rte["setor_sigla"]),
    ]
    larguras = [w_col * 0.8, w_col * 1.5, w_col * 0.7]
    x = x_logo
    for i, (rot, val) in enumerate(cols):
        if i:
            c.line(x, base_sup, x, meio)
        _tag(c, x + 1.5 * mm, meio - 6 * mm, rot)
        _texto_ajustado(c, x + 1.5 * mm, base_sup + 3 * mm, val,
                        larguras[i] - 3 * mm, "Helvetica-Bold", 10)
        x += larguras[i]

    c.line(x_dir, meio, R, meio)
    _tag(c, x_dir + 1.5 * mm, topo - 6 * mm, "Volume")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x_dir + 1.5 * mm, topo - 13 * mm,
                 f"{str(volume).zfill(4)}/{str(nf['volumes']).zfill(4)}")
    _tag(c, x_dir + 1.5 * mm, meio - 6 * mm, "Destino")
    c.setFont("Helvetica-Bold", 24)
    c.drawCentredString((x_dir + R) / 2, base_sup + 3 * mm, rte["unidade_destino"])

    # ---------------- remetente
    y = base_sup - 9 * mm
    c.rect(L, y, R - L, 9 * mm)
    w = _tag(c, L + 1.5 * mm, y + 2 * mm, "Remetente")
    _texto_ajustado(c, L + 3 * mm + w, y + 2.8 * mm, nf["emit_nome"].upper(),
                    R - L - w - 5 * mm, "Helvetica", 9)

    # ---------------- destinatário
    alt_dest = 30 * mm
    y -= alt_dest
    c.rect(L, y, R - L, alt_dest)
    w = _tag(c, L + 1.5 * mm, y + alt_dest - 6.5 * mm, "Destinatário")
    _texto_ajustado(c, L + 3 * mm + w, y + alt_dest - 5.7 * mm, nf["dest_nome"].upper(),
                    R - L - w - 5 * mm, "Helvetica-Bold", 9)
    cep = nf["cep"].zfill(8)
    endereco = (f"{cep[:2]}.{cep[2:5]}-{cep[5:]} - {nf['logradouro']}, Nr. {nf['numero']}"
                + (f" {nf['complemento']}" if nf["complemento"] else "")
                + f" - {nf['bairro']} - {nf['cidade']} / {nf['uf']}").upper()
    c.setFont("Helvetica", 8.5)
    linhas = simpleSplit(endereco, "Helvetica", 8.5, R - L - 4 * mm)[:3]
    for i, ln in enumerate(linhas):
        c.drawString(L + 2 * mm, y + alt_dest - 12 * mm - i * 4 * mm, ln)

    # ---------------- código de barras (Code 128, subset C automático para 30 dígitos)
    codigo = montar_codigo(nf, rte, volume)
    alt_cb = 48 * mm
    y -= alt_cb
    c.rect(L, y, R - L, alt_cb)
    bar = code128.Code128(codigo, barWidth=0.375 * mm,   # 3 pontos numa térmica 203 dpi
                          barHeight=32 * mm, quiet=1, lquiet=4 * mm, rquiet=4 * mm)
    bar.drawOn(c, (W - bar.width) / 2, y + 11 * mm)
    c.setFont("Helvetica", 9)
    c.drawCentredString(W / 2, y + 4.5 * mm, codigo)

    # ---------------- rodapé: NF e data
    alt_rod = 14 * mm
    y -= alt_rod
    c.rect(L, y, R - L, alt_rod)
    for x0, txt in ((L + 3 * mm, f"NF: {nf['nnf'].zfill(9)}"),
                    (W / 2 + 2 * mm, f"Data: {nf['data_saida']}")):
        c.setFont("Helvetica-Bold", 12)
        tw = stringWidth(txt, "Helvetica-Bold", 12) + 4 * mm
        c.rect(x0, y + 3 * mm, tw, 7 * mm, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.drawString(x0 + 2 * mm, y + 5 * mm, txt)
        c.setFillColorRGB(0, 0, 0)

    if amostra:
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(W / 2, y - 3.5 * mm,
                            "AMOSTRA - DADOS DE EXEMPLO DA RTE - NÃO USAR EM CARGA")


def gerar_etiqueta_pdf(xml_bytes, mock=False):
    """Recebe o XML da NF-e e devolve os bytes do PDF (uma página por volume)."""
    nf = ler_nfe(xml_bytes)
    rte = extrair_dados_rte(MOCK_MALHA if mock else consultar_malha(nf))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(LARGURA_MM * mm, ALTURA_MM * mm))
    c.setTitle(f"Etiqueta RTE NF {nf['nnf']}")
    for v in range(1, nf["volumes"] + 1):
        _pagina(c, nf, rte, v, amostra=mock)
        c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------- CLI
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Etiqueta única RTE Rodonaves em PDF")
    ap.add_argument("xml", help="XML da NF-e")
    ap.add_argument("-o", "--saida", help="PDF de saída (padrão: RTE_<NF>.pdf)")
    ap.add_argument("--mock", action="store_true",
                    help="usa o retorno de exemplo do PDF da RTE em vez de chamar a API")
    ap.add_argument("--ver-api", action="store_true",
                    help="só testa o acesso: gera o token, consulta a malha e mostra o retorno, sem gerar PDF")
    a = ap.parse_args()

    if a.ver_api:
        import json
        nf = ler_nfe(open(a.xml, "rb").read())
        try:
            obter_token()
            print("Token: OK")
        except requests.HTTPError as e:
            print(f"Token: ERRO {e.response.status_code} - {e.response.text[:300]}")
            sys.exit(1)
        except KeyError as e:
            print(f"Variável de ambiente faltando: {e}")
            sys.exit(1)
        malha = consultar_malha(nf)
        print(json.dumps(malha, indent=2, ensure_ascii=False))
        rte = extrair_dados_rte(malha)
        print(f"\nNa etiqueta: origem {rte['origem']} | CTC {' | '.join(c for c in rte['ctc'] if c) or '(vazio)'} | "
              f"setor {rte['setor_sigla'] or '(vazio)'} | destino {rte['unidade_destino']}")
        print(f"Código de barras do volume 1: {montar_codigo(nf, rte, 1)}")
        sys.exit(0)

    with open(a.xml, "rb") as f:
        xml = f.read()
    pdf = gerar_etiqueta_pdf(xml, mock=a.mock)
    saida = a.saida or f"RTE_{ler_nfe(xml)['nnf']}.pdf"
    with open(saida, "wb") as f:
        f.write(pdf)
    print(f"OK: {saida}")
    sys.exit(0)
