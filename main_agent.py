import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

# 우리가 만든 두 전문가(모듈)를 불러옵니다.
from sql_agent import get_sql_answer
from chatbot import get_rag_answer

# 환경 변수 로드
load_dotenv()

# 분류기(Router)용 LLM (가볍고 빠른 모델 사용 추천)
router_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

# --- 1. 의도 분류 프롬프트 (Router) ---
router_template = """
Given the user's question, classify it into one of the two categories: 'DATABASE' or 'KNOWLEDGE'.

[Definitions]
- **DATABASE**: Questions about personal banking data, account balance, transaction history, transfer records, or specific user information. (e.g., "My balance?", "How much did I spend?", "Transfer to mom")
- **KNOWLEDGE**: Questions about financial terms, economic concepts, definitions, or general banking procedures. (e.g., "What is inflation?", "Explain SWIFT code", "How to save money")

[Rule]
- Output ONLY one word: 'DATABASE' or 'KNOWLEDGE'.
- Do not add any explanation.

Question: {question}
Category:
"""

router_prompt = PromptTemplate.from_template(router_template)
router_chain = router_prompt | router_llm | StrOutputParser()

# --- 2. 메인 에이전트 함수 ---
def run_fintech_agent(question):
    print(f"\nUser: {question}")
    
    # 1단계: 의도 파악
    category = router_chain.invoke({"question": question}).strip()
    print(f"🕵️ 의도 분석 결과: [{category}]")
    
    final_answer = ""
    
    # 2단계: 전문가 호출
    if category == "DATABASE":
        print("🏦 은행 직원(SQL Agent)을 연결합니다...")
        final_answer = get_sql_answer(question)
        
    elif category == "KNOWLEDGE":
        print("🎓 금융 교수(RAG Agent)를 연결합니다...")
        # [수정] 임시 메시지를 지우고 실제 RAG 함수를 호출합니다.
        final_answer = get_rag_answer(question) 
        
    else:
        final_answer = "죄송합니다. 질문의 의도를 파악하지 못했습니다."

    return final_answer


# --- 실행 테스트 ---
if __name__ == "__main__":
    # 시나리오 1: SQL 질문
    print("\n🤖 답변:", run_fintech_agent("내 월급통장 잔액 얼마야?"))
    
    # 시나리오 2: RAG 질문
    print("\n🤖 답변:", run_fintech_agent("적금이 영어로 뭐야?"))