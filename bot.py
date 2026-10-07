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
#  UPLOAD GOOGLE DRIVE
# ════════════════════════════════════════════════════════════
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
        destinatarios_para = ["Adm.operacional@fitlogistica.com.br"]
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
#  PORTAL JAMEF — tudo via browser (evita bloqueio Akamai 403)
#  Todas as chamadas de API são feitas com fetch() DE DENTRO da
#  página do Chrome, levando os cookies de verificação do Akamai.
# ════════════════════════════════════════════════════════════

def _extrair_corpo_email(payload: dict) -> str:
    """Extrai o texto do corpo do email recursivamente."""
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


def _gmail_service():
    """Cria o serviço do Gmail usando o GMAIL_OAUTH_TOKEN."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    oauth_raw = os.getenv("GMAIL_OAUTH_TOKEN", "")
    if not oauth_raw:
        print("   ⚠️  GMAIL_OAUTH_TOKEN não configurado.")
        return None
    oauth_data = json.loads(base64.b64decode(oauth_raw).decode("utf-8"))
    creds = Credentials(
        token=None,
        refresh_token=oauth_data["refresh_token"],
        token_uri=oauth_data["token_uri"],
        client_id=oauth_data["client_id"],
        client_secret=oauth_data["client_secret"],
        scopes=["https://www.googleapis.com/auth/gmail.readonly"]
    )
    return build("gmail", "v1", credentials=creds)


GMAIL_QUERY_MFA = 'from:naoresponda@jamef.com.br subject:"Portal Cliente Jamef"'


def gmail_ids_mfa_existentes(service) -> set:
    """Marca os emails de MFA que já existem (para ignorar códigos antigos)."""
    try:
        res = service.users().messages().list(userId="me", q=GMAIL_QUERY_MFA, maxResults=20).execute()
        return {m["id"] for m in res.get("messages", [])}
    except Exception as e:
        print(f"   ⚠️  Não consegui listar emails antigos: {e}")
        return set()


def gmail_aguardar_codigo_mfa(service, ids_vistos: set, timeout_seg: int = 120) -> str | None:
    """Aguarda chegar um email NOVO da JAMEF e extrai o código de 6 dígitos."""
    import re
    import time

    print(f"\n📬  Aguardando código MFA no Gmail (até {timeout_seg}s)...")
    inicio = time.time()
    while time.time() - inicio < timeout_seg:
        try:
            res = service.users().messages().list(userId="me", q=GMAIL_QUERY_MFA, maxResults=5).execute()
            for msg in res.get("messages", []):
                if msg["id"] in ids_vistos:
                    continue
                dados = service.users().messages().get(userId="me", id=msg["id"], format="full").execute()
                corpo = _extrair_corpo_email(dados.get("payload", {}))
                codigos = re.findall(r"\b(\d{6})\b", corpo)
                if codigos:
                    print(f"   ✅  Código MFA encontrado: {codigos[0]}")
                    return codigos[0]
        except Exception as e:
            print(f"   ⚠️  Erro lendo Gmail: {e}")
        print(f"   ⏳  Aguardando email... ({int(time.time() - inicio)}s)")
        time.sleep(5)
    print("   ❌  Timeout — código MFA não chegou no Gmail.")
    return None


def jamef_fetch(page, path: str, method: str = "GET", body=None, binario: bool = False) -> dict:
    """
    Faz uma chamada à API da JAMEF com fetch() de dentro da página.
    Assim a requisição sai do próprio Chrome, com os cookies do Akamai e da sessão.
    """
    return page.evaluate(
        """async ({url, method, body, binario}) => {
            const opts = {method, credentials: 'include',
                          headers: {'Accept': 'application/json, text/plain, */*'}};
            if (body !== null) {
                opts.headers['Content-Type'] = 'application/json';
                opts.body = JSON.stringify(body);
            }
            const r = await fetch(url, opts);
            let data;
            if (binario) {
                const buf = new Uint8Array(await r.arrayBuffer());
                let s = '';
                for (let i = 0; i < buf.length; i += 0x8000)
                    s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
                data = btoa(s);
            } else {
                data = await r.text();
            }
            return {status: r.status, ok: r.ok, data,
                    contentType: r.headers.get('content-type') || ''};
        }""",
        {"url": JAMEF_URL_BASE + path, "method": method, "body": body, "binario": binario},
    )


def _bloqueado_akamai(texto: str) -> bool:
    return "Access Denied" in (texto or "") and "permission to access" in (texto or "")


def jamef_login(email: str, senha: str, page) -> bool:
    """
    Login no portal JAMEF pelo próprio browser:
    1. Abre /login (o Akamai valida o browser e grava os cookies dele)
    2. fetch POST /api/auth/login  → JAMEF manda o código por email
    3. Lê o código no Gmail
    4. fetch POST /api/auth/confirm-mfa → sessão fica gravada nos cookies do browser
    """
    print("\n🔐  Fazendo login no portal JAMEF (via browser)...")

    try:
        page.goto(f"{JAMEF_URL_BASE}/login", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(4_000)  # tempo para o script do Akamai rodar

        if _bloqueado_akamai(page.content()):
            print("   ❌  A JAMEF bloqueou o acesso a esta máquina/IP (Akamai) já na página de login.")
            capturar_screenshot(page, "jamef_bloqueado_login")
            return False

        # Gmail: marca emails antigos ANTES de pedir o código novo
        gmail = _gmail_service()
        if gmail is None:
            return False
        ids_vistos = gmail_ids_mfa_existentes(gmail)
        print(f"   ℹ️  {len(ids_vistos)} email(s) de MFA antigo(s) serão ignorados.")

        # Passo 1 — login
        r = jamef_fetch(page, "/api/auth/login", "POST", {"email": email, "password": senha})
        if _bloqueado_akamai(r["data"]):
            print(f"   ❌  Akamai bloqueou /api/auth/login (HTTP {r['status']}).")
            capturar_screenshot(page, "jamef_bloqueado_api")
            return False
        if not r["ok"]:
            print(f"   ❌  Login JAMEF HTTP {r['status']}: {r['data'][:200]}")
            return False

        dados = json.loads(r["data"] or "{}")
        print(f"   ℹ️  Challenge: {dados.get('challengeName')} | {dados.get('message', '')}")

        if dados.get("challengeName") != "EMAIL_MFA":
            print("   ✅  Login JAMEF OK (sem MFA).")
            return True

        # Passo 2 — código do Gmail
        codigo = gmail_aguardar_codigo_mfa(gmail, ids_vistos)
        if not codigo:
            return False

        # Passo 3 — confirma MFA (cookies de sessão ficam no browser)
        r2 = jamef_fetch(page, "/api/auth/confirm-mfa", "POST", {
            "challengeName": "EMAIL_MFA",
            "email": email,
            "mfaCode": codigo,
            "session": dados.get("session"),
        })
        if not r2["ok"]:
            print(f"   ❌  confirm-mfa HTTP {r2['status']}: {r2['data'][:200]}")
            return False

        nomes_cookies = [c["name"] for c in page.context.cookies(JAMEF_URL_BASE)]
        if "idToken" in nomes_cookies or "accessToken" in nomes_cookies:
            print("   ✅  Login JAMEF com MFA OK!")
            return True

        print(f"   ⚠️  MFA respondeu {r2['data'][:100]}, mas não achei o token nos cookies: {nomes_cookies}")
        return False

    except Exception as e:
        print(f"   ❌  Erro no login JAMEF: {e}")
        capturar_screenshot(page, "jamef_erro_login")
        return False


def jamef_extrair_dados_xml(xml_path: Path) -> dict:
    """Extrai chave de acesso (44 dígitos) e número da NF do XML."""
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(str(xml_path)).getroot()

        def sem_ns(tag):
            return tag.split("}")[-1] if "}" in tag else tag

        chave, n_nf = None, None
        for el in root.iter():
            nome = sem_ns(el.tag)
            if nome == "infNFe" and not chave:
                id_attr = el.get("Id", "")
                if id_attr.startswith("NFe"):
                    chave = id_attr[3:]
            elif nome == "chNFe" and not chave:
                chave = el.text
            elif nome == "nNF" and not n_nf:
                n_nf = el.text
        return {"chave": chave, "nNF": n_nf, "filial": "57"}
    except Exception as e:
        print(f"   ⚠️  Erro ao ler XML {xml_path.name}: {e}")
        return {"chave": None, "nNF": None, "filial": "57"}


def jamef_enviar_xml(page, xml_path: Path) -> dict:
    """Envia um XML para /api/label/send-note de dentro do browser."""
    dados = jamef_extrair_dados_xml(xml_path)
    n_nf = dados["nNF"]
    try:
        xml_b64 = base64.b64encode(xml_path.read_bytes()).decode("utf-8")
        r = jamef_fetch(page, "/api/label/send-note", "POST", {
            "cgc": JAMEF_CGC,
            "filialOrigem": dados["filial"],
            "xmlBase64": xml_b64,
        })
        if r["ok"]:
            print(f"   ✅  NF {n_nf} enviada à JAMEF.")
            return {"arquivo": xml_path.name, "ok": True, "nNF": n_nf, "chave": dados["chave"]}
        motivo = "bloqueio Akamai" if _bloqueado_akamai(r["data"]) else r["data"][:120]
        print(f"   ❌  NF {n_nf}: HTTP {r['status']} — {motivo}")
        return {"arquivo": xml_path.name, "ok": False, "nNF": n_nf, "erro": motivo}
    except Exception as e:
        print(f"   ❌  NF {n_nf}: {e}")
        return {"arquivo": xml_path.name, "ok": False, "nNF": n_nf, "erro": str(e)}


def _jamef_linha_da_nf(page, n_nf: str):
    """Retorna a linha (tr) da tabela de etiquetas cuja célula é exatamente o número da NF."""
    for linha in page.locator("table tbody tr").all():
        celulas = [c.strip() for c in linha.locator("td").all_inner_texts()]
        if str(n_nf) in celulas:
            return linha, " ".join(celulas)
    return None, ""


def jamef_ler_status(page, nfs: list[str]) -> dict:
    """Abre /etiquetas uma vez e devolve {nf: 'sucesso' | 'ja_cadastrada' | 'outro' | None}."""
    page.goto(f"{JAMEF_URL_BASE}/etiquetas", wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(3_000)
    resultado = {}
    for nf in nfs:
        _, texto = _jamef_linha_da_nf(page, nf)
        t = texto.lower()
        if not texto:
            resultado[nf] = None
        elif "sucesso" in t:
            resultado[nf] = "sucesso"
        elif "cadastrada" in t:
            resultado[nf] = "ja_cadastrada"
        else:
            resultado[nf] = "outro"
    return resultado


def jamef_baixar_etiqueta(page, n_nf: str) -> Path | None:
    """
    Clica no botão de imprimir da NF. A JAMEF gera o PDF no próprio browser
    e abre num link blob: — capturamos esse link e salvamos o PDF.
    A página /etiquetas já precisa estar aberta.
    """
    caminho = PASTA_XMLS / f"etiqueta_JAMEF_NF{n_nf}.pdf"
    if caminho.exists() and caminho.stat().st_size > 1000:
        return caminho

    linha, _ = _jamef_linha_da_nf(page, n_nf)
    if linha is None:
        print(f"   ⚠️  NF {n_nf} não está na tabela de etiquetas.")
        return None

    try:
        # Intercepta o window.open para capturar o link do PDF sem abrir aba nova
        page.evaluate("""() => {
            window.__etiquetas = [];
            if (!window.__openOriginal) window.__openOriginal = window.open;
            window.open = function(u) { window.__etiquetas.push(String(u)); return null; };
        }""")

        linha.locator("button").last.click()

        url_pdf = None
        for _ in range(60):  # até 30s (a geração leva ~5s)
            capturados = page.evaluate("() => window.__etiquetas || []")
            if capturados:
                url_pdf = capturados[-1]
                break
            page.wait_for_timeout(500)

        if not url_pdf:
            print(f"   ⚠️  NF {n_nf}: a etiqueta não foi gerada em 30s.")
            return None

        b64 = page.evaluate("""async (u) => {
            const r = await fetch(u);
            const buf = new Uint8Array(await r.arrayBuffer());
            let s = '';
            for (let i = 0; i < buf.length; i += 0x8000)
                s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
            return btoa(s);
        }""", url_pdf)

        conteudo = base64.b64decode(b64)
        if conteudo[:4] != b"%PDF":
            print(f"   ⚠️  NF {n_nf}: o arquivo capturado não é um PDF.")
            return None

        caminho.write_bytes(conteudo)
        print(f"   🏷️  Etiqueta NF {n_nf} salva ({len(conteudo)//1024} KB).")
        return caminho

    except Exception as e:
        print(f"   ❌  Etiqueta NF {n_nf}: {e}")
        return None


def jamef_upload_xmls(xmls: list[Path], page=None) -> dict:
    """
    1. Login no portal JAMEF (browser + MFA pelo Gmail)
    2. Envia TODOS os XMLs
    3. Aguarda a JAMEF processar e lê o status de todas de uma vez
    4. Baixa as etiquetas com status Sucesso ou Já Cadastrada
    """
    import time

    email = os.getenv("JAMEF_EMAIL", "carlos.berbert@zeb.mx")
    senha = os.getenv("JAMEF_SENHA", "")
    if not senha:
        print("   ⚠️  JAMEF_SENHA não configurada — pulando portal JAMEF.")
        return {"ok": [], "falha": [], "pulado": True}
    if page is None:
        print("   ⚠️  Browser não disponível — pulando portal JAMEF.")
        return {"ok": [], "falha": [], "pulado": True}

    apenas_xmls = [f for f in xmls if f.suffix.lower() == ".xml"]
    if not apenas_xmls:
        print("   ⚠️  Nenhum XML para enviar ao portal JAMEF.")
        return {"ok": [], "falha": []}

    if not jamef_login(email, senha, page):
        return {"ok": [], "falha": [f.name for f in apenas_xmls], "erro_login": True}

    # 1) Envia todos os XMLs
    print(f"\n📤  Enviando {len(apenas_xmls)} XML(s) para o portal JAMEF...")
    enviados, falhas = [], []
    for xml_path in apenas_xmls:
        res = jamef_enviar_xml(page, xml_path)
        (enviados if res["ok"] else falhas).append(res)
        page.wait_for_timeout(300)

    nfs_enviadas = [r["nNF"] for r in enviados if r.get("nNF")]
    status_final = {}

    # 2) Aguarda processamento e lê status de todas de uma vez (até ~3 min)
    if nfs_enviadas:
        print(f"\n⏳  Aguardando a JAMEF processar {len(nfs_enviadas)} etiqueta(s)...")
        pendentes = list(nfs_enviadas)
        for rodada in range(1, 9):
            time.sleep(20)
            lidos = jamef_ler_status(page, pendentes)
            for nf, st in lidos.items():
                if st in ("sucesso", "ja_cadastrada"):
                    status_final[nf] = st
            pendentes = [nf for nf in pendentes if nf not in status_final]
            print(f"   🔄  Rodada {rodada}: {len(status_final)} pronta(s), {len(pendentes)} pendente(s).")
            if not pendentes:
                break

    # 3) Baixa as etiquetas prontas (página /etiquetas já está aberta)
    etiquetas_ok, etiquetas_falha = [], []
    for nf in nfs_enviadas:
        if nf not in status_final:
            etiquetas_falha.append(f"NF {nf} (não processada)")
            continue
        if jamef_baixar_etiqueta(page, nf):
            etiquetas_ok.append(f"NF {nf}")
        else:
            etiquetas_falha.append(f"NF {nf} (erro no download)")

    print(f"\n   📊  JAMEF: {len(enviados)} XML(s) enviado(s), {len(falhas)} falha(s)")
    print(f"   🏷️  Etiquetas: {len(etiquetas_ok)} OK, {len(etiquetas_falha)} falha(s)")
    return {
        "ok": [r["arquivo"] for r in enviados],
        "falha": [f"NF {r.get('nNF')} — {r.get('erro', '')}" for r in falhas],
        "etiquetas_ok": etiquetas_ok,
        "etiquetas_falha": etiquetas_falha,
    }


def main():
    email, senha = obter_credenciais()
    transportadora = obter_transportadora()

    with sync_playwright() as p:
        is_ci = os.getenv("CI", "false").lower() == "true"
        headless = os.getenv("HEADLESS", "false").lower() == "true"
        browser = p.chromium.launch(
            headless=headless,
            slow_mo=0 if is_ci else 300,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
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

            # 8. Se for JAMEF, sobe os XMLs no portal + baixa etiquetas + Platinum OMS
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

                if jamef_resultado.get("erro_login"):
                    resumo_jamef = "\n❌ *Portal JAMEF:* falha no login (ver log do Actions)"
                else:
                    resumo_jamef = f"\n📤 *Portal JAMEF:* {ok_count} XML(s) OK, {fail_count} falha(s)"
                resumo_jamef += f"\n🏷️ *Etiquetas JAMEF:* {len(etiquetas_ok)} OK, {len(etiquetas_falha)} falha(s)"

                if etiquetas_ok:
                    resumo_jamef += f"\n   ✅ " + " | ".join(etiquetas_ok[:10])
                if etiquetas_falha:
                    resumo_jamef += f"\n   ⚠️ " + " | ".join(etiquetas_falha[:10])

                drive_link = (drive_link or "") + resumo_jamef

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
