# main.py
import os
from fastapi import FastAPI, Request, HTTPException
from google.oauth2 import service_account
from googleapiclient.discovery import build
import httpx  # Usar httpx em vez de requests para chamadas async
from dotenv import load_dotenv # Para carregar variáveis de ambiente
from fastapi.concurrency import run_in_threadpool # Para rodar código síncrono

# Carrega as variáveis do arquivo .env
load_dotenv()

app = FastAPI()

# === CONFIGURAÇÕES (Carregadas do ambiente) ===
BOTPRESS_BOT_ID = os.getenv("BOTPRESS_BOT_ID")
BOTPRESS_WEBHOOK_TOKEN = os.getenv("BOTPRESS_WEBHOOK_TOKEN")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")

# Validação das configurações
if not all([BOTPRESS_BOT_ID, BOTPRESS_WEBHOOK_TOKEN, SERVICE_ACCOUNT_FILE]):
    raise Exception("Erro: Variáveis de ambiente não configuradas corretamente.")

SCOPES = ["https://www.googleapis.com/auth/chat.bot"]

# === CREDENCIAL GOOGLE CHAT API ===
# Esta parte continua síncrona, pois a biblioteca do Google não é async
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
chat_service = build("chat", "v1", credentials=credentials)


# === ROTAS ===

@app.post("/chat/message")
async def on_message(req: Request):
    body = await req.json()
    print("Recebido do Google Chat:", body) # Ótimo para depuração

    # 1. Prevenção de Loop: Ignorar mensagens do próprio bot
    if body.get("message", {}).get("sender", {}).get("type") == "BOT":
        return {"status": "ignorado, mensagem de bot"}

    # 2. Extrair informações essenciais do Google Chat
    user_message = body.get("message", {}).get("text", "")
    space_name = body.get("space", {}).get("name") # Ex: "spaces/AAAAAAAAAAA"
    
    # CRÍTICO: Usar o ID do espaço como ID da conversa para o Botpress
    # Isso garante que cada sala ou DM tenha seu próprio estado de conversa
    conversation_id = space_name.split('/')[-1]

    if not user_message or not space_name:
        # Não processar eventos sem mensagem ou espaço (ex: adições de membros)
        return {"status": "evento ignorado"}

    # 3. Enviar para o Botpress de forma ASSÍNCRONA
    botpress_url = f"https://api.botpress.cloud/v1/bots/{BOTPRESS_BOT_ID}/converse/{conversation_id}"
    headers = {
        "Authorization": f"Bearer {BOTPRESS_WEBHOOK_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"text": user_message, "type": "text"}
    
    bot_reply_text = "Desculpe, não consegui processar sua solicitação." # Resposta padrão
    try:
        async with httpx.AsyncClient() as client:
            bp_response = await client.post(botpress_url, json=payload, headers=headers)
            bp_response.raise_for_status() # Lança um erro se a resposta for 4xx ou 5xx
            
            # Extrai a resposta do Botpress (ajuste conforme a estrutura da sua resposta)
            responses = bp_response.json().get("responses", [])
            for resp in responses:
                if resp.get("type") == "text":
                    bot_reply_text = resp.get("text", bot_reply_text)
                    break

    except httpx.HTTPStatusError as e:
        print(f"Erro na API do Botpress: {e.response.status_code} - {e.response.text}")
        # Você pode querer notificar o usuário que o bot está com problemas
        bot_reply_text = "Ocorreu um erro ao me comunicar com o assistente. Tente novamente."
    except Exception as e:
        print(f"Erro inesperado ao contatar o Botpress: {e}")

    # 4. Enviar de volta ao Google Chat (usando run_in_threadpool)
    # A biblioteca do Google é síncrona, então a executamos em uma thread separada
    # para não bloquear o loop de eventos principal do FastAPI.
    message_to_send = { "text": bot_reply_text }
    
    try:
        await run_in_threadpool(
            chat_service.spaces().messages().create(
                parent=space_name,
                body=message_to_send
            ).execute
        )
    except Exception as e:
        print(f"Erro ao enviar mensagem para o Google Chat API: {e}")
        # Lançar um HTTPException fará com que o Google saiba que houve uma falha
        raise HTTPException(status_code=500, detail="Falha ao responder no Google Chat")

    return {"status": "respondido com sucesso"}