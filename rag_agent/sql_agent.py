import os
from pathlib import Path
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

from utils.handle_sql import get_data

# 1. 환경 변수 로드
load_dotenv()

# 2. LLM 설정
llm = ChatOpenAI(model="gpt-5-mini")

# ---------------------------------------------------------
# [설정] 프롬프트 경로 설정 및 로딩 함수
# ---------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent
PROMPT_DIR = CURRENT_DIR.parent /"rag_agent"/ "prompt" / "sql"

def read_prompt(filename: str) -> str:
    """MD 파일을 읽어서 문자열로 반환하는 함수"""
    file_path = PROMPT_DIR / filename
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"❌ [Error] 프롬프트 파일을 찾을 수 없습니다: {file_path}")
        return ""

# ---------------------------------------------------------
# DB 유틸리티 함수
# ---------------------------------------------------------
def get_schema_info(allowed_views: list):
    """허용된 뷰 목록을 받아 스키마 정보를 텍스트로 반환"""
    try:
        if not allowed_views:
            return "No accessible tables provided."

        schema_text = ""
        for view_name in allowed_views:
            schema_text += f"\n[Table/View: {view_name}]\n"
            
            # DESCRIBE 쿼리로 컬럼 정보 조회
            columns = get_data(f"DESCRIBE {view_name}")
            if columns:
                for col in columns:
                    schema_text += f"- {col['Field']} ({col['Type']})\n"
            else:
                schema_text += "- (No columns found or permission denied)\n"

        return schema_text.strip()

    except Exception as e:
        return f"스키마 조회 실패: {e}"

def clean_sql_query(text: str) -> str:
    """LLM이 생성한 SQL에서 마크다운이나 불필요한 텍스트 제거"""
    text = text.strip()
    # SQLQuery: 접두어 제거
    if text.startswith("SQLQuery:"):
        text = text.replace("SQLQuery:", "").strip()
    # 마크다운 코드 블록 제거
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            if part.lower().strip().startswith("sql"):
                text = part.strip()[3:].strip()
                break
            elif len(part) > 10 and "select" in part.lower():
                text = part.strip()
                break
    return text.strip()

def run_db_query(query, username):
    """실제 SQL 실행 및 결과 반환 (문자열 변환)"""
    try:
        # 안전장치: 쿼리가 비어있으면 실행 안 함
        if not query:
            return "생성된 쿼리가 없습니다."
            
        print(f"🔄 [DB Executing]: {query}")
        result = get_data(query) # handle_sql.py 함수 사용
        
        if not result:
            return "검색 결과가 없습니다."
        return str(result)
    except Exception as e:
        return f"SQL 실행 오류: {e}"

# ---------------------------------------------------------
# 체인 구성 (LangChain Pipeline)
# ---------------------------------------------------------

# (1) Text-to-SQL 체인
sql_gen_template = read_prompt("sql_01_generation.md")
sql_gen_prompt = PromptTemplate.from_template(sql_gen_template)

sql_chain = (
    RunnablePassthrough.assign(schema=lambda x: get_schema_info(x["allowed_views"])) 
    | sql_gen_prompt 
    | llm 
    | StrOutputParser() 
    | clean_sql_query
)

# (2) 최종 답변 생성 체인
answer_template = read_prompt("sql_02_answer.md")
answer_prompt = PromptTemplate.from_template(answer_template)

# 전체 파이프라인 연결
# 입력: {question, username, allowed_views}
full_chain = (
    RunnablePassthrough.assign(query=sql_chain)
    .assign(result=lambda x: run_db_query(x["query"], x["username"]))
    | answer_prompt
    | llm
    | StrOutputParser()
)

# ---------------------------------------------------------
# 외부 호출용 함수
# ---------------------------------------------------------
def get_sql_answer(question, username, allowed_views=None):
    """
    사용자 질문을 받아 SQL로 변환하여 DB 조회 후 답변 반환
    :param question: 사용자 질문 (예: "내 잔액 얼마야?")
    :param username: 사용자 ID (쿼리 실행 시 필요할 수 있음)
    :param allowed_views: 조회 권한이 있는 테이블/뷰 리스트
    """
    try:
        # 뷰 권한이 없으면 기본 빈 리스트 처리
        if allowed_views is None:
            allowed_views = []

        print(f"\n🔍 [SQL Agent] 질문 분석: '{question}' (User: {username})")

        response = full_chain.invoke({
            "question": question, 
            "username": username,
            "allowed_views": allowed_views
        })
        
        return response

    except Exception as e:
        error_msg = f"데이터 조회 중 오류가 발생했습니다: {e}"
        print(f"❌ [SQL Agent Error]: {error_msg}")
        return error_msg

# --- 테스트 코드 ---
if __name__ == "__main__":
    # 테스트를 위한 가짜 뷰 리스트
    test_views = ["account_summary_view", "transaction_history_view"]
    q = "내 월급통장 잔액이 얼마야?"
    
    print(f"Q: {q}")
    # 실제 실행 시 DB 연결이 되어 있어야 함
    print(f"A: {get_sql_answer(q, 'test_user', test_views)}")