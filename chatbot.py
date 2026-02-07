import os
import json
import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from openai import OpenAI
from dotenv import load_dotenv

# 1. 환경 설정
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "fin_dictionary")

# 2. DB 로딩 (메모리 최적화)
print("⏳ [System] 다국어 금융 AI가 지식을 로딩 중입니다...")
db_url = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
engine = create_engine(db_url)

df = pd.read_sql("SELECT word, definition, embedding FROM terms", engine)
df['embedding'] = df['embedding'].apply(json.loads)
embedding_matrix = np.vstack(df['embedding'].values)

print(f"✅ 로딩 완료! (총 {len(df)}개 용어)")
print("="*50)

# 유틸리티 함수
def get_embedding(text):
    return client.embeddings.create(input=[text], model="text-embedding-3-small").data[0].embedding

def search_docs(query_text, top_k=3):
    query_vec = get_embedding(query_text)
    similarities = np.dot(embedding_matrix, query_vec)
    df['similarity'] = similarities
    return df.sort_values('similarity', ascending=False).head(top_k)

# 🔥 [핵심 기능] 질문의 의도를 파악하고 '한국어 검색어'를 추출
def translate_query_to_korean(user_query):
    # GPT에게 "이 외국어 질문이 한국어 금융 용어로 무엇인지" 물어봅니다.
    response = client.chat.completions.create(
        model="gpt-4o-mini",  # [변경] 비용 절감 모델 적용
        messages=[
            {"role": "system", "content": """
             You are a sophisticated translation assistant for a Korean Financial Terminology Search Engine.
             Your goal is to convert the user's query (in any language) into the most appropriate **Korean financial keyword** for searching the database.
             
             [Rules]
             1. If the user asks for a definition (e.g., "What is inflation?"), output ONLY the Korean term (e.g., "인플레이션").
             2. If the user describes a concept (e.g., "account with negative balance", "Sổ tài khoản âm"), map it to the specific Korean financial product name (e.g., "마이너스통장", "한도대출").
             3. If the query is already in Korean, output it as is.
             4. Do NOT output any explanation, just the Korean keyword(s).
             """},
            {"role": "user", "content": user_query}
        ],
        temperature=0
    )
    return response.choices[0].message.content.strip()

# RAG 답변 생성
def ask_multilingual_bot(user_query):
    # 1. [번역 단계] 외국어 질문 -> 한국어 검색어 변환
    korean_search_term = translate_query_to_korean(user_query)
    print(f"   ↳ 🔍 검색용 키워드 변환: '{user_query}' -> '{korean_search_term}'")
    
    # 2. [검색 단계] 한국어 키워드로 DB 검색 (정확도 극대화)
    relevant_docs = search_docs(korean_search_term)
    
    # 유사도 체크 (관련 없는 질문 방어)
    if relevant_docs.iloc[0]['similarity'] < 0.35:
        return "Sorry, I couldn't find relevant financial terms in my database."

    # 3. [컨텍스트 구성]
    context_text = ""
    for idx, row in relevant_docs.iterrows():
        context_text += f"Term: {row['word']}\nDefinition: {row['definition']}\n\n"

    # 4. [답변 생성 단계] "찾은 한국어 정보를 바탕으로, 사용자 언어로 답변하라"
    system_prompt = f"""
    You are a helpful Financial Expert AI suitable for foreigners or financial beginners.
    
    1. Read the provided [Context] (Korean financial terms).
    2. Answer the user's original question based on the [Context].
    3. **IMPORTANT:** You MUST answer in the **SAME LANGUAGE** as the user's question.
       (e.g., If user asks in Vietnamese, answer in Vietnamese. If English, in English.)
    4. Explain the concept simply and clearly.
    
    [Context]
    {context_text}
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query} # 원본 질문 그대로 전달
        ],
        temperature=0
    )
    
    return response.choices[0].message.content

# 메인 루프
if __name__ == "__main__":
    print("🌍 다국어 금융 AI 챗봇 (지원: 🇰🇷, 🇺🇸, 🇻🇳, 🇨🇳 등)")
    print("질문을 입력하세요 (예: 'What is LTV?', 'Lạm phát là gì?')")
    
    while True:
        user_input = input("\nUser: ")
        if user_input.lower() in ['exit', 'quit']:
            break
        
        if not user_input.strip(): continue

        answer = ask_multilingual_bot(user_input)
        print(f"\nAI: {answer}")
        print("-" * 50)