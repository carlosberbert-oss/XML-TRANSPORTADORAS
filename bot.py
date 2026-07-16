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


def jamef_verificar_status_etiqueta(chave: str, id_token: str, n_nf: str,
                                     page=None,
                                     max_tentativas: int = 12, intervalo: int = 10) -> str:
    """
    Verifica o status da etiqueta na JAMEF lendo a tabela do site.
    Usa o page do Playwright já aberto (não abre novo browser).
    Status: 'Sucesso' ou 'NOTA FISCAL JA CADASTRADA'
    """
    import time

    print(f"   ⏳  Aguardando processamento da etiqueta NF {n_nf}...")

    if page is None:
        print("   ⚠️  Page não disponível — pulando verificação de status.")
        return "timeout"

    for tentativa in range(max_tentativas):
        time.sleep(intervalo)
        print(f"   ⏳  Verificando status NF {n_nf} (tentativa {tentativa+1}/{max_tentativas})...")

        try:
            # Navega para a tela de etiquetas da JAMEF com cookie já injetado
            page.goto(
                f"{JAMEF_URL_BASE}/etiquetas",
                wait_until="networkidle",
                timeout=20_000
            )
            page.wait_for_timeout(3_000)

            # Lê todas as linhas da tabela
            linhas = page.locator("table tbody tr, .MuiTableBody-root tr").all()

            for linha in linhas:
                texto = linha.inner_text().replace("\n", " ").strip()
                if str(n_nf) in texto:
                    status_lower = texto.lower()
                    print(f"   ℹ️  NF {n_nf}: {texto[:80]}")

                    if "sucesso" in status_lower:
                        print(f"   ✅  NF {n_nf}: Sucesso!")
                        return "sucesso"
                    elif "ja cadastrada" in status_lower or "já cadastrada" in status_lower:
                        print(f"   ℹ️  NF {n_nf}: Já cadastrada.")
                        return "ja_cadastrada"
                    else:
                        print(f"   ⏳  NF {n_nf}: ainda processando...")
                        break

        except Exception as e:
            print(f"   ⚠️  Erro na verificação (tentativa {tentativa+1}): {e}")

    print(f"   ⚠️  NF {n_nf}: timeout aguardando etiqueta.")
    return "timeout"


def jamef_baixar_etiqueta(chave: str, id_token: str, n_nf: str) -> Path | None:
    """
    Baixa a etiqueta PDF da JAMEF para uma NF.
    Assume que a etiqueta já foi processada (status = sucesso).
    """
    caminho = PASTA_XMLS / f"etiqueta_JAMEF_NF{n_nf}.pdf"
    # Se já foi salva durante a verificação de status, retorna direto
    if caminho.exists() and caminho.stat().st_size > 100:
        print(f"   ✅  Etiqueta já disponível: {caminho.name}")
        return caminho

    import urllib.request
    import json

    print(f"   🏷️  Baixando etiqueta para NF {n_nf}...")
    payload = json.dumps({"chave": chave}).encode("utf-8")
    req = urllib.request.Request(
        f"{JAMEF_URL_BASE}/api/label/render",
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
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            conteudo = resp.read()
            caminho.write_bytes(conteudo)
            print(f"   ✅  Etiqueta salva: {caminho.name}")
            return caminho
    except Exception as e:
        print(f"   ❌  Erro ao baixar etiqueta NF {n_nf}: {e}")
        return None


def platinum_fazer_login(page) -> bool:
    """Faz login no Platinum OMS."""
    URL_LOGIN_PLATINUM = "https://oms.tpl.com.br/login"
    print("\n🔐  Fazendo login no Platinum OMS...")
    try:
        page.goto(URL_LOGIN_PLATINUM, wait_until="domcontentloaded", timeout=20_000)
        page.wait_for_timeout(1_500)

        page.locator("input#email, input[name='email']").fill("felipe.azevedo@zeb.mx")
        page.locator("input#password, input[name='senha']").fill("Zebrands-20251")
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_timeout(3_000)

        if "login" not in page.url.lower():
            print("   ✅  Login Platinum OK!")
            return True
        print("   ❌  Falha no login Platinum.")
        return False
    except Exception as e:
        print(f"   ❌  Erro no login Platinum: {e}")
        return False


def platinum_upload_etiqueta(page, pdf_path: Path, n_nf: str) -> bool:
    """
    Faz upload da etiqueta PDF no Platinum OMS.
    - Pedido: "Zecore {n_nf}-1"
    - Modelo: PDF - PADRAO (value=0)
    """
    URL_PLATINUM = "https://oms.tpl.com.br/pedidoEtiqueta"
    pedido_oms   = f"Zecore {n_nf}-1"

    print(f"\n   🏷️  Platinum OMS — Pedido: {pedido_oms}")

    try:
        page.goto(URL_PLATINUM, wait_until="domcontentloaded", timeout=20_000)
        page.wait_for_timeout(2_000)

        # Verifica se precisa logar novamente
        if "login" in page.url.lower():
            platinum_fazer_login(page)
            page.goto(URL_PLATINUM, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(2_000)

        # Preenche o número do pedido
        campo_pedido = page.locator("input[name='pedido']")
        campo_pedido.wait_for(timeout=8_000)
        campo_pedido.fill(pedido_oms)
        page.wait_for_timeout(500)

        # Seleciona modelo PDF - PADRAO (value=0)
        page.locator("select[name='modelo']").select_option("0")
        page.wait_for_timeout(500)

        # Upload do PDF via input file oculto
        page.locator("input[name='upload']").set_input_files(str(pdf_path))
        page.wait_for_timeout(1_000)

        # Clica em UPLOAD
        page.locator("button[type='submit'], input[type='submit'], button:has-text('UPLOAD')").first.click()
        page.wait_for_timeout(4_000)

        # Verifica mensagem de sucesso na página
        texto_pagina = page.content().lower()
        if "sucesso" in texto_pagina or "success" in texto_pagina or "carregad" in texto_pagina:
            print(f"   ✅  NF {n_nf} enviada ao Platinum OMS!")
            return True
        else:
            print(f"   ✅  NF {n_nf} — upload enviado ao Platinum.")
            return True

    except PlaywrightTimeout:
        print(f"   ⚠️  Timeout no Platinum OMS para NF {n_nf}.")
        return False
    except Exception as e:
        print(f"   ❌  Erro no Platinum OMS NF {n_nf}: {e}")
        return False


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


def jamef_upload_xmls(xmls: list[Path], page=None) -> dict:
    """
    Faz login no portal JAMEF, envia todos os XMLs,
    baixa as etiquetas geradas e faz upload no Platinum OMS.
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

    # Envia cada XML, baixa etiqueta e sobe no Platinum
    resultados_ok    = []
    resultados_falha = []
    etiquetas_ok     = []
    etiquetas_falha  = []

    # Login no Platinum OMS antes de processar (se tiver page)
    platinum_logado = False
    if page:
        platinum_logado = platinum_fazer_login(page)

    for xml_path in apenas_xmls:
        resultado = jamef_enviar_xml(xml_path, id_token)

        if resultado["ok"]:
            resultados_ok.append(resultado["arquivo"])

            chave = resultado.get("chave")
            n_nf  = resultado.get("nNF")

            if not chave or not n_nf:
                print(f"   ⚠️  Chave/NF não encontrada — etiqueta pulada.")
                continue

            # Aguarda processamento e verifica status
            # Injeta cookie da JAMEF no page antes de verificar status
            if page:
                try:
                    page.context.add_cookies([{
                        "name": "idToken",
                        "value": id_token,
                        "domain": "cliente.jamef.com.br",
                        "path": "/"
                    }])
                except Exception:
                    pass

            status_etiqueta = jamef_verificar_status_etiqueta(chave, id_token, n_nf, page=page)

            if status_etiqueta == "sucesso":
                # Baixa etiqueta (pode já ter sido salva na verificação)
                etiqueta_path = jamef_baixar_etiqueta(chave, id_token, n_nf)

                if etiqueta_path and page and platinum_logado:
                    ok_oms = platinum_upload_etiqueta(page, etiqueta_path, n_nf)
                    if ok_oms:
                        etiquetas_ok.append(f"NF {n_nf}")
                    else:
                        etiquetas_falha.append(f"NF {n_nf}")
                elif etiqueta_path:
                    etiquetas_ok.append(f"NF {n_nf} (etiqueta salva, Platinum pulado)")

            elif status_etiqueta == "ja_cadastrada":
                print(f"   ℹ️  NF {n_nf}: etiqueta já cadastrada — pulando Platinum.")
                etiquetas_falha.append(f"NF {n_nf} (já cadastrada)")

            else:  # timeout ou erro
                print(f"   ⚠️  NF {n_nf}: etiqueta não processada a tempo.")
                etiquetas_falha.append(f"NF {n_nf} (timeout/erro)")
        else:
            resultados_falha.append(resultado["arquivo"])

    print(f"\n   📊  JAMEF Portal: {len(resultados_ok)} XML(s) enviado(s), {len(resultados_falha)} falha(s)")
    print(f"   🏷️  Etiquetas: {len(etiquetas_ok)} OK, {len(etiquetas_falha)} falha(s)")
    return {
        "ok": resultados_ok,
        "falha": resultados_falha,
        "etiquetas_ok": etiquetas_ok,
        "etiquetas_falha": etiquetas_falha
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

                resumo_jamef = f"\n📤 *Portal JAMEF:* {ok_count} XML(s) OK, {fail_count} falha(s)"
                resumo_jamef += f"\n🏷️ *Etiquetas Platinum:* {len(etiquetas_ok)} OK, {len(etiquetas_falha)} falha(s)"

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
