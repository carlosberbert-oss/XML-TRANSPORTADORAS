"""
Rodonaves (RTE) no bot XML Transportadoras.

1. Para cada XML: consulta a malha da RTE e gera o PDF da etiqueta única (rte_etiqueta.py)
2. Gera o NOTFIS só com as NFs que ficaram com etiqueta (notfis.py)
3. Envia o NOTFIS por e-mail para a RTE

As etiquetas sobem no Platinum pela mesma rotina da JAMEF (platinum_subir_etiquetas, no bot.py).

Variáveis (GitHub Secrets / .env):
    RTE_USER, RTE_PASS        acesso à API da RTE (obrigatórias)
    RTE_AUTH_TYPE             padrão "dev"
    RTE_ENVIAR_NOTFIS         "false" desliga o envio do NOTFIS (padrão: envia)
    RTE_EMAIL_NOTFIS          destino do NOTFIS (padrão edi.emissao@rte.com.br)
"""

import os
import smtplib
from pathlib import Path
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

import rte_etiqueta as rte
import notfis

COPIA_INTERNA = ["felipe.azevedo@zeb.mx", "israel.lopes@zeb.mx"]


def _envio_notfis_ligado() -> bool:
    return os.getenv("RTE_ENVIAR_NOTFIS", "true").strip().lower() != "false"


def rodonaves_processar_xmls(arquivos: list[Path], pasta: Path) -> dict:
    """Gera as etiquetas e o NOTFIS das NFs baixadas. Devolve o resultado para o Chat e o histórico."""
    vazio = {"ok": [], "falha": [], "etiquetas_pdfs": [], "notfis": None, "notfis_nfs": 0}

    if not (os.getenv("RTE_USER") and os.getenv("RTE_PASS")):
        print("   ⚠️  RTE_USER / RTE_PASS não configurados — pulando Rodonaves.")
        return {**vazio, "pulado": True}

    xmls = [f for f in arquivos if f.suffix.lower() == ".xml"]
    if not xmls:
        print("   ⚠️  Nenhum XML para gerar etiqueta RTE.")
        return vazio

    try:
        rte.obter_token()
        print("\n🔐  Token da API RTE OK!")
    except Exception as e:
        print(f"   ❌  Token da API RTE falhou: {e}")
        return {**vazio, "falha": [f.name for f in xmls], "erro_login": True}

    pasta_etiquetas = pasta / "etiquetas_rte"
    pasta_etiquetas.mkdir(parents=True, exist_ok=True)

    print(f"\n🏷️  Gerando {len(xmls)} etiqueta(s) RTE...")
    ok, falha, etiquetas_pdfs, xmls_prontos = [], [], [], []
    for xml in xmls:
        dados = xml.read_bytes()
        try:
            nf = rte.ler_nfe(dados)
        except Exception as e:
            print(f"   ❌  {xml.name}: XML inválido ({e})")
            falha.append(f"{xml.name} — XML inválido")
            continue

        n_nf = nf["nnf"].lstrip("0") or nf["nnf"]
        try:
            pdf = rte.gerar_etiqueta_pdf(dados)          # consulta a malha e desenha a etiqueta
        except requests.HTTPError as e:
            motivo = f"API RTE respondeu {e.response.status_code}"
            print(f"   ❌  NF {n_nf}: {motivo} — {e.response.text[:200]}")
            falha.append(f"NF {n_nf} — {motivo}")
            continue
        except Exception as e:
            print(f"   ❌  NF {n_nf}: {e}")
            falha.append(f"NF {n_nf} — {str(e)[:80]}")
            continue

        caminho = pasta_etiquetas / f"RTE_{n_nf}.pdf"
        caminho.write_bytes(pdf)
        print(f"   🏷️  Etiqueta NF {n_nf}: {nf['volumes']} volume(s) → {caminho}")
        ok.append(f"NF {n_nf} ({nf['volumes']} vol.)")
        etiquetas_pdfs.append((n_nf, caminho))
        xmls_prontos.append(dados)

    # NOTFIS só com as notas que têm etiqueta (nota sem etiqueta não sai)
    notfis_path = None
    if xmls_prontos:
        agora = datetime.now()
        try:
            conteudo = notfis.gerar_notfis(xmls_prontos, agora)
            notfis_path = pasta / f"NOTFIS_LUUNA_{agora:%Y%m%d_%H%M}.txt"
            with open(notfis_path, "w", encoding="ascii", newline="") as f:
                f.write(conteudo)
            print(f"\n📄  NOTFIS gerado: {notfis_path.name} ({len(xmls_prontos)} NF)")
        except Exception as e:
            print(f"   ❌  Erro ao gerar o NOTFIS: {e}")

    print(f"\n   📊  RTE: {len(ok)} etiqueta(s) OK, {len(falha)} falha(s)")
    return {"ok": ok, "falha": falha, "etiquetas_pdfs": etiquetas_pdfs,
            "notfis": notfis_path, "notfis_nfs": len(xmls_prontos) if notfis_path else 0}


def enviar_notfis_por_email(notfis_path: Path | None, n_nfs: int) -> bool | None:
    """Envia o NOTFIS para a RTE com cópia interna. Devolve None quando não havia o que enviar."""
    if not notfis_path:
        return None
    if not _envio_notfis_ligado():
        print("   ℹ️  RTE_ENVIAR_NOTFIS=false — NOTFIS gerado, mas não enviado.")
        return None

    usuario = os.getenv("GMAIL_USUARIO", "carlos.berbert@zeb.mx")
    senha = os.getenv("GMAIL_APP_PASSWORD", "")
    if not senha:
        print("   ⚠️  GMAIL_APP_PASSWORD não configurado — NOTFIS não enviado.")
        return False

    para = [os.getenv("RTE_EMAIL_NOTFIS", "edi.emissao@rte.com.br")]
    cc = [usuario] + [e for e in COPIA_INTERNA if e != usuario]
    data_hoje = datetime.now().strftime("%d/%m/%Y")
    assunto = f"NOTFIS LUUNA {data_hoje} - RODONAVES"

    corpo = (
        '<html><body style="font-family: Arial, sans-serif; color: #1a1a1a;">'
        f"<p>Olá,</p><p>Segue o arquivo NOTFIS da coleta de hoje ({data_hoje}), "
        f"com {n_nfs} nota(s) fiscal(is).</p>"
        f"<p>Arquivo: <strong>{notfis_path.name}</strong></p>"
        '<p style="color:#6b7280;font-size:12px;">Enviado automaticamente pelo Bot XML Transportadoras — Zebrands/Luuna</p>'
        "</body></html>"
    )

    try:
        print(f"\n📧  Enviando NOTFIS — Para: {', '.join(para)} | CC: {', '.join(cc)}")
        msg = MIMEMultipart()
        msg["From"], msg["To"], msg["Cc"], msg["Subject"] = usuario, ", ".join(para), ", ".join(cc), assunto
        msg.attach(MIMEText(corpo, "html"))
        parte = MIMEBase("text", "plain")
        parte.set_payload(notfis_path.read_bytes())
        encoders.encode_base64(parte)
        parte.add_header("Content-Disposition", f"attachment; filename={notfis_path.name}")
        msg.attach(parte)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as servidor:
            servidor.login(usuario, senha)
            servidor.sendmail(usuario, para + cc, msg.as_string())
        print("   ✅  NOTFIS enviado!")
        return True
    except Exception as e:
        print(f"   ❌  Erro ao enviar o NOTFIS: {e}")
        return False


def resumo_chat(res: dict) -> str:
    """Trecho da mensagem do Google Chat com o resultado da Rodonaves."""
    if res.get("pulado"):
        return ("\n⚠️ *API RTE:* credenciais não configuradas — etiquetas e NOTFIS NÃO gerados"
                " (os pedidos serão tentados de novo na próxima execução)")
    if res.get("erro_login"):
        return "\n❌ *API RTE:* token recusado — ver log do Actions (pedidos ficam para a próxima execução)"

    ok, falha = res.get("ok", []), res.get("falha", [])
    txt = f"\n🏷️ *Etiquetas RTE:* {len(ok)} OK, {len(falha)} falha(s)"
    if ok:
        txt += "\n   ✅ " + " | ".join(ok[:10])
    if falha:
        txt += "\n   ⚠️ " + " | ".join(falha[:10])
        txt += "\n   👉 Gere as que falharam pelo painel de etiquetas Rodonaves e envie o NOTFIS delas."

    email = res.get("notfis_email")
    if res.get("notfis"):
        if email is True:
            txt += f"\n📄 *NOTFIS:* {res.get('notfis_nfs', 0)} NF(s) enviada(s) para a RTE"
        elif email is None:
            txt += "\n📄 *NOTFIS:* gerado, envio desligado (RTE_ENVIAR_NOTFIS=false)"
        else:
            txt += "\n❌ *NOTFIS:* falha no envio por e-mail — ver log (pedidos ficam para a próxima execução)"

    plat_ok, plat_falha = res.get("platinum_ok", []), res.get("platinum_falha", [])
    if res.get("platinum_pulado"):
        txt += "\n📦 *Platinum:* não executado (login/credenciais — ver log)"
    elif plat_ok or plat_falha:
        txt += f"\n📦 *Platinum:* {len(plat_ok)} etiqueta(s) OK, {len(plat_falha)} falha(s)"
        if plat_falha:
            txt += "\n   ⚠️ " + " | ".join(plat_falha[:10])
    return txt


def rodonaves_nao_rodou(res: dict | None) -> bool:
    """True quando a rodada da RTE não se completou e os pedidos devem ser tentados de novo."""
    if not res:
        return False
    if any(res.get(k) for k in ("pulado", "erro_login", "platinum_pulado")):
        return True
    if res.get("etiquetas_pdfs") and not res.get("notfis"):     # etiquetas prontas, mas o NOTFIS não foi gerado
        return True
    return bool(res.get("notfis")) and res.get("notfis_email") is False
