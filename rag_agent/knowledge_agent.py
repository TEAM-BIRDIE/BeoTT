from datetime import datetime
from pathlib import Path
from typing import TypedDict, Literal
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import StateGraph, START, END

from rag_agent.websearch_agent import WebSearchRAG
from utils.agent_utils import read_prompt, print_log
from utils.handle_chromaDB import load_knowledge_base

load_dotenv()

CURRENT_DIR = Path(__file__).resolve().parent
PROMPT_DIR = CURRENT_DIR / "prompt" / "finrag"

SIMILARITY_THRESHOLD = 0.6
WEB_SEARCH_KEYWORDS = ["현재", "최신", "오늘", "주가", "시세", "뉴스", "전망", "날씨", "검색해줘", "얼마야","지금","검색","검색해"]

llm = ChatOpenAI(model="gpt-5-mini")
web_rag = WebSearchRAG()

# ---------------------------------------------------------
# FinRAG 상태
# ---------------------------------------------------------
class FinRAGState(TypedDict, total=False):
    korean_query: str
    original_query: str
    use_web: bool
    relevant_docs: list
    context_text: str
    citations: list
    final_output: str

# ---------------------------------------------------------
# 노드
# ---------------------------------------------------------
def node_route(state: FinRAGState) -> dict:
    t0 = print_log("1. 검색 방식 라우팅 (node_route)", "start")
    korean_query = state["korean_query"]
    use_web = any(kw in korean_query for kw in WEB_SEARCH_KEYWORDS)
    
    extra = f"키워드 감지됨 -> 웹 검색 전환" if use_web else "웹 검색 키워드 없음 -> 내부 DB 검색"
    print_log("1. 검색 방식 라우팅 (node_route)", "end", t0, extra_info=extra)
    return {"use_web": use_web}

def node_web_search(state: FinRAGState) -> dict:
    t0 = print_log("2-A. 웹 검색 수행 (node_web_search)", "start")
    korean_query = state["korean_query"]
    original_query = state.get("original_query")
    
    web_result = web_rag.web_search(korean_query)
    final_output = web_rag.format_web_result(web_result, original_query, korean_query)
    
    print_log("2-A. 웹 검색 수행 (node_web_search)", "end", t0, extra_info="웹 검색 완료 및 포맷팅")
    return {"final_output": final_output}

def node_db_retrieve(state: FinRAGState) -> dict:
    t0 = print_log("2-B. 벡터 DB 검색 (node_db_retrieve)", "start")
    vs = load_knowledge_base()
        
    korean_query = state["korean_query"]
    relevant_docs = []
    
    if vs:
        try:
            results = vs.similarity_search_with_score(korean_query, k=5)
            print(f"   [Search] '{korean_query}' DB 검색 수행")
            for doc, score in results:
                if score <= SIMILARITY_THRESHOLD:
                    relevant_docs.append((doc, score))
                    print(f"      채택: {doc.metadata.get('word')} (거리: {score:.4f})")
                else:
                    print(f"      제외: {doc.metadata.get('word')} (거리: {score:.4f} > {SIMILARITY_THRESHOLD})")
            relevant_docs = relevant_docs[:3]
        except Exception as e:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            print(f"[{now}] DB 검색 중 오류: {e}")
            
    print_log("2-B. 벡터 DB 검색 (node_db_retrieve)", "end", t0, extra_info=f"조회된 유효 문서 수: {len(relevant_docs)}개")
    return {"relevant_docs": relevant_docs}

def node_web_fallback(state: FinRAGState) -> dict:
    t0 = print_log("3-A. 웹 검색으로 폴백 (node_web_fallback)", "start")
    extra = "내부 DB에 관련 정보 없음 (유효 문서 0개) -> 웹 검색 자동 전환"
    print_log("3-A. 웹 검색으로 폴백 (node_web_fallback)", "end", t0, extra_info=extra)
    return node_web_search(state)

def node_db_answer(state: FinRAGState) -> dict:
    t0 = print_log("3-B. DB 기반 답변 생성 (node_db_answer)", "start")
    korean_query = state["korean_query"]
    original_query = state.get("original_query")
    relevant_docs = state.get("relevant_docs") or []
    
    context_text = ""
    citations = []
    for doc, score in relevant_docs:
        word = doc.metadata.get("word", "Term")
        raw_content = doc.page_content
        definition = raw_content.split(":", 1)[1].strip() if ":" in raw_content else raw_content
        context_text += f"- **{word}**: {definition}\n"
        citations.append(f"- **{word}**: {definition[:60]}... (거리: {score:.4f})")

    system_template = read_prompt(PROMPT_DIR, "finrag_01_system.md")
    rag_prompt = PromptTemplate.from_template(system_template)
    rag_chain = rag_prompt | llm | StrOutputParser()
    
    try:
        ai_answer = rag_chain.invoke({"context": context_text, "question": korean_query})
    except Exception as e:
        ai_answer = f"죄송합니다. 답변 생성 중 오류가 발생했습니다. ({e})"

    final_output = f"""
### 🌏 질문
- **Original**: {original_query if original_query else korean_query}
- **Translated**: {korean_query}

### 💡 FinBot의 답변
{ai_answer}

---
### 📚 내부 참고 문헌
{chr(10).join(citations)}
"""
    print_log("3-B. DB 기반 답변 생성 (node_db_answer)", "end", t0)
    return {"final_output": final_output}

def route_after_start(state: FinRAGState) -> Literal["web_search", "db_retrieve"]:
    return "web_search" if state.get("use_web") else "db_retrieve"

def route_after_db(state: FinRAGState) -> Literal["web_fallback", "db_answer"]:
    return "web_fallback" if not (state.get("relevant_docs")) else "db_answer"

# ---------------------------------------------------------
# 그래프 빌드
# ---------------------------------------------------------
_finrag_graph = None

def _get_finrag_graph():
    global _finrag_graph
    if _finrag_graph is None:
        builder = StateGraph(FinRAGState)
        builder.add_node("route", node_route)
        builder.add_node("web_search", node_web_search)
        builder.add_node("db_retrieve", node_db_retrieve)
        builder.add_node("web_fallback", node_web_fallback)
        builder.add_node("db_answer", node_db_answer)

        builder.add_edge(START, "route")
        builder.add_conditional_edges("route", route_after_start, {"web_search": "web_search", "db_retrieve": "db_retrieve"})
        builder.add_edge("web_search", END)
        builder.add_conditional_edges("db_retrieve", route_after_db, {"web_fallback": "web_fallback", "db_answer": "db_answer"})
        builder.add_edge("web_fallback", END)
        builder.add_edge("db_answer", END)
        _finrag_graph = builder.compile()
    return _finrag_graph

def get_rag_answer(korean_query, original_query=None):
    print("\n" + "-"*50)
    total_t0 = print_log("FinRAG 에이전트 파이프라인", "start")
    
    graph = _get_finrag_graph()
    initial: FinRAGState = {"korean_query": korean_query, "original_query": original_query}
    result = graph.invoke(initial)
    
    print("-"*50)
    print_log("FinRAG 에이전트 파이프라인", "end", total_t0)
    print("-"*50 + "\n")
    
    return result.get("final_output", "답변을 생성하지 못했습니다.")

if __name__ == "__main__":
    print(get_rag_answer("금리가 뭐야?"))
    print("=" * 60)
    print(get_rag_answer("현재 삼성전자 주가 알려줘"))
    print("=" * 60)
    print(get_rag_answer("아이유 최신 앨범 뭐야?"))