# main.py - Versão Final para Produção (Render)

import os
import json
from fastapi import FastAPI, Request, HTTPException
from google.oauth2 import service_account
from googleapiclient.discovery import build
import httpx  
from dotenv import load_dotenv 
from fastapi.concurrency import run_in_threadpool

load_dotenv()

app = FastAPI()


BOTPRESS_BOT_ID = os.getenv("BOTPRESS_BOT_ID")
BOTPRESS_WEBHOOK_TOKEN = os.getenv("BOTPRESS_WEBHOOK_TOKEN")

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")


if not all([BOTPRESS_BOT_ID, BOTPRESS_WEBHOOK_TOKEN, GOOGLE_CREDENTIALS_JSON]):
    raise Exception("Erro: Uma ou mais variáveis de ambiente essenciais não foram configuradas.")

SCOPES = ["https://www.googleapis.com/auth/chat.bot"]



google_credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)

credentials = service_account.Credentials.from_service_account_info(
    google_credentials_info, scopes=SCOPES
)
chat_service = build("chat", "v1", credentials=credentials)


# === ROTAS ===

@app.post("/chat/message")
async def on_message(req: Request):
    """
    Recebe eventos do Google Chat, processa a mensagem e responde.
    """
    body = await req.json()
    print("Recebido do Google Chat:", body) # Log para depuração

    # Prevenção de Loop: Ignorar mensagens do próprio bot
    if body.get("message", {}).get("sender", {}).get("type") == "BOT":
        return {"status": "ignorado, mensagem de bot"}

    # Extrair informações essenciais do Google Chat
    user_message = body.get("message", {}).get("text", "")
    space_name = body.get("space", {}).get("name")
    
    # Ignorar eventos que não são mensagens de texto (ex: adicionar/remover membros)
    if not user_message or not space_name:
        return {"status": "evento ignorado"}

    # Usar o ID do espaço como ID da conversa para manter o contexto no Botpress
    conversation_id = space_name.split('/')[-1]

    # Enviar para o Botpress de forma assíncrona
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
            
            responses = bp_response.json().get("responses", [])
            for resp in responses:
                if resp.get("type") == "text":
                    bot_reply_text = resp.get("text", bot_reply_text)
                    break
    except httpx.HTTPStatusError as e:
        print(f"Erro na API do Botpress: {e.response.status_code} - {e.response.text}")
        bot_reply_text = "Ocorreu um erro ao me comunicar com o assistente. Tente novamente."
    except Exception as e:
        print(f"Erro inesperado ao contatar o Botpress: {e}")

    # Enviar de volta ao Google Chat (usando run_in_threadpool para a biblioteca síncrona)
    message_to_send = {"text": bot_reply_text}
    
    try:
        await run_in_threadpool(
            chat_service.spaces().messages().create(
                parent=space_name,
                body=message_to_send
            ).execute
        )
    except Exception as e:
        print(f"Erro ao enviar mensagem para o Google Chat API: {e}")
        raise HTTPException(status_code=500, detail="Falha ao responder no Google Chat")

    return {"status": "respondido com sucesso"}
