"""
Gera o arquivo EDI NOTFIS no layout do Grupo Rodonaves (PROCEDA 3.1 com a chave
de acesso da NF-e no registro 313), a partir dos XMLs das NF-e.

Uso:
    python notfis.py nota1.xml nota2.xml ...            -> NOTFIS_LUUNA_<data>_<hora>.txt
    python notfis.py *.xml -o NOTFIS_LUUNA.txt

Registros gerados: 000, 310, 311, 312, 313 + 317 + 333 (por NF), 318.
Linhas de 300 posições completadas com espaços e CRLF, como no exemplo enviado pela RTE.
"""

import sys
import argparse
import unicodedata
import datetime as dt
import xml.etree.ElementTree as ET
from decimal import Decimal, ROUND_HALF_UP
from collections import OrderedDict

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}
TAM_LINHA = 300   # tamanho de cada linha no arquivo (exemplo da RTE)

# --- valores fixos (confirmar com a RTE) -------------------------------------
CAIXA_REMETENTE = "LUUNA"          # 000 campo 2
CAIXA_DESTINATARIO = "RTE RODONAVES"  # 000 campo 3
NATUREZA = "COLCHOES"              # 313 campo 11 (tipo da mercadoria)
ESPECIE_PADRAO = "VOLUMES"         # 313 campo 12, se o XML não trouxer <esp>
CONDICAO_FRETE = "C"               # C = CIF, F = FOB
TIPO_ICMS = "S"                    # S = alíquota normal
MEIO_TRANSPORTE = "1"              # 1 = rodoviário
TIPO_TRANSPORTE = "2"              # 2 = carga fracionada
TIPO_CARGA = "2"                   # 2 = seca


# --- formatação PROCEDA --------------------------------------------------------
def _ascii(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    return "".join(c if 32 <= ord(c) < 127 else " " for c in s)   # só ASCII no arquivo


def A(valor, n):
    """Alfanumérico: maiúsculo, sem acento, alinhado à esquerda com espaços."""
    return _ascii(valor).replace("\r", " ").replace("\n", " ")[:n].ljust(n)


def N(valor, inteiros, decimais=0):
    """Numérico: zeros à esquerda, decimais implícitos (sem ponto/vírgula)."""
    if valor in (None, ""):
        return "0" * (inteiros + decimais)
    q = (Decimal(str(valor)) * (10 ** decimais)).quantize(Decimal(1), ROUND_HALF_UP)
    s = str(abs(int(q)))
    if len(s) > inteiros + decimais:
        raise ValueError(f"valor {valor} não cabe em N {inteiros},{decimais}")
    return s.zfill(inteiros + decimais)


def digitos(s):
    return "".join(c for c in str(s or "") if c.isdigit())


def linha(*campos, tamanho=240):
    r = "".join(campos)
    if len(r) != tamanho:
        raise ValueError(f"registro {r[:3]} com {len(r)} posições (esperado {tamanho})")
    return r.ljust(TAM_LINHA)


def cep(s):
    d = digitos(s).zfill(8)
    return f"{d[:5]}-{d[5:]}"


# --- leitura do XML ------------------------------------------------------------
def _t(node, path, default=""):
    el = node.find(path, NS) if node is not None else None
    return el.text.strip() if el is not None and el.text else default


def _data(iso):
    return dt.date.fromisoformat(iso[:10]) if iso else dt.date.today()


def ler_nfe(xml_bytes):
    inf = ET.fromstring(xml_bytes).find(".//n:infNFe", NS)
    if inf is None:
        raise ValueError("XML não é uma NF-e (infNFe não encontrado)")
    ide, emit, dest = (inf.find(f"n:{t}", NS) for t in ("ide", "emit", "dest"))
    ee, ed = emit.find("n:enderEmit", NS), dest.find("n:enderDest", NS)
    tot = inf.find("n:total/n:ICMSTot", NS)
    vols = inf.findall("n:transp/n:vol", NS)

    def endereco(e):
        partes = [_t(e, "n:xLgr"), _t(e, "n:nro"), _t(e, "n:xCpl")]
        return ", ".join(p for p in partes if p)

    doc_dest = _t(dest, "n:CNPJ")
    return {
        "chave": (inf.get("Id") or "")[3:],
        "emit": {
            "cnpj": digitos(_t(emit, "n:CNPJ")),
            "ie": _t(emit, "n:IE"),
            "nome": _t(emit, "n:xNome"),
            "endereco": endereco(ee),
            "bairro": _t(ee, "n:xBairro"),
            "cidade": _t(ee, "n:xMun"),
            "cod_mun": _t(ee, "n:cMun"),
            "cep": cep(_t(ee, "n:CEP")),
            "uf": _t(ee, "n:UF"),
            "fone": _t(ee, "n:fone"),
        },
        "dest": {
            "doc": digitos(doc_dest or _t(dest, "n:CPF")),
            "tipo_doc": "1" if doc_dest else "2",
            "ie": _t(dest, "n:IE"),
            "nome": _t(dest, "n:xNome"),
            "endereco": endereco(ed),
            "bairro": _t(ed, "n:xBairro"),
            "cidade": _t(ed, "n:xMun"),
            "cod_mun": _t(ed, "n:cMun"),
            "cep": cep(_t(ed, "n:CEP")),
            "uf": _t(ed, "n:UF"),
            "fone": _t(ed, "n:fone"),
        },
        "cfop": _t(inf, "n:det/n:prod/n:CFOP"),
        "serie": _t(ide, "n:serie"),
        "numero": _t(ide, "n:nNF"),
        "emissao": _data(_t(ide, "n:dhEmi")),
        "saida": _data(_t(ide, "n:dhSaiEnt") or _t(ide, "n:dhEmi")),
        "volumes": sum(Decimal(_t(v, "n:qVol", "0") or 0) for v in vols) or Decimal(1),
        "especie": next((_t(v, "n:esp") for v in vols if _t(v, "n:esp")), ESPECIE_PADRAO),
        "peso": sum(Decimal(_t(v, "n:pesoB", "0") or 0) for v in vols),
        "valor": Decimal(_t(tot, "n:vNF", "0")),
        "icms": Decimal(_t(tot, "n:vICMS", "0")),
        "icms_st": Decimal(_t(tot, "n:vST", "0")),
    }


# --- registros -----------------------------------------------------------------
def r000(agora, interc):
    return linha("000", A(CAIXA_REMETENTE, 35), A(CAIXA_DESTINATARIO, 35),
                 agora.strftime("%d%m%y"), agora.strftime("%H%M"), A(interc, 12), A("", 145))


def r310(doc_id):
    return linha("310", A(doc_id, 14), A("", 223))


def r311(e, embarque):
    return linha("311", N(e["cnpj"], 14), A(e["ie"], 15), A(e["endereco"], 40), A(e["cidade"], 35),
                 A(e["cep"], 9), A(e["uf"], 9), embarque.strftime("%d%m%Y"), A(e["nome"], 40), A("", 67))


def r312(d):
    return linha("312", A(d["nome"], 40), N(d["doc"], 14), A(d["ie"], 15), A(d["endereco"], 40),
                 A(d["bairro"], 20), A(d["cidade"], 35), A(d["cep"], 9), A(d["cod_mun"], 9),
                 A(d["uf"], 9), A("", 4), A(d["fone"], 35), A(d["tipo_doc"], 1), A("", 6))


def r313(nf):
    return linha(
        "313",
        A("", 15),                      # 2  romaneio/coleta
        A("", 7),                       # 3  código da rota
        MEIO_TRANSPORTE, TIPO_TRANSPORTE, TIPO_CARGA,   # 4-6
        CONDICAO_FRETE,                 # 7
        A(nf["serie"], 3),              # 8  série
        N(nf["numero"], 8),             # 9  número da NF
        nf["emissao"].strftime("%d%m%Y"),  # 10
        A(NATUREZA, 15),                # 11
        A(nf["especie"], 15),           # 12
        N(nf["volumes"], 5, 2),         # 13
        N(nf["valor"], 13, 2),          # 14
        N(nf["peso"], 5, 2),            # 15
        N(0, 3, 2),                     # 16 cubagem
        TIPO_ICMS,                      # 17
        "N",                            # 18 seguro já efetuado
        N(0, 13, 2), N(0, 13, 2),       # 19-20 seguro, valor a cobrar
        A("", 7), A("", 1),             # 21-22 placa, carga rápida
        N(0, 13, 2), N(0, 13, 2), N(0, 13, 2), N(0, 13, 2),  # 23-26 valores de frete
        "I",                            # 27 ação: inclusão
        N(nf["icms"], 10, 2),           # 28
        N(nf["icms_st"], 10, 2),        # 29
        "N",                            # 30 bonificação
        A(nf["chave"], 44),             # 31 chave de acesso da NF-e (layout RTE)
        tamanho=282,
    )


def r317(e):
    """Responsável pelo frete: a própria Luuna (CIF), como no exemplo da RTE."""
    return linha("317", A(e["nome"], 40), N(e["cnpj"], 14), A(e["ie"], 15), A(e["endereco"], 40),
                 A(e["bairro"], 20), A(e["cidade"], 35), A(e["cep"], 9), A(e["cod_mun"], 9),
                 A(e["uf"], 9), A(e["fone"], 35), A("", 11))


def r333(nf):
    return linha(
        "333",
        N(digitos(nf["cfop"]), 4),      # 2  CFOP do primeiro item
        "0",                            # 3  sem data de entrega definida
        N(0, 8), N(0, 4), N(0, 8), N(0, 4),   # 4-7 datas e horas de entrega
        A("", 15),                      # 8  local de desembarque
        "N",                            # 9  cálculo de frete diferenciado
        A("", 10),                      # 10 tabela de preço
        *[N(0, 15) + A("", 3) + N(0, 8) for _ in range(5)],   # 11-15 entrega casada
        N(0, 13, 2),                    # 16 despesas adicionais
        A("", 5), A("", 10), A("", 5), A("", 12),   # 17-20 veículo e redespacho
        A("", 5),                       # 21 filler
    )


def r318(nfs):
    return linha("318", N(sum(n["valor"] for n in nfs), 13, 2), N(sum(n["peso"] for n in nfs), 13, 2),
                 N(0, 13, 2), N(sum(n["volumes"] for n in nfs), 13, 2), N(0, 13, 2), N(0, 13, 2), A("", 147))


def gerar_notfis(xmls, agora=None):
    """Recebe uma lista de XMLs (bytes) e devolve o conteúdo do NOTFIS (str, linhas CRLF)."""
    agora = agora or dt.datetime.now()
    nfs = [ler_nfe(x) for x in xmls]
    if not nfs:
        raise ValueError("nenhuma NF informada")
    if len({n["emit"]["cnpj"] for n in nfs}) > 1:
        raise ValueError("NFs de emitentes diferentes: gere um arquivo por CNPJ emitente")
    if len(nfs) > 40 * 500:
        raise ValueError("NFs demais para um único documento 310")

    marca = agora.strftime("%d%m%H%M") + "0"
    out = [r000(agora, "NOT" + marca), r310("NOTFI" + marca),
           r311(nfs[0]["emit"], max(n["saida"] for n in nfs))]
    por_dest = OrderedDict()
    for n in nfs:
        por_dest.setdefault(n["dest"]["doc"], []).append(n)
    for grupo in por_dest.values():
        for i in range(0, len(grupo), 40):           # até 40 notas por 312
            out.append(r312(grupo[0]["dest"]))
            for n in grupo[i:i + 40]:
                out.extend([r313(n), r317(n["emit"]), r333(n)])
    out.append(r318(nfs))
    return "\r\n".join(out) + "\r\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Gera NOTFIS 3.1 (PROCEDA) a partir de XMLs de NF-e")
    ap.add_argument("xmls", nargs="+")
    ap.add_argument("-o", "--saida")
    a = ap.parse_args()
    import glob  # no Windows o terminal não expande *.xml; o próprio script expande
    arquivos = sorted({f for padrao in a.xmls for f in (glob.glob(padrao) or [padrao])})
    agora = dt.datetime.now()
    conteudo = gerar_notfis([open(p, "rb").read() for p in arquivos], agora)
    saida = a.saida or f"NOTFIS_LUUNA_{agora:%Y%m%d_%H%M}.txt"
    with open(saida, "w", encoding="ascii", newline="") as f:
        f.write(conteudo)
    print(f"OK: {saida} ({len(arquivos)} NF)")
    sys.exit(0)
