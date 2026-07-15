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

WEBHOOK_URL = "https://chat.googleapis.com/v1/spaces/AAQAnQfMMEY/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=ea5WZkjgL0OWVDLv2brT5uef-D26Xz_8u8YuTRwu1_Y"

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

# Cache de IDs de subpastas do Drive (evita criar duplicatas)
_drive_folder_cache = {}


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
def carregar_historico() -> set:
    if ARQUIVO_HISTORICO.exists():
        try:
            dados = json.loads(ARQUIVO_HISTORICO.read_text(encoding="utf-8"))
            return set(dados.get("docnames_processados", []))
        except Exception:
            return set()
    return set()


def salvar_historico(docnames: set):
    dados = {
        "ultima_execucao": datetime.now().isoformat(),
        "docnames_processados": sorted(docnames)
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
        page.goto(URL_SALES_INVOICE, wait_until="domcontentloaded", timeout=20_000)
        page.wait_for_timeout(2_000)

        btn_filtros = page.locator("span.button-label", has_text="filter")
        if btn_filtros.count() == 0:
            btn_filtros = page.locator(".filter-button, [data-label='Filter']")
        btn_filtros.first.click(timeout=5_000)
        page.wait_for_timeout(800)

        campo = page.locator("input[data-fieldname='sales_order']")
        campo.wait_for(timeout=TIMEOUT)
        campo.fill(docname)
        page.wait_for_timeout(400)

        page.locator("button.apply-filters").click(timeout=5_000)
        page.wait_for_timeout(2_000)

        resultado = page.locator(".list-row-col .ellipsis a, tbody tr td a").first
        resultado.wait_for(timeout=TIMEOUT)
        resultado.click()
        page.wait_for_timeout(2_000)

        page.wait_for_selector(
            ".attachment-row a[href*='/private/files/'][href$='.xml'], "
            ".attachment-row a[href*='/private/files/'][href$='.pdf']",
            timeout=TIMEOUT
        )

    except PlaywrightTimeout:
        print(f"   ⚠️  XML/PDF não encontrado para {docname} em 8s — pulando.")
        return []
    except Exception as e:
        print(f"   ⚠️  Erro ao buscar {docname}: {e} — pulando.")
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


# ════════════════════════════════════════════════════════════
#  BAIXAR ARQUIVO
# ════════════════════════════════════════════════════════════
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
#  UPLOAD GOOGLE DRIVE
# ════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════
#  ENVIAR ZIP POR EMAIL (Gmail / Google Workspace)
# ════════════════════════════════════════════════════════════
def enviar_zip_por_email(zip_path: Path, transportadora: str, pedidos: list[dict],
                          arquivos: list[Path], pedidos_sem_xml: list[dict] | None = None) -> bool:
    """
    Envia o ZIP com XMLs e PDFs por email para os destinatários configurados.
    Usa SMTP do Gmail/Google Workspace com App Password.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    GMAIL_USUARIO  = os.getenv("GMAIL_USUARIO", "carlos.berbert@zeb.mx")
    GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
    DESTINATARIOS  = [
        GMAIL_USUARIO,
        "felipe.azevedo@zeb.mx",
        "israel.lopes@zeb.mx",
    ]

    if not GMAIL_PASSWORD:
        print("   ⚠️  GMAIL_APP_PASSWORD não configurado — pulando envio de email.")
        return False

    agora     = datetime.now().strftime("%d/%m/%Y %H:%M")
    data_hoje = datetime.now().strftime("%d/%m/%Y")
    pedidos_sem_xml = pedidos_sem_xml or []
    pedidos_ok = [p for p in pedidos if p["docname"] not in {x["docname"] for x in pedidos_sem_xml}]

    # ── Monta o corpo do email ───────────────────────────────
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
        print(f"\n📧  Enviando email para {len(DESTINATARIOS)} destinatário(s)...")

        msg = MIMEMultipart()
        msg["From"]    = GMAIL_USUARIO
        msg["To"]      = ", ".join(DESTINATARIOS)
        msg["Subject"] = f"[Bot XML] {transportadora} — {data_hoje} — {len(pedidos_ok)} pedido(s)"

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
            servidor.sendmail(GMAIL_USUARIO, DESTINATARIOS, msg.as_string())

        print(f"   ✅  Email enviado para: {', '.join(DESTINATARIOS)}")
        return True

    except Exception as e:
        print(f"   ❌  Erro ao enviar email: {e}")
        return False


def _garantir_pasta_drive(service, transportadora: str) -> str:
    global _drive_folder_cache
    if transportadora in _drive_folder_cache:
        return _drive_folder_cache[transportadora]

    nome_pasta = transportadora.upper().replace(" ", "_")
    query = (
        f"name='{nome_pasta}' and "
        f"'{DRIVE_FOLDER_ID}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"trashed=false"
    )
    resultado = service.files().list(q=query, fields="files(id)").execute()
    arquivos  = resultado.get("files", [])

    if arquivos:
        folder_id = arquivos[0]["id"]
    else:
        meta = {
            "name": nome_pasta,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [DRIVE_FOLDER_ID]
        }
        pasta = service.files().create(body=meta, fields="id").execute()
        folder_id = pasta["id"]
        print(f"   📁  Subpasta criada no Drive: {nome_pasta}")

    _drive_folder_cache[transportadora] = folder_id
    return folder_id


# ════════════════════════════════════════════════════════════
#  NOTIFICAÇÕES — Google Chat
# ════════════════════════════════════════════════════════════
def _enviar_mensagem_chat(mensagem: str):
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
        linhas.append(f"📁 *Drive:* {drive_link}")

    if pedidos_free:
        linhas.append(f"\n🎁 *FREE- ignorados:* {len(pedidos_free)}")

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
        linhas.append(f"\n🎁 FREE- ignorados: {len(pedidos_free)}")
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

def jamef_login(email: str, senha: str) -> str | None:
    """
    Faz login no portal JAMEF via AWS Cognito.
    Retorna o idToken para uso nas chamadas da API.
    """
    import urllib.request
    import json

    print("\n🔐  Fazendo login no portal JAMEF...")

    payload = json.dumps({
        "AuthFlow": "USER_PASSWORD_AUTH",
        "ClientId": JAMEF_CLIENT_ID,
        "AuthParameters": {
            "USERNAME": email,
            "PASSWORD": senha
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        JAMEF_COGNITO_URL,
        data=payload,
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            id_token = data["AuthenticationResult"]["IdToken"]
            print("   ✅  Login JAMEF OK!")
            return id_token
    except Exception as e:
        print(f"   ❌  Erro no login JAMEF: {e}")
        return None


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
    Retorna dict com status do envio.
    """
    import urllib.request
    import json

    nome = xml_path.name

    try:
        # Lê e converte o XML para base64
        xml_bytes  = xml_path.read_bytes()
        xml_base64 = base64.b64encode(xml_bytes).decode("utf-8")

        # Extrai filial do XML
        filial = jamef_extrair_filial(xml_path)

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
                "Authorization": f"Bearer {id_token}",
                "Origin": JAMEF_URL_BASE,
                "Referer": f"{JAMEF_URL_BASE}/etiquetas"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            body   = resp.read().decode("utf-8")
            if status in (200, 201):
                print(f"   ✅  {nome} enviado com sucesso!")
                return {"arquivo": nome, "ok": True, "status": status}
            else:
                print(f"   ⚠️  {nome}: resposta {status}")
                return {"arquivo": nome, "ok": False, "status": status, "erro": body}

    except urllib.error.HTTPError as e:
        erro = e.read().decode("utf-8") if e.fp else str(e)
        print(f"   ❌  {nome}: HTTP {e.code} — {erro[:100]}")
        return {"arquivo": nome, "ok": False, "status": e.code, "erro": erro}
    except Exception as e:
        print(f"   ❌  {nome}: {e}")
        return {"arquivo": nome, "ok": False, "erro": str(e)}


def jamef_upload_xmls(xmls: list[Path]) -> dict:
    """
    Faz login no portal JAMEF e envia todos os XMLs.
    Retorna resumo com sucesso e falhas.
    """
    email = os.getenv("JAMEF_EMAIL", "carlos.berbert@zeb.mx")
    senha = os.getenv("JAMEF_SENHA", "")

    if not senha:
        print("   ⚠️  JAMEF_SENHA não configurada — pulando upload JAMEF.")
        return {"ok": [], "falha": [], "pulado": True}

    # Filtra só XMLs (não PDFs)
    apenas_xmls = [f for f in xmls if f.suffix.lower() == ".xml"]

    if not apenas_xmls:
        print("   ⚠️  Nenhum XML para enviar ao portal JAMEF.")
        return {"ok": [], "falha": []}

    print(f"\n📤  Enviando {len(apenas_xmls)} XML(s) para o portal JAMEF...")

    # Login
    id_token = jamef_login(email, senha)
    if not id_token:
        return {"ok": [], "falha": [f.name for f in apenas_xmls]}

    # Envia cada XML
    resultados_ok    = []
    resultados_falha = []

    for xml_path in apenas_xmls:
        resultado = jamef_enviar_xml(xml_path, id_token)
        if resultado["ok"]:
            resultados_ok.append(resultado["arquivo"])
        else:
            resultados_falha.append(resultado["arquivo"])

    print(f"\n   📊  JAMEF Portal: {len(resultados_ok)} enviado(s), {len(resultados_falha)} falha(s)")
    return {"ok": resultados_ok, "falha": resultados_falha}


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
                salvar_historico(historico | {p["docname"] for p in pedidos_free})
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

            # 8. Se for JAMEF, sobe os XMLs no portal deles
            jamef_resultado = None
            if "JAMEF" in transportadora.upper():
                jamef_resultado = jamef_upload_xmls(todos_arquivos)

            # 9. Notifica no Chat
            drive_link = "📧 Enviado por email" if email_ok else None
            if jamef_resultado and not jamef_resultado.get("pulado"):
                ok_count   = len(jamef_resultado.get("ok", []))
                fail_count = len(jamef_resultado.get("falha", []))
                drive_link = (drive_link or "") + f"\n📤 Portal JAMEF: {ok_count} OK, {fail_count} falha(s)"

            enviar_notificacao(
                pedidos_novos, todos_arquivos, zip_path,
                drive_link=drive_link,
                pedidos_sem_xml=pedidos_sem_xml,
                pedidos_free=pedidos_free,
                transportadora=transportadora
            )

            # 9. Salva histórico
            docnames_free = {p["docname"] for p in pedidos_free}
            salvar_historico(historico | docnames_ok | docnames_free)
            print(f"\n💾  Histórico atualizado.")

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
