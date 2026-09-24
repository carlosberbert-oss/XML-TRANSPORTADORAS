"""
╔══════════════════════════════════════════════════════════╗
║         BOT XML TRANSPORTADORAS — Zebrands/Luuna        ║
║   Download XML+PDF + Upload Google Drive + Chat         ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import base64
import zipfile
import getpass
import urllib.request
import urllib.error
from pathlib import Path
from collections import Counter
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ── Configurações ──────────────────────────────────────────
URL_LOGIN         = "https://zecore.zebrands.mx/login#login"
URL_REPORT        = "https://zecore.zebrands.mx/app/arrangement/view/report/REPORT%203PL"
URL_SALES_INVOICE = "https://zecore.zebrands.mx/app/sales-invoice"
BASE_URL          = "https://zecore.zebrands.mx"


# ── Configurações JAMEF Portal ────────────────────────────
JAMEF_URL_BASE    = "https://cliente.jamef.com.br"
JAMEF_CGC         = "42418313000104"
JAMEF_CLIENT_ID   = "75lv5or3fufjp3trhse7bh508m"
JAMEF_USER_POOL   = "us-east-1_OUb3yXu8P"
JAMEF_COGNITO_URL = f"https://cognito-idp.us-east-1.amazonaws.com/"

PASTA_XMLS       = Path("xmls_baixados")
PASTA_LOGS       = Path("logs")
ARQUIVO_HISTORICO = Path("historico_processados.json")

PASTA_XMLS.mkdir(exist_ok=True)
PASTA_LOGS.mkdir(exist_ok=True)

load_dotenv()

# Webhook do Google Chat (Secret CHAT_WEBHOOK_URL)
WEBHOOK_URL = os.getenv("CHAT_WEBHOOK_URL", "").strip()

# Quantos dias um docname fica no histórico antes de ser podado
HISTORICO_DIAS = int(os.getenv("HISTORICO_DIAS", "60"))


# ════════════════════════════════════════════════════════════
#  CREDENCIAIS E CONFIGURAÇÕES
# ════════════════════════════════════════════════════════════
def obter_credenciais():
    email = os.getenv("SISTEMA_EMAIL", "").strip()
    senha = os.getenv("SISTEMA_SENHA", "").strip()

    print("\n" + "═" * 55)
    print("   BOT XML TRANSPORTADORAS — Zebrands/Luuna")
    print("═" * 55)

    if not email:
        email = input("\n📧  Email de acesso ao sistema: ").strip()
    else:
        print(f"\n📧  Email: {email}")

    if not senha:
        senha = getpass.getpass("🔒  Senha: ").strip()
    else:
        print("🔒  Senha: ••••••••")

    if not email or not senha:
        print("\n❌  Email e senha são obrigatórios.")
        sys.exit(1)

    return email, senha


def obter_transportadora() -> str:
    transportadora = os.getenv("TRANSPORTADORA", "").strip().upper()
    if not transportadora:
        print("\n   📋  Transportadoras disponíveis: JAMEF | FITLOG TRANSPORTES | MIRA TRANSPORTES")
        transportadora = input("   🚚  Qual transportadora processar? ").strip().upper()
    if not transportadora:
        print("\n❌  Nenhuma transportadora informada.")
        sys.exit(1)
    print(f"\n   ✅  Transportadora: {transportadora}")
    return transportadora


# ════════════════════════════════════════════════════════════
#  HISTÓRICO — evita reprocessar pedidos
# ════════════════════════════════════════════════════════════
def carregar_historico() -> dict:
    """
    Retorna {docname: data_iso}. Aceita o formato antigo (lista de docnames):
    esses entram com a data de hoje e saem sozinhos após HISTORICO_DIAS.
    """
    if not ARQUIVO_HISTORICO.exists():
        return {}
    try:
        dados = json.loads(ARQUIVO_HISTORICO.read_text(encoding="utf-8"))
    except Exception:
        return {}
    hoje = datetime.now().date().isoformat()
    brutos = dados.get("docnames_processados", {})
    if isinstance(brutos, list):
        return {d: hoje for d in brutos}
    return dict(brutos)


def salvar_historico(historico: dict, novos: set | None = None):
    """Grava o histórico, adicionando `novos` com a data de hoje e podando os antigos."""
    hoje = datetime.now().date()
    for d in (novos or set()):
        historico.setdefault(d, hoje.isoformat())

    limite = hoje.toordinal() - HISTORICO_DIAS
    podado = {}
    for d, data in historico.items():
        try:
            if datetime.fromisoformat(data).date().toordinal() >= limite:
                podado[d] = data
        except Exception:
            podado[d] = hoje.isoformat()

    dados = {
        "ultima_execucao": datetime.now().isoformat(),
        "docnames_processados": dict(sorted(podado.items())),
    }
    ARQUIVO_HISTORICO.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ════════════════════════════════════════════════════════════
#  LOGIN
# ════════════════════════════════════════════════════════════
def fazer_login(page, email: str, senha: str) -> bool:
    print(f"\n🌐  Fazendo login...")
    page.goto(URL_LOGIN, wait_until="networkidle", timeout=45_000)
    page.locator("#login_email").fill(email)
    page.locator("#login_password").fill(senha)
    page.locator("button.btn-login").click()
    try:
        page.wait_for_url(lambda url: "login" not in url, timeout=15_000)
        print("✅  Login OK!")
        return True
    except PlaywrightTimeout:
        print("❌  Falha no login.")
        capturar_screenshot(page, "erro_login")
        return False


# ════════════════════════════════════════════════════════════
#  NAVEGAR + FILTRAR REPORT
# ════════════════════════════════════════════════════════════
def navegar_para_report(page):
    print(f"\n🌐  Abrindo Report 3PL...")
    page.goto(URL_REPORT, wait_until="networkidle", timeout=45_000)
    page.wait_for_timeout(4_000)
    print("✅  Report carregado!")
    capturar_screenshot(page, "debug_01_report")


def filtrar_por_status(page):
    print("\n🔍  Filtrando por 'Ready To Ship'...")
    campo = page.locator("input.dt-filter[data-col-index='8']")
    try:
        campo.wait_for(timeout=15_000)
        campo.click()
        campo.fill("Ready To Ship")
        campo.press("Enter")
        page.wait_for_timeout(3_500)
        print("✅  Filtro de status aplicado!")
        capturar_screenshot(page, "debug_02_filtro_status")
    except PlaywrightTimeout:
        print("⚠️  Filtro de status não encontrado.")
        capturar_screenshot(page, "debug_02_erro_status")


def filtrar_por_carrier(page, carrier: str):
    print(f"\n🔍  Filtrando por Carrier = '{carrier}'...")
    campo = page.locator("input.dt-filter[data-col-index='4']")
    try:
        campo.wait_for(timeout=15_000)
        campo.click()
        campo.fill("")
        campo.fill(carrier)
        campo.press("Enter")
        page.wait_for_timeout(3_500)
        print(f"✅  Filtro de carrier aplicado: {carrier}")
        capturar_screenshot(page, f"debug_02b_carrier")
    except PlaywrightTimeout:
        print("⚠️  Filtro de carrier não encontrado.")
        capturar_screenshot(page, "debug_02b_erro_carrier")


# ════════════════════════════════════════════════════════════
#  LER TABELA — scroll progressivo (virtual scroll)
# ════════════════════════════════════════════════════════════
def ler_pedidos(page, transportadora_alvo: str) -> list[dict]:
    print("\n📋  Lendo pedidos da tabela...")

    try:
        page.wait_for_selector(".dt-cell", timeout=20_000)
    except PlaywrightTimeout:
        print("⚠️  Tabela vazia.")
        capturar_screenshot(page, "debug_03_vazia")
        return []

    # Clica em 500 para mostrar o máximo de linhas
    try:
        btn_500 = page.locator("button", has_text="500").first
        if btn_500.count() > 0:
            btn_500.click()
            page.wait_for_timeout(2_000)
    except Exception:
        pass

    # Descobre índices das colunas
    idx_carrier = None
    idx_docname = None

    for header in page.locator(".dt-cell--header").all():
        texto     = header.inner_text().strip().lower()
        col_index = header.get_attribute("data-col-index")
        if col_index is None:
            continue
        if "carrier" in texto:
            idx_carrier = col_index
        if "docname" in texto:
            idx_docname = col_index

    if idx_carrier is None or idx_docname is None:
        print("❌  Colunas não encontradas.")
        return []

    print(f"   ℹ️  Carrier: col={idx_carrier} | Docname: col={idx_docname}")

    # Scroll progressivo — coleta dados a cada passo
    capturados = {}
    altura_anterior = -1
    tentativas = 0

    while tentativas < 60:
        # Captura linhas visíveis agora
        linhas_js = page.evaluate(f"""
            (args) => {{
                const cells = document.querySelectorAll(`.dt-cell[data-col-index='${{args.idxCarrier}}'][data-row-index]`);
                const resultado = [];
                cells.forEach(c => {{
                    const ri = c.getAttribute('data-row-index');
                    const d = document.querySelector(`.dt-cell[data-col-index='${{args.idxDocname}}'][data-row-index='${{ri}}']`);
                    resultado.push({{
                        carrier: c.innerText.trim(),
                        docname: d ? d.innerText.trim() : ""
                    }});
                }});
                return resultado;
            }}
        """, {"idxCarrier": idx_carrier, "idxDocname": idx_docname})

        for item in linhas_js:
            if item["carrier"] and item["docname"]:
                capturados[item["docname"]] = item

        # Rola um passo
        altura_atual = page.evaluate("""
            () => {
                const el = document.querySelector('.dt-scrollable, .datatable-body, .dt-instance');
                if (!el) return -1;
                const passo = Math.floor(el.clientHeight * 0.7);
                el.scrollTop = el.scrollTop + passo;
                return el.scrollTop;
            }
        """)

        page.wait_for_timeout(400)

        if altura_atual == altura_anterior:
            # Última captura no fim
            linhas_final = page.evaluate(f"""
                (args) => {{
                    const cells = document.querySelectorAll(`.dt-cell[data-col-index='${{args.idxCarrier}}'][data-row-index]`);
                    const resultado = [];
                    cells.forEach(c => {{
                        const ri = c.getAttribute('data-row-index');
                        const d = document.querySelector(`.dt-cell[data-col-index='${{args.idxDocname}}'][data-row-index='${{ri}}']`);
                        resultado.push({{
                            carrier: c.innerText.trim(),
                            docname: d ? d.innerText.trim() : ""
                        }});
                    }});
                    return resultado;
                }}
            """, {"idxCarrier": idx_carrier, "idxDocname": idx_docname})
            for item in linhas_final:
                if item["carrier"] and item["docname"]:
                    capturados[item["docname"]] = item
            break

        altura_anterior = altura_atual
        tentativas += 1

    print(f"   ✅  {len(capturados)} docname(s) capturado(s).")
    capturar_screenshot(page, "debug_03_tabela")

    todos     = []
    filtrados = []

    for item in capturados.values():
        carrier = item["carrier"]
        docname = item["docname"]
        todos.append(carrier)
        if transportadora_alvo.upper() in carrier.upper():
            filtrados.append({"carrier": carrier, "docname": docname})

    # Remove duplicados
    vistos = set()
    filtrados_unicos = []
    for p in filtrados:
        if p["docname"] not in vistos:
            vistos.add(p["docname"])
            filtrados_unicos.append(p)
    filtrados = filtrados_unicos

    # Resumo
    print("\n" + "═" * 55)
    for carrier, qtd in Counter(todos).items():
        marcador = "✅" if transportadora_alvo.upper() in carrier.upper() else "  "
        print(f"   {marcador} {carrier}: {qtd} pedido(s)")
    print(f"\n   🎯  {transportadora_alvo}: {len(filtrados)} pedido(s) únicos")
    print("═" * 55)

    return filtrados


# ════════════════════════════════════════════════════════════
#  BUSCAR XML + PDF DE CADA PEDIDO
# ════════════════════════════════════════════════════════════
def buscar_arquivos_do_pedido(page, docname: str) -> list[str]:
    print(f"\n   🔎  Buscando XML+PDF para: {docname}")
    TIMEOUT = 8_000

    try:
        page.goto(URL_SALES_INVOICE, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_timeout(1_500)

        btn_filtros = page.locator("span.button-label", has_text="filter")
        if btn_filtros.count() == 0:
            btn_filtros = page.locator(".filter-button, [data-label='Filter']")
        btn_filtros.first.click(timeout=5_000)
        page.wait_for_timeout(600)

        campo = page.locator("input[data-fieldname='sales_order']")
        campo.wait_for(timeout=TIMEOUT)
        campo.fill(docname)
        page.wait_for_timeout(300)

        page.locator("button.apply-filters").click(timeout=5_000)
        page.wait_for_timeout(1_500)

        # Se não encontrar resultado em 8s, pula imediatamente
        resultado = page.locator(".list-row-col .ellipsis a, tbody tr td a").first
        try:
            resultado.wait_for(timeout=TIMEOUT)
        except PlaywrightTimeout:
            print(f"   ⚠️  Nenhum resultado encontrado para {docname} — pulando.")
            return []

        resultado.click()
        page.wait_for_timeout(1_500)

        # Se não encontrar XML/PDF em 8s, pula imediatamente
        try:
            page.wait_for_selector(
                ".attachment-row a[href*='/private/files/'][href$='.xml'], "
                ".attachment-row a[href*='/private/files/'][href$='.pdf']",
                timeout=TIMEOUT
            )
        except PlaywrightTimeout:
            print(f"   ⚠️  Nenhum XML/PDF nos attachments para {docname} — pulando.")
            return []

    except PlaywrightTimeout:
        print(f"   ⚠️  Timeout ao buscar {docname} — pulando.")
        return []
    except Exception as e:
        print(f"   ⚠️  Erro ao buscar {docname}: {type(e).__name__} — pulando.")
        return []

    links = page.locator(
        ".attachment-row a[href*='/private/files/'][href$='.xml'], "
        ".attachment-row a[href*='/private/files/'][href$='.pdf']"
    ).all()

    urls = []
    for link in links:
        href = link.get_attribute("href")
        if href:
            url = BASE_URL + href if href.startswith("/") else href
            urls.append(url)
            tipo = "XML" if href.endswith(".xml") else "PDF"
            print(f"   📎  {tipo}: {href.split('/')[-1]}")

    return urls
def baixar_arquivo(page, url: str, nome: str) -> Path | None:
    try:
        response = page.request.get(url)
        if response.ok:
            caminho = PASTA_XMLS / nome
            caminho.write_bytes(response.body())
            print(f"   ✅  Baixado: {nome}")
            return caminho
        else:
            print(f"   ❌  Erro HTTP {response.status}: {nome}")
            return None
    except Exception as e:
        print(f"   ❌  Erro: {e}")
        return None


# ════════════════════════════════════════════════════════════
#  CRIAR ZIP
# ════════════════════════════════════════════════════════════
def criar_zip(arquivos: list[Path], carrier: str) -> Path:
    carrier_limpo = carrier.strip().upper().replace(" ", "_")
    data_hoje = datetime.now().strftime("%d-%m-%Y")
    hora_agora = datetime.now().strftime("%H%M")
    nome_zip = PASTA_XMLS / f"{carrier_limpo}_{data_hoje}_{hora_agora}.zip"

    with zipfile.ZipFile(nome_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for arquivo in arquivos:
            if arquivo.suffix in (".xml", ".pdf"):
                zf.write(arquivo, arquivo.name)

    tamanho = nome_zip.stat().st_size / 1024
    print(f"\n📦  ZIP: {nome_zip.name} ({tamanho:.1f} KB) — {len(arquivos)} arquivo(s)")
    return nome_zip


# ════════════════════════════════════════════════════════════
#  ENVIAR ZIP POR EMAIL (Gmail / Google Workspace)
# ════════════════════════════════════════════════════════════
def enviar_zip_por_email(zip_path: Path, transportadora: str, pedidos: list[dict],
                          arquivos: list[Path], pedidos_sem_xml: list[dict] | None = None) -> bool:
    """
    Envia o ZIP com XMLs e PDFs por email.
    Destinatários e assunto variam por transportadora.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    GMAIL_USUARIO  = os.getenv("GMAIL_USUARIO", "carlos.berbert@zeb.mx")
    GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

    if not GMAIL_PASSWORD:
        print("   ⚠️  GMAIL_APP_PASSWORD não configurado — pulando envio de email.")
        return False

    data_hoje = datetime.now().strftime("%d/%m/%Y")
    agora     = datetime.now().strftime("%d/%m/%Y %H:%M")
    pedidos_sem_xml = pedidos_sem_xml or []
    pedidos_ok = [p for p in pedidos if p["docname"] not in {x["docname"] for x in pedidos_sem_xml}]

    # ── Configuração por transportadora ─────────────────────
    carrier_upper = transportadora.upper()

    if "FITLOG" in carrier_upper:
        destinatarios_para = ["Adm.operacional@fitlogistica.com.br", "assistenteoperacional1@fitlogistica.com.br"]
        destinatarios_cc   = ["expedicao.sp@fitlogistica.com.br", GMAIL_USUARIO, "felipe.azevedo@zeb.mx", "israel.lopes@zeb.mx"]
        assunto = f"COLETA LUUNA {data_hoje} - FITLOG"

    elif "MIRA" in carrier_upper:
        destinatarios_para = ["expedicao@mira.com.br"]
        destinatarios_cc   = [GMAIL_USUARIO, "felipe.azevedo@zeb.mx", "israel.lopes@zeb.mx"]
        assunto = f"COLETA LUUNA {data_hoje} - MIRA"

    else:  # JAMEF — mantém como estava
        destinatarios_para = [GMAIL_USUARIO, "felipe.azevedo@zeb.mx", "israel.lopes@zeb.mx"]
        destinatarios_cc   = []
        assunto = f"COLETA LUUNA {data_hoje} - JAMEF"

    todos_destinatarios = destinatarios_para + destinatarios_cc

    # ── Corpo do email ───────────────────────────────────────
    corpo = f"""
    <html><body style="font-family: Arial, sans-serif; color: #1a1a1a;">
    <div style="max-width:600px;margin:0 auto;padding:24px;">

      <div style="background:#1F5C99;padding:20px;border-radius:8px;margin-bottom:24px;">
        <h2 style="color:white;margin:0;">🚚 Bot XML Transportadoras</h2>
        <p style="color:#a8c8e8;margin:4px 0 0;">Zebrands / Luuna — {agora}</p>
      </div>

      <h3 style="color:#1F5C99;">📦 {transportadora} — {data_hoje}</h3>

      <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
        <tr style="background:#f0f7fd;">
          <td style="padding:10px;border:1px solid #daeaf8;font-weight:bold;">Pedidos processados</td>
          <td style="padding:10px;border:1px solid #daeaf8;">{len(pedidos_ok)}</td>
        </tr>
        <tr>
          <td style="padding:10px;border:1px solid #daeaf8;font-weight:bold;">Arquivos (XML + PDF)</td>
          <td style="padding:10px;border:1px solid #daeaf8;">{len(arquivos)}</td>
        </tr>
        <tr style="background:#f0f7fd;">
          <td style="padding:10px;border:1px solid #daeaf8;font-weight:bold;">ZIP anexado</td>
          <td style="padding:10px;border:1px solid #daeaf8;">{zip_path.name}</td>
        </tr>
      </table>

      {'<div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;padding:12px;margin-bottom:16px;"><strong>⚠️ Pedidos sem XML/PDF:</strong><ul>' + ''.join(f"<li>{p['docname']}</li>" for p in pedidos_sem_xml) + '</ul></div>' if pedidos_sem_xml else ''}

      <p style="color:#6b7280;font-size:12px;margin-top:24px;border-top:1px solid #e5e7eb;padding-top:12px;">
        Enviado automaticamente pelo Bot XML Transportadoras — Zebrands/Luuna
      </p>
    </div>
    </body></html>
    """

    try:
        print(f"\n📧  Enviando email — Assunto: {assunto}")
        print(f"   Para: {', '.join(destinatarios_para)}")
        if destinatarios_cc:
            print(f"   CC:   {', '.join(destinatarios_cc)}")

        msg = MIMEMultipart()
        msg["From"]    = GMAIL_USUARIO
        msg["To"]      = ", ".join(destinatarios_para)
        msg["Subject"] = assunto
        if destinatarios_cc:
            msg["Cc"] = ", ".join(destinatarios_cc)

        msg.attach(MIMEText(corpo, "html"))

        # Anexa o ZIP
        with open(zip_path, "rb") as f:
            parte = MIMEBase("application", "zip")
            parte.set_payload(f.read())
            encoders.encode_base64(parte)
            parte.add_header(
                "Content-Disposition",
                f"attachment; filename={zip_path.name}"
            )
            msg.attach(parte)

        # Envia via SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as servidor:
            servidor.login(GMAIL_USUARIO, GMAIL_PASSWORD)
            servidor.sendmail(GMAIL_USUARIO, todos_destinatarios, msg.as_string())

        print(f"   ✅  Email enviado com sucesso!")
        return True

    except Exception as e:
        print(f"   ❌  Erro ao enviar email: {e}")
        return False


# ════════════════════════════════════════════════════════════
#  NOTIFICAÇÕES — Google Chat
# ════════════════════════════════════════════════════════════
def _enviar_mensagem_chat(mensagem: str):
    if not WEBHOOK_URL:
        print("   ⚠️  CHAT_WEBHOOK_URL não configurado — pulando notificação no Chat.")
        return
    payload = json.dumps({"text": mensagem}).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print("✅  Notificação enviada no Chat!")
    except Exception as e:
        print(f"❌  Erro ao enviar Chat: {e}")


def enviar_notificacao(pedidos, arquivos, zip_path, drive_link=None,
                       pedidos_sem_xml=None, pedidos_free=None, transportadora=""):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    pedidos_sem_xml = pedidos_sem_xml or []
    pedidos_free    = pedidos_free or []
    pedidos_ok      = [p for p in pedidos if p["docname"] not in {x["docname"] for x in pedidos_sem_xml}]

    linhas = [
        f"🤖 *Bot XML Transportadoras — Zebrands/Luuna*",
        f"📅 {agora}",
        f"",
        f"📊 *Resumo — {transportadora}*",
        f"   • Pedidos encontrados: *{len(pedidos) + len(pedidos_free)}*",
        f"   • XMLs/PDFs baixados: *{len(arquivos)}* (em {len(pedidos_ok)} pedido(s))",
        f"   • Pedidos SEM XML: *{len(pedidos_sem_xml)}*",
        f"   • Pedidos FREE- ignorados: *{len(pedidos_free)}*",
        f"",
    ]

    if pedidos_sem_xml:
        linhas.append(f"⚠️ *PEDIDOS SEM XML — verificar ({len(pedidos_sem_xml)}):*")
        for p in pedidos_sem_xml:
            linhas.append(f"   • `{p['docname']}`")
        linhas.append("")

    linhas.append(f"✅ *XMLs baixados:* {len(pedidos_ok)} pedido(s)")
    linhas.append(f"🗜️ *ZIP:* `{zip_path.name}`")

    if drive_link:
        linhas.append(f"📨 *Envio:* {drive_link}")

    if pedidos_free:
        linhas.append(f"\n🎁 *FREE- ignorados ({len(pedidos_free)}):*")
        for p in pedidos_free:
            linhas.append(f"   • `{p['docname']}`")

    _enviar_mensagem_chat("\n".join(linhas))


def enviar_notificacao_vazia(motivo: str, pedidos_free=None, transportadora=""):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    linhas = [
        f"🤖 *Bot XML Transportadoras — Zebrands/Luuna*",
        f"📅 {agora}",
        f"",
        f"ℹ️ *{transportadora}* — Execução concluída.",
        motivo,
    ]
    if pedidos_free:
        linhas.append(f"\n🎁 *FREE- ignorados ({len(pedidos_free)}):*")
        for p in pedidos_free:
            linhas.append(f"   • `{p['docname']}`")
    _enviar_mensagem_chat("\n".join(linhas))


def enviar_notificacao_erro(erro: str, transportadora=""):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    _enviar_mensagem_chat(
        f"🤖 *Bot XML Transportadoras — Zebrands/Luuna*\n"
        f"📅 {agora}\n\n"
        f"❌ *Erro — {transportadora}:*\n`{erro}`"
    )


# ════════════════════════════════════════════════════════════
#  SCREENSHOT
# ════════════════════════════════════════════════════════════
def capturar_screenshot(page, nome: str):
    caminho = PASTA_LOGS / f"{nome}.png"
    page.screenshot(path=str(caminho), full_page=True)


# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════
#  PORTAL JAMEF — Login via AWS Cognito + Upload XMLs
# ════════════════════════════════════════════════════════════

def gmail_ler_codigo_mfa(remetente_filtro: str = "jamef", timeout_seg: int = 120) -> str | None:
    """
    Lê o código MFA enviado pela JAMEF no Gmail.
    Busca pelo assunto: 'Portal Cliente Jamef - Código de Verificação MFA'
    """
    import json
    import base64
    import time
    import re
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    print(f"\n📬  Aguardando código MFA no Gmail (até {timeout_seg}s)...")

    oauth_raw = os.getenv("GMAIL_OAUTH_TOKEN", "")
    if not oauth_raw:
        print("   ⚠️  GMAIL_OAUTH_TOKEN não configurado.")
        return None

    try:
        oauth_data = json.loads(base64.b64decode(oauth_raw).decode("utf-8"))
        creds = Credentials(
            token=None,
            refresh_token=oauth_data["refresh_token"],
            token_uri=oauth_data["token_uri"],
            client_id=oauth_data["client_id"],
            client_secret=oauth_data["client_secret"],
            scopes=["https://www.googleapis.com/auth/gmail.readonly"]
        )
        service = build("gmail", "v1", credentials=creds)

        inicio = time.time()
        # IDs de mensagens já vistas (para não reprocessar emails antigos)
        ids_vistos = set()

        # Primeiro scan: marca todos os emails existentes como já vistos
        try:
            resultado_inicial = service.users().messages().list(
                userId="me",
                q='from:naoresponda@jamef.com.br subject:"Portal Cliente Jamef"',
                maxResults=10
            ).execute()
            for msg in resultado_inicial.get("messages", []):
                ids_vistos.add(msg["id"])
            print(f"   ℹ️  {len(ids_vistos)} email(s) antigo(s) ignorado(s).")
        except Exception:
            pass

        while time.time() - inicio < timeout_seg:
            resultado = service.users().messages().list(
                userId="me",
                q='from:naoresponda@jamef.com.br subject:"Portal Cliente Jamef"',
                maxResults=5
            ).execute()

            mensagens = resultado.get("messages", [])
            for msg in mensagens:
                # Pula emails que já existiam antes do login
                if msg["id"] in ids_vistos:
                    continue

                msg_data = service.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="full"
                ).execute()

                corpo = _extrair_corpo_email(msg_data.get("payload", {}))

                # Procura por código de 6 dígitos
                codigos = re.findall(r'\b(\d{6})\b', corpo)
                if codigos:
                    codigo = codigos[0]
                    print(f"   ✅  Código MFA encontrado: {codigo}")
                    return codigo

            print(f"   ⏳  Aguardando email... ({int(time.time()-inicio)}s)")
            time.sleep(5)

        print("   ❌  Timeout — código MFA não encontrado no Gmail.")
        return None

    except Exception as e:
        print(f"   ❌  Erro ao ler Gmail: {e}")
        return None


def _extrair_corpo_email(payload: dict) -> str:
    """Extrai o texto do corpo do email recursivamente."""
    import base64
    corpo = ""
    if "parts" in payload:
        for part in payload["parts"]:
            corpo += _extrair_corpo_email(part)
    elif payload.get("mimeType") in ("text/plain", "text/html"):
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                corpo = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            except Exception:
                pass
    return corpo


def jamef_login(email: str, senha: str) -> str | None:
    """
    Faz login no portal JAMEF via API própria.
    1. POST /api/auth/login → retorna session + challengeName EMAIL_MFA
    2. Bot lê o código do Gmail
    3. POST /api/auth/confirm-mfa → retorna o token
    """
    import urllib.request
    import json

    print("\n🔐  Fazendo login no portal JAMEF...")

    # Passo 1: Login inicial
    payload = json.dumps({
        "email": email,
        "password": senha
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{JAMEF_URL_BASE}/api/auth/login",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Origin": JAMEF_URL_BASE,
            "Referer": f"{JAMEF_URL_BASE}/login"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        challenge = data.get("challengeName")
        session   = data.get("session")

        print(f"   ℹ️  Challenge: {challenge}")
        print(f"   ℹ️  Mensagem: {data.get('message', '')}")

        if challenge == "EMAIL_MFA" and session:
            # Passo 2: Lê código MFA do Gmail
            codigo = gmail_ler_codigo_mfa()
            if not codigo:
                print("   ❌  Código MFA não encontrado no Gmail.")
                return None

            # Passo 3: Confirma MFA
            return jamef_confirmar_mfa(email, codigo, session)

        # Se por algum motivo retornou token direto
        token = data.get("idToken") or data.get("token") or data.get("accessToken")
        if token:
            print("   ✅  Login JAMEF OK (sem MFA)!")
            return token

        print(f"   ❌  Resposta inesperada: {str(data)[:200]}")
        return None

    except urllib.error.HTTPError as e:
        erro = e.read().decode("utf-8") if e.fp else str(e)
        print(f"   ❌  Erro HTTP {e.code}: {erro[:200]}")
        return None
    except Exception as e:
        print(f"   ❌  Erro no login JAMEF: {e}")
        return None


def jamef_confirmar_mfa(email: str, codigo: str, session: str | None) -> str | None:
    """Confirma o código MFA no portal JAMEF e retorna o idToken dos cookies."""
    import urllib.request
    import urllib.parse
    import http.cookiejar
    import json

    print(f"   🔐  Confirmando código MFA: {codigo}")

    payload = json.dumps({
        "challengeName": "EMAIL_MFA",
        "email": email,
        "mfaCode": codigo,
        "session": session
    }).encode("utf-8")

    # Usa CookieJar para capturar os cookies da resposta
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar)
    )

    req = urllib.request.Request(
        f"{JAMEF_URL_BASE}/api/auth/confirm-mfa",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Origin": JAMEF_URL_BASE,
            "Referer": f"{JAMEF_URL_BASE}/login"
        },
        method="POST"
    )

    try:
        with opener.open(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            print(f"   ℹ️  Resposta confirm-mfa: {body}")

        # Extrai idToken dos cookies
        for cookie in cookie_jar:
            if cookie.name == "idToken":
                print("   ✅  MFA confirmado! idToken obtido dos cookies.")
                return cookie.value

        # Fallback: tenta accessToken
        for cookie in cookie_jar:
            if cookie.name == "accessToken":
                print("   ✅  MFA confirmado! accessToken obtido dos cookies.")
                return cookie.value

        print(f"   ❌  Token não encontrado nos cookies.")
        print(f"   ℹ️  Cookies recebidos: {[c.name for c in cookie_jar]}")
        return None

    except urllib.error.HTTPError as e:
        erro = e.read().decode("utf-8") if e.fp else str(e)
        print(f"   ❌  confirm-mfa HTTP {e.code}: {erro[:200]}")
        return None
    except Exception as e:
        print(f"   ❌  confirm-mfa erro: {e}")
        return None


def jamef_extrair_dados_xml(xml_path: Path) -> dict:
    """
    Extrai dados importantes do XML da NF-e:
    - chave: chave de acesso 44 dígitos (para gerar etiqueta)
    - nNF: número da nota fiscal (para o OMS)
    - filial: código da filial
    """
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(str(xml_path))
        root = tree.getroot()

        # Remove namespace para facilitar busca
        def sem_ns(tag):
            return tag.split("}")[-1] if "}" in tag else tag

        def encontrar(root, tag_alvo):
            for el in root.iter():
                if sem_ns(el.tag) == tag_alvo:
                    return el.text
            return None

        # Chave de acesso (44 dígitos) — vem no atributo Id da tag infNFe
        chave = None
        for el in root.iter():
            if sem_ns(el.tag) == "infNFe":
                id_attr = el.get("Id", "")
                if id_attr.startswith("NFe"):
                    chave = id_attr[3:]  # remove "NFe" do início
                break

        # Se não achou no Id, tenta na tag chNFe
        if not chave:
            chave = encontrar(root, "chNFe")

        # Número da NF
        n_nf = encontrar(root, "nNF")

        # Filial (padrão 57)
        filial = "57"

        print(f"   📋  XML: chave={chave[:10] if chave else 'N/A'}... | NF={n_nf} | filial={filial}")

        return {
            "chave": chave,
            "nNF": n_nf,
            "filial": filial
        }

    except Exception as e:
        print(f"   ⚠️  Erro ao extrair dados do XML: {e}")
        return {"chave": None, "nNF": None, "filial": "57"}


def _jamef_render_pdf(chave: str, id_token: str, silencioso: bool = False) -> bytes | None:
    """
    Pede o PDF da etiqueta direto na API da JAMEF.
    Retorna os bytes só se vier um PDF de verdade; senão None.
    """
    payload = json.dumps({"chave": chave}).encode("utf-8")
    req = urllib.request.Request(
        f"{JAMEF_URL_BASE}/api/label/render",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": id_token,
            "Cookie": f"idToken={id_token}",
            "Origin": JAMEF_URL_BASE,
            "Referer": f"{JAMEF_URL_BASE}/etiquetas",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            conteudo = resp.read()
    except urllib.error.HTTPError as e:
        corpo = ""
        try:
            corpo = e.read()[:150].decode("utf-8", "ignore")
        except Exception:
            pass
        if not silencioso:
            print(f"   ℹ️  render HTTP {e.code}: {corpo}")
        return None
    except Exception as e:
        if not silencioso:
            print(f"   ℹ️  render falhou: {e}")
        return None

    if conteudo[:4] == b"%PDF":
        return conteudo

    pdf = _jamef_labels_json_para_pdf(conteudo)
    if pdf:
        return pdf

    if not silencioso:
        print(f"   ℹ️  render respondeu sem etiqueta: {conteudo[:120]!r}")
    return None


def _jamef_labels_json_para_pdf(conteudo: bytes) -> bytes | None:
    """
    A API /api/label/render responde JSON: {"labels": [{"base64": "iVBOR..."}]}
    Cada item é a imagem PNG de uma etiqueta (1 por volume). Junta todas num
    PDF (uma página por etiqueta). Se vier PDF em base64, usa direto.
    """
    import io
    try:
        dados = json.loads(conteudo)
    except Exception:
        return None

    labels = dados.get("labels") if isinstance(dados, dict) else dados
    if not isinstance(labels, list) or not labels:
        return None

    imagens, pdfs = [], []
    for item in labels:
        b64 = item.get("base64") if isinstance(item, dict) else item
        if not isinstance(b64, str) or not b64:
            continue
        if "," in b64[:40]:                 # "data:image/png;base64,...."
            b64 = b64.split(",", 1)[1]
        try:
            bruto = base64.b64decode(b64)
        except Exception:
            continue
        if bruto[:4] == b"%PDF":
            pdfs.append(bruto)
            continue
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(bruto))
            img.load()
            imagens.append(img)
        except Exception as e:
            print(f"   ⚠️  Etiqueta em formato não reconhecido: {e}")

    if imagens:
        paginas = [im.convert("RGB") for im in imagens]
        dpi = imagens[0].info.get("dpi", (203, 203))[0] or 203
        buf = io.BytesIO()
        paginas[0].save(buf, "PDF", save_all=True,
                        append_images=paginas[1:], resolution=float(dpi))
        return buf.getvalue()
    if pdfs:
        if len(pdfs) > 1:
            print(f"   ⚠️  {len(pdfs)} PDFs de etiqueta recebidos — usando o primeiro.")
        return pdfs[0]
    return None


def jamef_verificar_status_etiqueta(chave: str, id_token: str, n_nf: str,
                                     page=None,
                                     max_tentativas: int = 12, intervalo: int = 10) -> str:
    """
    Espera a etiqueta da NF ficar pronta na JAMEF.

    1º tenta pela API (/api/label/render): se já devolve PDF, a etiqueta está
       pronta — salva o arquivo e retorna 'sucesso' (jamef_baixar_etiqueta
       reaproveita o arquivo).
    2º fallback: lê a tabela da tela /etiquetas. O portal é uma SPA que nunca
       fica com a rede parada, por isso NÃO usa wait_until="networkidle"
       (era o que dava Timeout 20000ms em toda tentativa).
    """
    import time

    caminho = PASTA_XMLS / f"etiqueta_JAMEF_NF{n_nf}.pdf"
    variantes_nf = {str(n_nf), f"{int(n_nf):,}".replace(",", ".")} if str(n_nf).isdigit() else {str(n_nf)}

    print(f"   ⏳  Aguardando processamento da etiqueta NF {n_nf}...")

    for tentativa in range(max_tentativas):
        time.sleep(intervalo)
        print(f"   ⏳  Verificando status NF {n_nf} (tentativa {tentativa+1}/{max_tentativas})...")

        # ── 1. API ────────────────────────────────────────────
        pdf = _jamef_render_pdf(chave, id_token)
        if pdf:
            caminho.write_bytes(pdf)
            print(f"   ✅  NF {n_nf}: etiqueta pronta (via API) — {caminho.name}")
            return "sucesso"

        # ── 2. Tela /etiquetas ────────────────────────────────
        if page is None:
            continue
        try:
            page.goto(f"{JAMEF_URL_BASE}/etiquetas",
                      wait_until="domcontentloaded", timeout=30_000)

            if "login" in page.url.lower():
                print("   ⚠️  Portal JAMEF redirecionou para o login — cookie não aceito na tela.")
                continue

            seletor_linhas = "table tbody tr, .MuiTableBody-root tr"
            try:
                page.locator(seletor_linhas).first.wait_for(timeout=15_000)
            except PlaywrightTimeout:
                print("   ⏳  Tabela de etiquetas ainda não carregou.")
                continue

            for linha in page.locator(seletor_linhas).all():
                texto = linha.inner_text().replace("\n", " ").strip()
                if not (chave in texto or any(v in texto for v in variantes_nf)):
                    continue
                status_lower = texto.lower()
                print(f"   ℹ️  NF {n_nf}: {texto[:80]}")
                if "sucesso" in status_lower:
                    print(f"   ✅  NF {n_nf}: Sucesso!")
                    return "sucesso"
                if "ja cadastrada" in status_lower or "já cadastrada" in status_lower:
                    print(f"   ℹ️  NF {n_nf}: Já cadastrada.")
                    return "ja_cadastrada"
                print(f"   ⏳  NF {n_nf}: ainda processando...")
                break

        except Exception as e:
            print(f"   ⚠️  Erro na verificação (tentativa {tentativa+1}): {e}")

    print(f"   ⚠️  NF {n_nf}: timeout aguardando etiqueta.")
    if page is not None:
        capturar_screenshot(page, f"jamef_timeout_NF{n_nf}")
    return "timeout"


def jamef_baixar_etiqueta(chave: str, id_token: str, n_nf: str, page=None) -> Path | None:
    """
    Baixa a etiqueta PDF da JAMEF clicando no botão de imprimir
    e capturando a nova aba que abre com o PDF.
    """
    import urllib.request
    import json

    # Se já foi salva durante a verificação de status, retorna direto
    caminho = PASTA_XMLS / f"etiqueta_JAMEF_NF{n_nf}.pdf"
    if caminho.exists() and caminho.stat().st_size > 100:
        print(f"   ✅  Etiqueta já disponível: {caminho.name}")
        return caminho

    print(f"   🏷️  Baixando etiqueta para NF {n_nf}...")

    # Tenta via API primeiro (request direto)
    pdf = _jamef_render_pdf(chave, id_token)
    if pdf:
        caminho.write_bytes(pdf)
        print(f"   ✅  Etiqueta salva via API: {caminho.name}")
        return caminho
    print("   ⚠️  API não devolveu PDF, tentando via browser...")

    # Fallback: usa o Playwright para clicar no botão e capturar a nova aba
    if page is None:
        print(f"   ❌  Sem page disponível para baixar etiqueta NF {n_nf}.")
        return None

    try:
        # Navega para a tela de etiquetas da JAMEF
        page.goto(f"{JAMEF_URL_BASE}/etiquetas", wait_until="domcontentloaded", timeout=30_000)
        page.locator("table tbody tr, .MuiTableBody-root tr").first.wait_for(timeout=15_000)

        # Encontra o botão de imprimir/download da NF correta
        # O botão fica na linha que contém o número da NF
        linhas = page.locator("table tbody tr, .MuiTableBody-root tr").all()
        btn_imprimir = None

        for linha in linhas:
            if str(n_nf) in linha.inner_text():
                # Botão de imprimir é o último elemento da linha (ícone de impressora)
                btn_imprimir = linha.locator("button, a[href*='print'], svg").last
                break

        if btn_imprimir is None:
            print(f"   ❌  Botão de imprimir não encontrado para NF {n_nf}.")
            return None

        # Captura a nova aba que abre ao clicar no botão
        with page.context.expect_page() as nova_aba_info:
            btn_imprimir.click()

        nova_aba = nova_aba_info.value
        nova_aba.wait_for_load_state("domcontentloaded", timeout=15_000)
        page.wait_for_timeout(2_000)

        # Baixa o PDF da nova aba via request autenticado
        url_pdf = nova_aba.url
        print(f"   ℹ️  URL da etiqueta: {url_pdf[:60]}...")

        response = page.request.get(url_pdf)
        if response.ok:
            caminho.write_bytes(response.body())
            print(f"   ✅  Etiqueta salva via browser: {caminho.name}")
            nova_aba.close()
            return caminho
        else:
            print(f"   ❌  Erro ao baixar PDF: HTTP {response.status}")
            nova_aba.close()
            return None

    except Exception as e:
        print(f"   ❌  Erro ao capturar etiqueta NF {n_nf}: {e}")
        return None


PLATINUM_URL_BASE   = "https://oms.tpl.com.br"
PLATINUM_URL_UPLOAD = f"{PLATINUM_URL_BASE}/pedidoEtiqueta"
PLATINUM_MODELO     = "PDF - PADRAO"        # "PDF - PADRAO (15cm x 11cm)"


def platinum_fazer_login(page) -> bool:
    """Login no Platinum/TPL OMS (tela com E-mail, Senha e botão Entrar)."""
    email = os.getenv("PLATINUM_EMAIL", "").strip()
    senha = os.getenv("PLATINUM_SENHA", "").strip()
    if not email or not senha:
        print("   ⚠️  PLATINUM_EMAIL/PLATINUM_SENHA não configurados — pulando Platinum.")
        return False

    print("\n🔐  Fazendo login no Platinum OMS...")
    try:
        page.goto(PLATINUM_URL_BASE, wait_until="domcontentloaded", timeout=30_000)
        campo_senha = page.locator("input[type='password']").first
        campo_senha.wait_for(timeout=15_000)

        page.locator("input[type='email'], input[name*='mail' i], input[id*='mail' i]").first.fill(email)
        campo_senha.fill(senha)

        botao = page.get_by_role("button", name="Entrar")
        if botao.count():
            botao.first.click()
        else:
            page.locator("button[type='submit'], input[type='submit']").first.click()

        # Depois do login cai em /home
        page.wait_for_url(lambda u: "login" not in u.lower() and u.rstrip("/") != PLATINUM_URL_BASE,
                          timeout=20_000)
        print(f"   ✅  Login Platinum OK ({page.url})")
        return True
    except Exception as e:
        print(f"   ❌  Falha no login Platinum: {str(e).splitlines()[0]}")
        capturar_screenshot(page, "platinum_erro_login")
        return False


def _platinum_fechar_popup(page, timeout: int = 10_000) -> str:
    """Espera o popup (SweetAlert, ex.: 'Fique ligado !'), lê o texto e clica OK."""
    try:
        popup = page.locator(".swal2-popup, .swal-modal, [role='dialog']").first
        popup.wait_for(state="visible", timeout=timeout)
        texto = " ".join(popup.inner_text().split())
        botao_ok = popup.locator(".swal2-confirm, .swal-button, button:has-text('OK')").first
        if botao_ok.count():
            botao_ok.click()
        page.wait_for_timeout(500)
        return texto
    except Exception:
        return ""


def _platinum_escolher_modelo(page) -> bool:
    """Seleciona 'PDF - PADRAO (15cm x 11cm)' no campo MODELO DA ETIQUETA."""
    vistos = []
    for sel in page.locator("select").all():
        try:
            opcoes = sel.locator("option").all_inner_texts()
        except Exception:
            continue
        vistos.append(opcoes)
        norm = lambda t: " ".join(t.replace("\xa0", " ").split()).lower()
        alvo = next((o for o in opcoes if norm(PLATINUM_MODELO) in norm(o)), None)
        if alvo:
            sel.select_option(label=alvo)
            return True

    # Fallback: dropdown montado com div (não <select>) — abre pelo "SELECIONE..."
    try:
        bloco = page.locator("text=MODELO DA ETIQUETA").first.locator("xpath=..")
        bloco.get_by_text("SELECIONE", exact=False).first.click(timeout=5_000)
        page.get_by_text(PLATINUM_MODELO, exact=False).first.click(timeout=5_000)
        return True
    except Exception:
        pass

    print(f"   ❌  Modelo '{PLATINUM_MODELO}' não encontrado. Selects na página: {vistos}")
    return False


def platinum_upload_etiqueta(page, pdf_path: Path, n_nf: str) -> bool:
    """
    Sobe a etiqueta no Platinum — mesmo passo a passo do vídeo:
      Saídas > Pedidos > Carregar etiquetas (/pedidoEtiqueta)
      PEDIDO: "Zecore {NF}-1" | MODELO: PDF - PADRAO (15cm x 11cm) | PDF | UPLOAD
    """
    pedido_oms = f"Zecore {n_nf}-1"
    print(f"   🏷️  Platinum — {pedido_oms}")

    try:
        page.goto(PLATINUM_URL_UPLOAD, wait_until="domcontentloaded", timeout=30_000)
        if "login" in page.url.lower() or page.locator("input[type='password']").count():
            if not platinum_fazer_login(page):
                return False
            page.goto(PLATINUM_URL_UPLOAD, wait_until="domcontentloaded", timeout=30_000)

        # PEDIDO
        campo_pedido = page.locator(
            "input[placeholder*='pedido' i], input[name='pedido'], input[id*='pedido' i]"
        ).first
        campo_pedido.wait_for(timeout=15_000)
        campo_pedido.fill("")
        campo_pedido.fill(pedido_oms)
        campo_pedido.press("Tab")

        # MODELO DA ETIQUETA
        # A página tem outros <select> no topo (cliente, unidade...). Procura
        # o <select> que tem a opção "PDF - PADRAO" em vez de pegar o primeiro.
        if not _platinum_escolher_modelo(page):
            capturar_screenshot(page, f"platinum_modelo_NF{n_nf}")
            return False

        # PDF
        page.locator("input[type='file']").first.set_input_files(str(pdf_path))
        page.wait_for_timeout(800)

        # UPLOAD
        botao = page.get_by_role("button", name="UPLOAD", exact=True)
        if botao.count():
            botao.first.click()
        else:
            page.locator("input[value='UPLOAD' i], button:has-text('UPLOAD')").first.click()

        texto = _platinum_fechar_popup(page, timeout=20_000)
        if texto:
            print(f"   ℹ️  Platinum respondeu: {texto[:150]}")

        baixo = texto.lower()
        sucesso = "fique ligado" in baixo      # popup padrão de sucesso do Platinum
        erro = not sucesso and any(p in baixo for p in ("erro", "não encontrado", "nao encontrado",
                                                          "inválid", "invalid", "falha"))
        if not texto:
            print(f"   ⚠️  NF {n_nf}: nenhum popup apareceu após o UPLOAD — conferir no Platinum.")
            capturar_screenshot(page, f"platinum_sem_popup_NF{n_nf}")
            return False
        if erro:
            print(f"   ❌  NF {n_nf}: Platinum recusou a etiqueta.")
            capturar_screenshot(page, f"platinum_recusou_NF{n_nf}")
            return False

        print(f"   ✅  NF {n_nf}: etiqueta enviada ao Platinum.")
        return True

    except Exception as e:
        print(f"   ❌  Erro no Platinum NF {n_nf}: {str(e).splitlines()[0]}")
        capturar_screenshot(page, f"platinum_erro_NF{n_nf}")
        return False


def platinum_subir_etiquetas(context, nfs: list[str]) -> dict:
    """FASE 3 — sobe no Platinum as etiquetas JAMEF já baixadas."""
    ok, falha = [], []
    if not nfs:
        return {"ok": ok, "falha": falha}

    print(f"\n📦  FASE 3 — Subindo {len(nfs)} etiqueta(s) no Platinum OMS...")
    pagina = context.new_page()
    try:
        if not platinum_fazer_login(pagina):
            return {"ok": ok, "falha": [f"NF {n}" for n in nfs], "pulado": True}
        for i, n_nf in enumerate(nfs, 1):
            print(f"   [{i}/{len(nfs)}]", end="")
            pdf = PASTA_XMLS / f"etiqueta_JAMEF_NF{n_nf}.pdf"
            if not pdf.exists():
                print(f"   ⚠️  NF {n_nf}: arquivo da etiqueta não encontrado.")
                falha.append(f"NF {n_nf}")
            elif platinum_upload_etiqueta(pagina, pdf, n_nf):
                ok.append(f"NF {n_nf}")
            else:
                falha.append(f"NF {n_nf}")
    finally:
        try:
            pagina.close()
        except Exception:
            pass

    print(f"   📊  Platinum: {len(ok)} OK, {len(falha)} falha(s)")
    return {"ok": ok, "falha": falha}


def jamef_extrair_filial(xml_path: Path) -> str:
    """
    Extrai o código da filial do XML da NF-e.
    Tenta ler o campo cMunFG (município do fato gerador) ou usa '57' como padrão.
    """
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(str(xml_path))
        root = tree.getroot()

        # Remove namespace para facilitar a busca
        ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

        # Tenta encontrar a filial pelo CNPJ emitente — mapeamento fixo
        # Por padrão usa filial 57 (matriz)
        return "57"

    except Exception:
        return "57"


def jamef_enviar_xml(xml_path: Path, id_token: str) -> dict:
    """
    Envia um XML para o portal JAMEF via API.
    Retorna dict com status do envio, chave NF-e e número da NF.
    """
    import urllib.request
    import json

    nome = xml_path.name

    # Extrai dados do XML antes de enviar
    dados_xml = jamef_extrair_dados_xml(xml_path)
    chave     = dados_xml.get("chave")
    n_nf      = dados_xml.get("nNF")
    filial    = dados_xml.get("filial", "57")

    try:
        xml_bytes  = xml_path.read_bytes()
        xml_base64 = base64.b64encode(xml_bytes).decode("utf-8")

        payload = json.dumps({
            "cgc": JAMEF_CGC,
            "filialOrigem": filial,
            "xmlBase64": xml_base64
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{JAMEF_URL_BASE}/api/label/send-note",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": id_token,
                "Cookie": f"idToken={id_token}",
                "Origin": JAMEF_URL_BASE,
                "Referer": f"{JAMEF_URL_BASE}/etiquetas"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            body   = resp.read().decode("utf-8")
            if status in (200, 201):
                print(f"   ✅  {nome} enviado! (NF: {n_nf})")
                return {"arquivo": nome, "ok": True, "status": status, "chave": chave, "nNF": n_nf}
            else:
                print(f"   ⚠️  {nome}: resposta {status}")
                return {"arquivo": nome, "ok": False, "status": status, "erro": body, "chave": chave, "nNF": n_nf}

    except urllib.error.HTTPError as e:
        erro = e.read().decode("utf-8") if e.fp else str(e)
        print(f"   ❌  {nome}: HTTP {e.code} — {erro[:100]}")
        return {"arquivo": nome, "ok": False, "status": e.code, "erro": erro}
    except Exception as e:
        print(f"   ❌  {nome}: {e}")
        return {"arquivo": nome, "ok": False, "erro": str(e)}


def jamef_upload_xmls(xmls: list[Path], page=None,
                      espera_inicial: int = 60, intervalo: int = 30,
                      max_rodadas: int = 6) -> dict:
    """
    Fluxo JAMEF em duas fases (antes era uma NF por vez, esperando a etiqueta
    de cada uma antes de mandar a próxima — com muitas notas estourava o
    tempo do job e/ou o token, e as últimas nem eram enviadas):

      FASE 1 — envia TODOS os XMLs para o portal, um atrás do outro.
      FASE 2 — espera e busca as etiquetas de todas as NFs juntas, em rodadas.
    """
    import time

    email = os.getenv("JAMEF_EMAIL", "carlos.berbert@zeb.mx")
    senha = os.getenv("JAMEF_SENHA", "")

    if not senha:
        print("   ⚠️  JAMEF_SENHA não configurada — pulando upload JAMEF.")
        return {"ok": [], "falha": [], "pulado": True}

    apenas_xmls = [f for f in xmls if f.suffix.lower() == ".xml"]
    if not apenas_xmls:
        print("   ⚠️  Nenhum XML para enviar ao portal JAMEF.")
        return {"ok": [], "falha": []}

    id_token = jamef_login(email, senha)
    if not id_token:
        return {"ok": [], "falha": [f.name for f in apenas_xmls]}

    # ══════════════ FASE 1 — envia todos os XMLs ══════════════
    print(f"\n📤  FASE 1 — Enviando {len(apenas_xmls)} XML(s) para o portal JAMEF...")
    resultados_ok, resultados_falha = [], []
    pendentes = {}          # n_nf -> chave (aguardando etiqueta)
    relogou = False

    for i, xml_path in enumerate(apenas_xmls, 1):
        print(f"   [{i}/{len(apenas_xmls)}] {xml_path.name}")
        resultado = jamef_enviar_xml(xml_path, id_token)

        # Token expirou no meio: loga de novo uma vez e reenvia este XML
        if not resultado["ok"] and resultado.get("status") in (401, 403) and not relogou:
            print("   🔄  Token recusado — refazendo login na JAMEF...")
            relogou = True
            novo_token = jamef_login(email, senha)
            if novo_token:
                id_token = novo_token
                resultado = jamef_enviar_xml(xml_path, id_token)

        # "Já cadastrada" não é falha: a etiqueta já existe no portal
        erro_txt = str(resultado.get("erro", "")).lower()
        ja_cadastrada = "cadastrad" in erro_txt

        if resultado["ok"] or ja_cadastrada:
            resultados_ok.append(resultado["arquivo"])
            dados = jamef_extrair_dados_xml(xml_path)
            chave = resultado.get("chave") or dados.get("chave")
            n_nf  = resultado.get("nNF") or dados.get("nNF")
            if chave and n_nf:
                pendentes[str(n_nf)] = chave
            else:
                print(f"   ⚠️  Chave/NF não encontrada em {xml_path.name} — etiqueta não será buscada.")
        else:
            resultados_falha.append(resultado["arquivo"])

    print(f"\n   📊  Fase 1: {len(resultados_ok)} XML(s) aceitos, {len(resultados_falha)} falha(s)")

    # ══════════════ FASE 2 — busca as etiquetas em lote ══════════════
    etiquetas_ok = []
    total = len(pendentes)
    if pendentes:
        print(f"\n🏷️  FASE 2 — Aguardando {espera_inicial}s para a JAMEF liberar as {total} etiqueta(s)...")
        time.sleep(espera_inicial)

    for rodada in range(1, max_rodadas + 1):
        if not pendentes:
            break
        print(f"   🔁  Rodada {rodada}/{max_rodadas} — faltam {len(pendentes)}/{total}")

        # Pede a etiqueta de cada NF pendente direto na API da JAMEF
        # (vem como imagem PNG dentro de um JSON; vira PDF aqui)
        for n_nf, chave in list(pendentes.items()):
            pdf = _jamef_render_pdf(chave, id_token, silencioso=True)
            if pdf:
                caminho = PASTA_XMLS / f"etiqueta_JAMEF_NF{n_nf}.pdf"
                caminho.write_bytes(pdf)
                etiquetas_ok.append(f"NF {n_nf}")
                del pendentes[n_nf]
                print(f"   ✅  NF {n_nf}: etiqueta salva")

        if pendentes and rodada < max_rodadas:
            time.sleep(intervalo)

    etiquetas_falha = []
    for n_nf in pendentes:
        etiquetas_falha.append(f"NF {n_nf}")
        print(f"   ⚠️  NF {n_nf}: etiqueta não liberada após {max_rodadas} tentativas")
        # mostra a última resposta da API para diagnóstico
        _jamef_render_pdf(pendentes[n_nf], id_token, silencioso=False)

    # ══════════════ FASE 3 — Platinum ══════════════
    nfs_com_etiqueta = [e.replace("NF ", "") for e in etiquetas_ok]
    if page is not None and nfs_com_etiqueta:
        plat = platinum_subir_etiquetas(page.context, nfs_com_etiqueta)
    else:
        plat = {"ok": [], "falha": []}

    print(f"\n   📊  JAMEF Portal: {len(resultados_ok)} XML(s) enviado(s), {len(resultados_falha)} falha(s)")
    print(f"   🏷️  Etiquetas: {len(etiquetas_ok)} OK, {len(etiquetas_falha)} pendente(s)")
    return {
        "ok": resultados_ok,
        "falha": resultados_falha,
        "etiquetas_ok": etiquetas_ok,
        "etiquetas_falha": etiquetas_falha,
        "platinum_ok": plat.get("ok", []),
        "platinum_falha": plat.get("falha", []),
        "platinum_pulado": plat.get("pulado", False),
    }


def main():
    email, senha = obter_credenciais()
    transportadora = obter_transportadora()

    with sync_playwright() as p:
        is_ci = os.getenv("CI", "false").lower() == "true"
        browser = p.chromium.launch(headless=is_ci, slow_mo=0 if is_ci else 400)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True
        )
        page = context.new_page()

        try:
            # 1. Login
            if not fazer_login(page, email, senha):
                enviar_notificacao_erro("Falha no login.", transportadora)
                return

            # 2. Report + filtros
            navegar_para_report(page)
            filtrar_por_status(page)
            filtrar_por_carrier(page, transportadora)
            pedidos = ler_pedidos(page, transportadora)

            if not pedidos:
                enviar_notificacao_vazia(
                    f"Nenhum pedido com status 'Ready To Ship' encontrado.",
                    transportadora=transportadora
                )
                return

            # 3. Filtra já processados
            historico = carregar_historico()
            pedidos_novos = [p for p in pedidos if p["docname"] not in historico]
            ja_processados = len(pedidos) - len(pedidos_novos)

            if ja_processados > 0:
                print(f"\n   ℹ️  {ja_processados} pedido(s) já processados anteriormente — pulando.")

            if not pedidos_novos:
                enviar_notificacao_vazia(
                    f"Todos os {len(pedidos)} pedido(s) já foram processados anteriormente.",
                    transportadora=transportadora
                )
                return

            # 4. Separa FREE-
            pedidos_free = [p for p in pedidos_novos if p["docname"].upper().startswith("FREE-")]
            pedidos_novos = [p for p in pedidos_novos if not p["docname"].upper().startswith("FREE-")]

            if not pedidos_novos:
                enviar_notificacao_vazia(
                    "Nenhum pedido com XML para processar (apenas FREE-).",
                    pedidos_free=pedidos_free,
                    transportadora=transportadora
                )
                # Marca FREE no histórico
                salvar_historico(historico, {p["docname"] for p in pedidos_free})
                return

            print(f"\n   🆕  {len(pedidos_novos)} pedido(s) novos para processar.")

            # 5. Baixa XML + PDF de cada pedido
            todos_arquivos = []
            pedidos_sem_xml = []
            docnames_ok = set()

            for pedido in pedidos_novos:
                docname = pedido["docname"]
                try:
                    urls = buscar_arquivos_do_pedido(page, docname)
                    urls = list(dict.fromkeys(urls))

                    if not urls:
                        pedidos_sem_xml.append(pedido)
                        continue

                    baixou = False
                    for url in urls:
                        nome = url.split("/")[-1]
                        dest = PASTA_XMLS / nome
                        if dest.exists():
                            todos_arquivos.append(dest)
                            baixou = True
                            continue
                        caminho = baixar_arquivo(page, url, nome)
                        if caminho:
                            todos_arquivos.append(caminho)
                            baixou = True

                    if baixou:
                        docnames_ok.add(docname)
                    else:
                        pedidos_sem_xml.append(pedido)

                except Exception as e:
                    print(f"   ❌  Erro em {docname}: {e}")
                    pedidos_sem_xml.append(pedido)
                    continue

            print(f"\n📊  Arquivos baixados: {len(todos_arquivos)}")

            if not todos_arquivos:
                enviar_notificacao_vazia(
                    "Pedidos encontrados mas nenhum XML/PDF disponível ainda.",
                    pedidos_free=pedidos_free,
                    transportadora=transportadora
                )
                return

            # 6. Cria ZIP
            zip_path = criar_zip(todos_arquivos, transportadora)

            # 7. Envia ZIP por email
            email_ok = enviar_zip_por_email(
                zip_path, transportadora, pedidos_novos,
                todos_arquivos, pedidos_sem_xml=pedidos_sem_xml
            )

            # 8. Se for JAMEF: envia todos os XMLs, busca as etiquetas e sobe no Platinum
            jamef_resultado = None
            if "JAMEF" in transportadora.upper():
                jamef_resultado = jamef_upload_xmls(todos_arquivos, page=page)

            # 9. Notifica no Chat
            drive_link = "📧 Enviado por email" if email_ok else None
            if jamef_resultado and not jamef_resultado.get("pulado"):
                ok_count          = len(jamef_resultado.get("ok", []))
                fail_count        = len(jamef_resultado.get("falha", []))
                etiquetas_ok      = jamef_resultado.get("etiquetas_ok", [])
                etiquetas_falha   = jamef_resultado.get("etiquetas_falha", [])

                resumo_jamef = f"\n📤 *Portal JAMEF:* {ok_count} XML(s) OK, {fail_count} falha(s)"
                resumo_jamef += f"\n🏷️ *Etiquetas:* {len(etiquetas_ok)} OK, {len(etiquetas_falha)} pendente(s)"
                if etiquetas_falha:
                    resumo_jamef += f"\n   ⚠️ " + " | ".join(etiquetas_falha[:15])

                plat_ok    = jamef_resultado.get("platinum_ok", [])
                plat_falha = jamef_resultado.get("platinum_falha", [])
                if jamef_resultado.get("platinum_pulado"):
                    resumo_jamef += "\n📦 *Platinum:* não executado (login/credenciais)"
                elif plat_ok or plat_falha:
                    resumo_jamef += f"\n📦 *Platinum:* {len(plat_ok)} OK, {len(plat_falha)} falha(s)"
                    if plat_falha:
                        resumo_jamef += f"\n   ⚠️ " + " | ".join(plat_falha[:15])

                drive_link = (drive_link or "") + resumo_jamef

            enviar_notificacao(
                pedidos_novos, todos_arquivos, zip_path,
                drive_link=drive_link,
                pedidos_sem_xml=pedidos_sem_xml,
                pedidos_free=pedidos_free,
                transportadora=transportadora
            )

            # 10. Salva histórico — pedidos só entram se o email saiu,
            #     senão a próxima execução tenta de novo.
            docnames_free = {p["docname"] for p in pedidos_free}
            if email_ok:
                salvar_historico(historico, docnames_ok | docnames_free)
                print(f"\n💾  Histórico atualizado.")
            else:
                salvar_historico(historico, docnames_free)
                enviar_notificacao_erro(
                    f"Email NÃO enviado — {len(docnames_ok)} pedido(s) ficam pendentes "
                    f"e serão reenviados na próxima execução.",
                    transportadora
                )
                print(f"\n⚠️  Email falhou — pedidos não marcados como processados.")

            if not is_ci:
                page.wait_for_timeout(5_000)

        except Exception as e:
            print(f"\n💥  Erro inesperado: {e}")
            capturar_screenshot(page, "erro_inesperado")
            try:
                enviar_notificacao_erro(str(e), transportadora)
            except Exception:
                pass
            raise

        finally:
            context.close()
            browser.close()
            print("\n🔒  Browser encerrado.")


if __name__ == "__main__":
    main()
