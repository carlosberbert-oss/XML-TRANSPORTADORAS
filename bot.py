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

# Google Drive — pasta raiz "XMLs Transportadoras"
DRIVE_FOLDER_ID = "1-vo-SyRYeKkmtsi7x2Hpx3zLtVkZJ-KF"

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
def upload_para_drive(zip_path: Path, transportadora: str) -> str | None:
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.service_account import Credentials

        creds_raw = os.getenv("GOOGLE_CREDENTIALS", "")
        if not creds_raw:
            print("   ⚠️  GOOGLE_CREDENTIALS não configurado — pulando upload.")
            return None

        creds_json = json.loads(base64.b64decode(creds_raw).decode("utf-8"))
        creds = Credentials.from_service_account_info(
            creds_json,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        service = build("drive", "v3", credentials=creds)

        # Garante subpasta da transportadora
        folder_id = _garantir_pasta_drive(service, transportadora)

        # Upload
        file_metadata = {"name": zip_path.name, "parents": [folder_id]}
        media = MediaFileUpload(str(zip_path), mimetype="application/zip")
        arquivo = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink"
        ).execute()

        link = arquivo.get("webViewLink", "")
        print(f"   ✅  Upload no Drive concluído!")
        print(f"   🔗  {link}")
        return link

    except Exception as e:
        print(f"   ❌  Erro no upload Drive: {e}")
        return None


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

            # 7. Upload no Google Drive
            drive_link = upload_para_drive(zip_path, transportadora)

            # 8. Notifica no Chat
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
