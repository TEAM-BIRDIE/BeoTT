import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_community.utilities import SQLDatabase
from langchain_community.tools import QuerySQLDatabaseTool
from operator import itemgetter
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough

# 1. 환경 변수 로드
load_dotenv()

# 2. DB 연결
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = "fintech_agent"

db_uri = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
db = SQLDatabase.from_uri(db_uri)

# 3. LLM 설정
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

# 4. SQL 청소 함수
def clean_sql_query(text: str) -> str:
    """LLM이 뱉은 SQL에서 불필요한 마크다운이나 접두어를 제거합니다."""
    text = text.strip()
    if text.startswith("SQLQuery:"):
        text = text.replace("SQLQuery:", "").strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.lower().startswith("sql"):
            text = text[3:]
    return text.strip()

# --- [변경점 1] create_sql_query_chain을 대체하는 순수 LCEL 체인 ---
# DB 스키마(테이블 정보)를 가져오는 함수
def get_schema(_):
    return db.get_table_info()

# SQL 생성을 위한 프롬프트 템플릿 작성
sql_prompt = PromptTemplate.from_template(
    """You are a {dialect} expert. Given an input question, create a syntactically correct {dialect} query to run.
    Here is the relevant table info: 
    {table_info}
    
    Question: {question}
    SQL Query:"""
)

# 데이터베이스 정보(스키마, 방언)를 프롬프트에 주입하고 LLM을 호출하여 SQL을 생성하는 체인
generate_query = (
    RunnablePassthrough.assign(
        table_info=get_schema,
        dialect=lambda _: db.dialect
    )
    | sql_prompt
    | llm
    | StrOutputParser()
    | clean_sql_query
)

# --- [변경점 2] 전체 파이프라인 연결 ---
execute_query = QuerySQLDatabaseTool(db=db)

answer_prompt = PromptTemplate.from_template(
    """Given the following user question, corresponding SQL query, and SQL result, answer the user question.

    [Rules]
    1. You MUST use the **actual values** from the [SQL Result].
    2. Do NOT use placeholders like "[SQL Result]" or "provided data".
    3. If there are multiple records, list them with bullet points.
    4. Format numbers with commas (e.g., 15,000원) and convert dates to a readable format.
    5. Answer in Korean naturally.

    Question: {question}
    SQL Query: {query}
    SQL Result: {result}
    Answer: """
)

# 최종 체인 조립 (중복되던 clean_sql_query는 generate_query 내부로 이동시켰습니다)
chain = (
    RunnablePassthrough.assign(query=generate_query).assign(
        result=itemgetter("query") | execute_query
    )
    | answer_prompt
    | llm
    | StrOutputParser()
)

# 중복 선언된 함수 제거 및 정리
def get_sql_answer(question):
    try:
        response = chain.invoke({"question": question})
        return response
    except Exception as e:
        return f"데이터 조회 중 오류가 발생했습니다: {e}"

if __name__ == "__main__":
    print(f"결과 1: {get_sql_answer('내 월급통장 잔액이 얼마야?')}")
    print(f"결과 2: {get_sql_answer('최근에 식비로 얼마 썼어?')}")
    print(f"결과 3: {get_sql_answer('가입된 사용자가 총 몇 명이야?')}")