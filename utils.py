import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

def init_llm():
    load_dotenv()
    api_key = os.getenv("API_KEY")

    return ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-001",
        temperature=0,
        max_tokens=None,
        timeout=None,
        max_retries=2,
        google_api_key=api_key,
    )