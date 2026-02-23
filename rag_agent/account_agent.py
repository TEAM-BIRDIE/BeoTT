import os
import time
from datetime import datetime
from pathlib import Path
from typing import TypedDict
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import StateGraph, START, END

from utils.handle_sql import get_data

load_dotenv()
llm = ChatOpenAI(model="gpt-5-mini")

# ---------------------------------------------------------
# 프롬프트 경로 설정 및 로딩 함수
# ---------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent
PROMPT_DIR = CURRENT_DIR.parent / "rag_agent" / "prompt" / "sql"

def read_prompt(filename: str) -> str:
    file_path = PROMPT_DIR / filename
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"❌ [Error] 프롬프트 파일을 찾을 수 없습니다: {file_path}")
        return ""

# ---------------------------------------------------------
# 로그 출력 유틸리티 함수
# ---------------------------------------------------------
def print_log(step_name: str, status: str, start_time: float = None, extra_info: str = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    if status == "start":
        # flush=True 추가
        print(f"[{now}] [{step_name}] 시작...", flush=True) 
        return time.time()
        
    elif status == "end" and start_time is not None:
        elapsed = time.time() - start_time
        log_msg = f"[{now}] [{step_name}] 완료 (소요시간: {elapsed:.3f}초)"
        if extra_info:
            log_msg += f"\n   {extra_info}"
        
        # flush=True 추가
        print(log_msg, flush=True) 
        return elapsed

# ---------------------------------------------------------
# DB 유틸리티 함수
# ---------------------------------------------------------
def get_schema_info(allowed_views: list):
    try:
        if not allowed_views:
            return "No accessible tables provided."
            
        placeholders = ','.join(['%s'] * len(allowed_views))
        sql = f"""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME IN ({placeholders})
            AND TABLE_SCHEMA = DATABASE()
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """
        
        results = get_data(sql, allowed_views)
        
        schema_dict = {}
        for row in results:
            t_name = row['TABLE_NAME']
            if t_name not in schema_dict:
                schema_dict[t_name] = []
            schema_dict[t_name].append(f"- {row['COLUMN_NAME']} ({row['DATA_TYPE']})")
            
        schema_text = ""
        for t_name, cols in schema_dict.items():
            schema_text += f"\n[Table/View: {t_name}]\n" + "\n".join(cols) + "\n"
            
        return schema_text.strip()
    except Exception as e:
        return f"스키마 조회 실패: {e}"

def clean_sql_query(text: str) -> str:
    text = text.strip()
    if text.startswith("SQLQuery:"):
        text = text.replace("SQLQuery:", "").strip()
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

def run_db_query(query):
    try:
        if not query:
            return "생성된 쿼리가 없습니다."
        result = get_data(query)
        if not result:
            return "검색 결과가 없습니다."
        return str(result)
    except Exception as e:
        return f"SQL 실행 오류: {e}"

# ---------------------------------------------------------
# SQL 에이전트 상태
# ---------------------------------------------------------
class SQLAgentState(TypedDict, total=False):
    question: str
    username: str
    allowed_views: list
    schema: str
    query: str
    result: str
    response: str

# ---------------------------------------------------------
# 노드
# ---------------------------------------------------------
def node_schema(state: SQLAgentState) -> dict:
    t0 = print_log("1. 스키마 조회 (node_schema)", "start")
    schema = get_schema_info(state.get("allowed_views") or [])
    print_log("1. 스키마 조회 (node_schema)", "end", t0)
    return {"schema": schema}

def node_sql_gen(state: SQLAgentState) -> dict:
    t0 = print_log("2. SQL 쿼리 생성 (node_sql_gen)", "start")
    template = read_prompt("sql_01_generation.md")
    prompt = PromptTemplate.from_template(template)
    chain = prompt | llm | StrOutputParser()
    raw = chain.invoke({
        "question": state["question"],
        "schema": state["schema"],
    })
    query = clean_sql_query(raw)
    print_log("2. SQL 쿼리 생성 (node_sql_gen)", "end", t0, extra_info=f"생성된 SQL:\n      {query}")
    return {"query": query}

def node_execute(state: SQLAgentState) -> dict:
    t0 = print_log("3. SQL 실행 (node_execute)", "start")
    result = run_db_query(state["query"])
    sample_result = str(result)[:100] + "..." if len(str(result)) > 100 else str(result)
    print_log("3. SQL 실행 (node_execute)", "end", t0, extra_info=f"실행 결과 일부: {sample_result}")
    return {"result": result}

def node_answer(state: SQLAgentState) -> dict:
    t0 = print_log("4. 최종 답변 생성 (node_answer)", "start")
    template = read_prompt("sql_02_answer.md")
    prompt = PromptTemplate.from_template(template)
    chain = prompt | llm | StrOutputParser()
    response = chain.invoke({
        "question": state["question"],
        "query": state["query"],
        "result": state["result"],
    })
    print_log("4. 최종 답변 생성 (node_answer)", "end", t0)
    return {"response": response}

# ---------------------------------------------------------
# 그래프 빌드
# ---------------------------------------------------------
_sql_graph = None

def _get_sql_graph():
    global _sql_graph
    if _sql_graph is None:
        builder = StateGraph(SQLAgentState)
        builder.add_node("schema", node_schema)
        builder.add_node("sql_gen", node_sql_gen)
        builder.add_node("execute", node_execute)
        builder.add_node("answer", node_answer)
        builder.add_edge(START, "schema")
        builder.add_edge("schema", "sql_gen")
        builder.add_edge("sql_gen", "execute")
        builder.add_edge("execute", "answer")
        builder.add_edge("answer", END)
        _sql_graph = builder.compile()
    return _sql_graph

# ---------------------------------------------------------
# 외부 호출용 함수
# ---------------------------------------------------------
def get_sql_answer(question, username, allowed_views=None):
    try:
        if allowed_views is None:
            allowed_views = []
            
        print("\n" + "="*50)
        total_t0 = print_log("SQL 에이전트 전체 파이프라인", "start")
        print(f"   [입력 질문]: '{question}' (User: {username})")
        print("="*50)
        
        graph = _get_sql_graph()
        result = graph.invoke({
            "question": question,
            "username": username,
            "allowed_views": allowed_views,
        })
        
        print("="*50)
        print_log("SQL 에이전트 전체 파이프라인", "end", total_t0)
        print("="*50 + "\n")
        
        return result.get("response", "응답을 생성하지 못했습니다.")
    except Exception as e:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        error_msg = f"데이터 조회 중 오류가 발생했습니다: {e}"
        print(f"[{now}] [SQL Agent Error]: {error_msg}")
        return error_msg

if __name__ == "__main__":
    test_views = ["account_summary_view", "transaction_history_view"]
    q = "내 월급통장 잔액이 얼마야?"
    print(f"A: {get_sql_answer(q, 'test_user', test_views)}")