"""
Script para gerar o token OAuth do Gmail.
Rode UMA VEZ no seu PC para gerar o refresh_token.
"""

from google_auth_oauthlib.flow import InstalledAppFlow
import json

CLIENT_ID     = "786504064573-hocv3ni6ma995fpci6lsct48ec558pto.apps.googleusercontent.com"
CLIENT_SECRET = input("Cole aqui o CLIENT SECRET do Google Cloud: ").strip()

# Configuração do OAuth
client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
    }
}

# Permissão apenas de leitura de emails
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
creds = flow.run_local_server(port=0)

# Salva o refresh_token
resultado = {
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "refresh_token": creds.refresh_token,
    "token_uri": "https://oauth2.googleapis.com/token"
}

print("\n" + "="*60)
print("GMAIL_OAUTH_TOKEN (cole como Secret no GitHub):")
print("="*60)
import base64
token_b64 = base64.b64encode(json.dumps(resultado).encode()).decode()
print(token_b64)
print("="*60)