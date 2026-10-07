"""
jamef_etiqueta.py - JAMEF pela API do Portal Developers (sem portal, sem MFA)

Teste rapido:
    python jamef_etiqueta.py <chave44 ou nota.xml> [...]     busca a etiqueta (NF ja cadastrada)
    python jamef_etiqueta.py --enviar nota.xml [...]         cadastra a NF e depois busca a etiqueta
    python jamef_etiqueta.py --dados <chave44 ou nota.xml>   mostra o JSON "DADOS" da etiqueta

Variaveis (.env ou GitHub Secrets): JAMEF_API_USUARIO, JAMEF_API_SENHA
Mesmo login da API de rastreamento (auth/v1/login), NAO o do cliente.jamef.com.br.
"""
import os
import re
import sys
import json
import time
import base64
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_BASE = os.getenv("JAMEF_API_BASE", "https://api.jamef.com.br")
ETIQUETA_PATH = os.getenv("JAMEF_ETIQUETA_PATH", "/operacao/v1/etiqueta")
NF_PATH = os.getenv("JAMEF_NF_PATH", "/documentos/v1/nota-fiscal")
FILIAL_JAMEF = os.getenv("JAMEF_FILIAL", "57")  # mesma filialOrigem que o portal usava
PASTA_SAIDA = Path(os.getenv("JAMEF_ETIQUETAS_DIR", "etiquetas_jamef"))
DPMM = 8  # 203 dpi, padrao das Zebra
NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}


def _checa_bloqueio(r):
    if r.status_code == 403 and "Access Denied" in r.text:
        raise RuntimeError("JAMEF bloqueou a requisicao (403 Access Denied / WAF)")


def _json(r):
    try:
        return r.json()
    except ValueError:
        return None


class JamefAPI:
    def __init__(self, timeout=30):
        self.usuario = os.environ["JAMEF_API_USUARIO"]
        self.senha = os.environ["JAMEF_API_SENHA"]
        self.timeout = timeout
        self.s = requests.Session()
        self.token = None

    def login(self):
        r = self.s.post(f"{API_BASE}/auth/v1/login",
                        json={"username": self.usuario, "password": self.senha},
                        timeout=self.timeout)
        _checa_bloqueio(r)
        r.raise_for_status()
        self.token = r.json()["dado"][0]["accessToken"]
        self.s.headers["Authorization"] = f"Bearer {self.token}"

    def _req(self, metodo, url, **kw):
        if not self.token:
            self.login()
        r = self.s.request(metodo, url, timeout=self.timeout, **kw)
        if r.status_code == 401:  # token expirado: reloga uma vez
            self.login()
            r = self.s.request(metodo, url, timeout=self.timeout, **kw)
        _checa_bloqueio(r)
        return r

    def enviar_nf(self, caminho_xml):
        """Cadastra a NF na JAMEF mandando o XML em base64. Retorna (ok, resposta)."""
        xml_b64 = base64.b64encode(Path(caminho_xml).read_bytes()).decode("ascii")
        r = self._req("POST", f"{API_BASE}{NF_PATH}",
                      json={"codigoFilialJamef": FILIAL_JAMEF, "xmlBase64": xml_b64})
        j = _json(r)
        if j is None:
            return False, f"HTTP {r.status_code}: {r.text[:300]}"
        return r.ok and str(j.get("situacao", r.status_code)).startswith("2"), j

    def etiqueta(self, chave, tipo="ZPL"):
        """tipo 'ZPL' ou 'DADOS'. Retorna (True, dado[0]) ou (False, mensagem de erro)."""
        r = self._req("GET", f"{API_BASE}{ETIQUETA_PATH}/{chave}", params={"tipoRetorno": tipo})
        j = _json(r)
        if j is None:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        if r.ok and str(j.get("situacao")) == "200" and j.get("dado"):
            return True, j["dado"][0]
        return False, f"HTTP {r.status_code}: {json.dumps(j, ensure_ascii=False)[:800]}"


def chave_do_xml(caminho):
    raiz = ET.parse(caminho).getroot()
    ch = raiz.find(".//nfe:infProt/nfe:chNFe", NS)
    if ch is not None and ch.text:
        return ch.text.strip()
    inf = raiz.find(".//nfe:infNFe", NS)
    if inf is not None and inf.get("Id"):
        return inf.get("Id").removeprefix("NFe")
    raise ValueError(f"chave nao encontrada em {caminho}")


def resolver_chave(item):
    """Aceita a chave de 44 digitos ou o caminho do XML da NF-e."""
    item = str(item).strip()
    return item if re.fullmatch(r"\d{44}", item) else chave_do_xml(item)


def _zpl(texto):
    """A API devolve ZPL em texto; se algum dia vier em base64, decodifica."""
    t = texto.strip()
    if t.startswith("^XA"):
        return t
    try:
        return base64.b64decode(t).decode("utf-8")
    except Exception:
        return t


def zpl_para_pdf(zpls, destino):
    """Converte ZPL em PDF pelo Labelary (um volume por pagina). Tamanho lido do ^PW/^LL."""
    zpl = "\n".join(zpls)
    pw, ll = re.search(r"\^PW(\d+)", zpl), re.search(r"\^LL(\d+)", zpl)
    larg = round(int(pw.group(1)) / (DPMM * 25.4), 2) if pw else 4
    alt = round(int(ll.group(1)) / (DPMM * 25.4), 2) if ll else 6
    r = requests.post(f"https://api.labelary.com/v1/printers/{DPMM}dpmm/labels/{larg}x{alt}/",
                      data=zpl.encode("utf-8"), headers={"Accept": "application/pdf"}, timeout=60)
    r.raise_for_status()
    Path(destino).write_bytes(r.content)


def gerar_etiquetas(itens, api=None, pasta=PASTA_SAIDA, tentativas=1, espera=15):
    """itens: chaves de 44 digitos ou caminhos de XML.
    tentativas > 1: repete a busca enquanto a NF recem-enviada ainda nao tem etiqueta.
    Retorna {chave: {"ok", "pdf", "volumes", "erro"}} - nunca para no meio por causa de uma NF."""
    api = api or JamefAPI()
    pasta.mkdir(parents=True, exist_ok=True)
    res = {}
    for item in itens:
        try:
            chave = resolver_chave(item)
        except (ValueError, ET.ParseError, OSError) as e:
            res[str(item)] = {"ok": False, "pdf": None, "volumes": 0, "erro": str(e)}
            continue
        for t in range(tentativas):
            ok, dado = api.etiqueta(chave, "ZPL")
            if ok or t == tentativas - 1:
                break
            print(f"...  {chave}: etiqueta ainda nao disponivel, nova tentativa em {espera}s")
            time.sleep(espera)
        zpls = [_zpl(z) for z in (dado.get("etiquetasZPL") or [])] if ok else []
        if not zpls:
            erro = dado if not ok else "API respondeu OK mas sem etiquetasZPL"
            res[chave] = {"ok": False, "pdf": None, "volumes": 0, "erro": erro}
            continue
        nf = chave[25:34].lstrip("0")
        (pasta / f"JAMEF_NF{nf}.zpl").write_text("\n".join(zpls), encoding="utf-8")
        pdf = pasta / f"JAMEF_NF{nf}.pdf"
        try:
            zpl_para_pdf(zpls, pdf)
            res[chave] = {"ok": True, "pdf": str(pdf), "volumes": len(zpls), "erro": None}
        except requests.RequestException as e:
            res[chave] = {"ok": False, "pdf": None, "volumes": len(zpls),
                          "erro": f"ZPL salvo, PDF falhou: {e}"}
        time.sleep(0.5)  # respeita o limite de requisicoes do Labelary
    return res


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    api = JamefAPI()
    api.login()
    print("OK  login na API JAMEF")

    if args[0] == "--dados":
        for item in args[1:]:
            ok, d = api.etiqueta(resolver_chave(item), "DADOS")
            print(json.dumps(d, ensure_ascii=False, indent=2) if ok else f"ERRO {item}: {d}")
        sys.exit(0)

    tentativas = 1
    if args[0] == "--enviar":
        args, tentativas = args[1:], 4
        for xml in args:
            ok, resp = api.enviar_nf(xml)
            texto = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)[:1500]
            print(f"{'OK  ' if ok else 'ERRO'} envio {xml}: {texto}")

    for chave, r in gerar_etiquetas(args, api, tentativas=tentativas).items():
        print(f"OK  {chave} -> {r['pdf']} ({r['volumes']} vol.)" if r["ok"]
              else f"ERRO {chave}: {r['erro']}")
