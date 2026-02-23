import json
from datetime import datetime
from pathlib import Path
from typing import TypedDict, List
from dotenv import load_dotenv
import bcrypt

from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import StateGraph, START, END

import utils.handle_sql as sql
from utils.agent_utils import read_prompt, print_log

load_dotenv()
CURRENT_DIR = Path(__file__).resolve().parent
PROMPT_DIR = CURRENT_DIR / "prompt" / "transfer"

llm = ChatOpenAI(model="gpt-5-mini")

# ---------------------------------------------------------
# 송금 정보 추출 그래프
# ---------------------------------------------------------
class TransferExtractState(TypedDict):
    question: str
    raw_llm_output: str
    extracted: dict

def _parse_transfer_json(text: str) -> dict:
    """JSON 파싱 및 예외 처리"""
    try:
        text = text.strip().replace("```json", "").replace("```", "")
        return json.loads(text)
    except Exception as e:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{now}] JSON Parsing Error: {e}, Raw: {text}")
        return {"target": None, "amount": None, "currency": None}

def _node_extract(state: TransferExtractState) -> dict:
    """
    사용자 발화에서 송금 대상, 금액, 통화를 추출합니다.
    """
    t0 = print_log("1. LLM 송금 정보 추출 (node_extract)", "start")
    
    template = read_prompt(PROMPT_DIR, "transfer_01_extract.md")
    
    prompt = PromptTemplate.from_template(template)
    chain = prompt | llm | StrOutputParser()
    
    raw = chain.invoke({"question": state["question"]})
    extracted = _parse_transfer_json(raw)
    
    print_log("1. LLM 송금 정보 추출 (node_extract)", "end", t0, extra_info=f"추출 결과: {extracted}")
    return {"raw_llm_output": raw, "extracted": extracted}

_transfer_extract_graph = None

def _get_transfer_extract_graph():
    global _transfer_extract_graph
    if _transfer_extract_graph is None:
        builder = StateGraph(TransferExtractState)
        builder.add_node("extract", _node_extract)
        builder.add_edge(START, "extract")
        builder.add_edge("extract", END)
        _transfer_extract_graph = builder.compile()
    return _transfer_extract_graph

def _invoke_transfer_extract(question: str) -> dict:
    graph = _get_transfer_extract_graph()
    result = graph.invoke({"question": question})
    return result.get("extracted", {"target": None, "amount": None, "currency": None})

# ---------------------------------------------------------
# LLM 기반 연락처 의미 매칭 함수
# ---------------------------------------------------------
def _find_best_match_contact_llm(user_input: str, contacts: List[dict]) -> str | None:
    """
    단순 문자열 비교 실패 시, LLM을 통해 의미적 매칭을 수행합니다.
    예: user_input="엄마", contacts=[{'contact_name': 'Mother'}] -> returns 'Mother'
    """
    t0 = print_log("2. LLM 기반 연락처 의미 매칭", "start")
    
    if not contacts:
        print_log("2. LLM 기반 연락처 의미 매칭", "end", t0, extra_info="연락처 목록이 비어있음")
        return None

    candidates_str = "\n".join([
        f"- Name: {c['contact_name']} (Relationship: {c.get('relationship', 'N/A')})" 
        for c in contacts
    ])

    template = read_prompt(PROMPT_DIR, "transfer_02_contact_match.md")
    
    prompt = PromptTemplate.from_template(template)
    chain = prompt | llm | StrOutputParser()
    
    try:
        matched_name = chain.invoke({"user_input": user_input, "candidates": candidates_str}).strip()
        
        if matched_name == "NONE":
            print_log("2. LLM 기반 연락처 의미 매칭", "end", t0, extra_info="적절한 매칭 대상 없음 (NONE)")
            return None
        for c in contacts:
            if c["contact_name"] == matched_name:
                print_log("2. LLM 기반 연락처 의미 매칭", "end", t0, extra_info=f"매칭 성공: '{matched_name}'")
                return matched_name
                
        print_log("2. LLM 기반 연락처 의미 매칭", "end", t0, extra_info=f"매칭 실패: 반환된 이름 '{matched_name}'이 DB에 없음")
        return None
        
    except Exception as e:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{now}] LLM Matching Error: {e}")
        return None

def _resolve_contact_name(user_id, user_input):
    """
    사용자 입력을 바탕으로 정확한 DB 내 연락처 이름(contact_name)을 찾습니다.
    1. 정확한 이름 매칭
    2. 관계(relationship) 매칭
    3. LLM 의미 기반 매칭 (New)
    """
    contacts = sql.get_all_contacts(user_id)
    if not contacts:
        return None
        
    user_input_clean = user_input.strip()
    user_input_lower = user_input_clean.lower()

    # 1차 시도: 정확한 문자열 매칭
    for c in contacts:
        if user_input_lower == c["contact_name"].lower():
            return c["contact_name"]
        if c.get("relationship") and user_input_lower == str(c["relationship"]).lower():
            return c["contact_name"]
            
    # 2차 시도: LLM을 이용한 의미론적 매칭
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{now}] 🔀 '{user_input}' 정확한 DB 매칭 실패. LLM 매칭 시도...")
    matched_name = _find_best_match_contact_llm(user_input_clean, contacts)
    
    if matched_name:
        return matched_name

    return None

# ---------------------------------------------------------
# 메인 송금 로직
# ---------------------------------------------------------
def process_transfer(question: str, username: str, context: dict | None = None):

    context = context or {}

    user_id = sql.get_member_id(username)
    if not user_id:
        return {"status": "ERROR", "message": "사용자를 찾을 수 없습니다."}

    # --------------------------------------------------
    # 1. PIN Code 입력 단계
    # --------------------------------------------------
    if context.get("awaiting_password"):
        t0_pin = print_log("송금 승인: PIN 검증 및 트랜잭션 실행", "start")
        stored_pin = sql.get_user_password(username)
        if not stored_pin:
            return {"status": "ERROR", "message": "사용자 정보를 찾을 수 없습니다."}

        if isinstance(stored_pin, str):
            stored_pin = stored_pin.encode('utf-8')

        # 패스워드 검증
        if bcrypt.checkpw(question.encode('utf-8'), stored_pin) == False:
            context["password_attempts"] = context.get("password_attempts", 0) + 1
            if context["password_attempts"] >= 5:
                print_log("송금 승인: PIN 검증", "end", t0_pin, extra_info="PIN 5회 오류로 취소")
                return {"status": "FAIL", "message": "PIN Code 5회 오류. 송금 실패."}

            print_log("송금 승인: PIN 검증", "end", t0_pin, extra_info=f"오류 횟수: {context['password_attempts']}")
            return {
                "status": "NEED_PASSWORD",
                "message": f"PIN Code 오류. 남은 기회: {5 - context['password_attempts']}",
                "context": context
            }

        # 송금 실행 (DB 업데이트)
        account = sql.get_primary_account(user_id)
        contact = sql.get_contact(user_id, context["target"]) 

        new_balance = float(account["balance"]) - context["amount_krw"]
        sql.update_balance(account["account_id"], new_balance)

        sql.insert_ledger(
            account["account_id"],
            contact["contact_id"],
            context["amount_krw"],
            new_balance,
            context["exchange_rate"],
            context["amount"],
            context["currency"]
        )

        print_log("송금 승인: PIN 검증 및 트랜잭션 실행", "end", t0_pin, extra_info=f"송금 완료 / 남은 잔액: {int(new_balance):,}")
        return {"status": "SUCCESS", "message": f"송금이 완료되었습니다. (잔액: {int(new_balance):,}원)"}

    # --------------------------------------------------
    # 2. 확인 단계 (Yes / No)
    # --------------------------------------------------
    if context.get("awaiting_confirm"):
        t0_cf = print_log("송금 전 최종 확인", "start")
        yes_signals = ["__yes__", "y", "yes", "네", "응", "맞아"]
        no_signals  = ["__no__",  "n", "no", "아니", "취소"]

        answer = question.strip().lower()

        if answer in no_signals:
            print_log("송금 전 최종 확인", "end", t0_cf, extra_info="사용자 송금 취소")
            return {"status": "CANCEL", "message": "송금이 취소되었습니다."}

        if answer not in yes_signals:
            print_log("송금 전 최종 확인", "end", t0_cf, extra_info="응답 불분명, 재확인 요청")
            return {
                "status": "CONFIRM",
                "message": context.get("confirm_message", "송금을 확인해주세요."),
                "context": context,
                "ui_type": "confirm_buttons"
            }

        context["awaiting_confirm"] = False
        context["awaiting_password"] = True
        context["password_attempts"] = 0

        print_log("송금 전 최종 확인", "end", t0_cf, extra_info="승인 확인됨. PIN 요청 진행")
        return {
            "status": "NEED_PASSWORD",
            "message": "PIN Code를 입력해주세요.",
            "context": context
        }

    # --------------------------------------------------
    # 3. HITL (Human-in-the-Loop) - 부족 정보 보완
    # --------------------------------------------------
    if context.get("missing_field"):
        field = context["missing_field"]
        t0_hitl = print_log(f"누락된 정보({field}) 보완 처리", "start")

        if field == "target":
            resolved = _resolve_contact_name(user_id, question)
            if not resolved:
                print_log(f"누락된 정보({field}) 보완 처리", "end", t0_hitl, extra_info="연락처 조회 실패")
                return {
                    "status": "NEED_INFO",
                    "field": "target",
                    "message": "연락처를 찾을 수 없습니다. 정확한 이름을 입력해주세요.",
                    "context": context
                }
            context["target"] = resolved

        elif field == "amount":
            try:
                clean_amt = question.strip().replace(",", "").replace("원", "")
                context["amount"] = float(clean_amt)
            except:
                print_log(f"누락된 정보({field}) 보완 처리", "end", t0_hitl, extra_info="금액 파싱 실패")
                return {
                    "status": "NEED_INFO",
                    "field": "amount",
                    "message": "금액을 숫자로 입력해주세요.",
                    "context": context
                }

        elif field == "currency":
            context["currency"] = question.strip().upper()

        context.pop("missing_field")
        print_log(f"누락된 정보({field}) 보완 처리", "end", t0_hitl, extra_info=f"성공적으로 보완됨: {context.get(field)}")

    # --------------------------------------------------
    # 4. 최초 요청
    # --------------------------------------------------
    if not context.get("target") and not context.get("amount"):
        info = _invoke_transfer_extract(question)
        context["target"]   = info.get("target")
        context["amount"]   = info.get("amount")
        context["currency"] = info.get("currency")

    target   = context.get("target")
    amount   = context.get("amount")
    currency = context.get("currency")

    if not target:
        context["missing_field"] = "target"
        return {
            "status": "NEED_INFO",
            "field": "target",
            "message": "송금할 대상을 입력해주세요.",
            "context": context
        }

    resolved = _resolve_contact_name(user_id, target)
    if not resolved:
        context["missing_field"] = "target"
        return {
            "status": "NEED_INFO",
            "field": "target",
            "message": f"'{target}'님을 연락처에서 찾을 수 없습니다. 정확한 이름을 알려주세요.",
            "context": context
        }
    context["target"] = resolved

    if not amount:
        context["missing_field"] = "amount"
        return {
            "status": "NEED_INFO",
            "field": "amount",
            "message": "송금 금액을 입력해주세요.",
            "context": context
        }

    if not currency:
        context["currency"] = "KRW"
        currency = "KRW"

    rate = sql.get_exchange_rate(currency)
    if rate is None:
        return {"status": "ERROR", "message": f"{currency} 환율 정보를 찾을 수 없습니다."}

    account = sql.get_primary_account(user_id)
    if not account:
        return {"status": "ERROR", "message": "주 계좌를 찾을 수 없습니다."}

    amount_krw = float(amount) * rate

    if amount_krw > float(account["balance"]):
        return {"status": "ERROR", "message": "잔액이 부족합니다."}

    confirm_message = f"{resolved}님에게 {int(amount):,} {currency} ({int(amount_krw):,}원) 송금하시겠습니까?"

    context.update({
        "target":           resolved,
        "amount":           float(amount),
        "currency":         currency,
        "amount_krw":       amount_krw,
        "exchange_rate":    rate,
        "awaiting_confirm": True,
        "confirm_message":  confirm_message,
    })

    return {
        "status":   "CONFIRM",
        "message":  confirm_message,
        "context":  context,
        "ui_type":  "confirm_buttons"
    }

# ---------------------------------------------------------
# 외부 호출 함수
# ---------------------------------------------------------
def get_transfer_answer(question, username, context=None):
    print("\n" + "-"*50)
    total_t0 = print_log("Transfer Agent 상태 머신 파이프라인", "start")
    
    try:
        result = process_transfer(question, username, context)
        
        print("-" * 50)
        print_log("Transfer Agent 상태 머신 파이프라인", "end", total_t0, extra_info=f"최종 상태: {result.get('status')}")
        print("-" * 50 + "\n")
        return result
        
    except Exception as e:
        import traceback
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{now}] Transfer Agent 오류: {e}")
        traceback.print_exc()
        return {"status": "ERROR", "message": f"시스템 오류가 발생했습니다: {e}"}

if __name__ == "__main__":
    print("Transfer Agent with Advanced Matching Ready")