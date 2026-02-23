from datetime import datetime
from pathlib import Path
from typing import TypedDict
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import StateGraph, START, END

from utils.handle_sql import get_schema_info, clean_sql_query, run_db_query
from utils.agent_utils import read_prompt, print_log

load_dotenv()

CURRENT_DIR = Path(__file__).resolve().parent
PROMPT_DIR = CURRENT_DIR / "prompt" / "sql"

llm = ChatOpenAI(model="gpt-5-mini")

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
    template = read_prompt(PROMPT_DIR, "sql_01_generation.md")
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
    template = read_prompt(PROMPT_DIR, "sql_02_answer.md")
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