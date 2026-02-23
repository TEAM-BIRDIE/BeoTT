import pymysql
import os
from dotenv import load_dotenv
from dbutils.pooled_db import PooledDB

load_dotenv()

# 전역 풀 생성
POOL = PooledDB(
    creator=pymysql,
    mincached=2,
    maxcached=5,
    maxconnections=10,
    blocking=True,
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    db=os.getenv('DB_NAME'),
    port=int(os.getenv('DB_PORT', 3306)),
    charset='utf8mb4'
)

def _get_connection():
    return POOL.connection()

def execute_query(query, args=None):
    """INSERT, UPDATE, DELETE 전용 (단건): 커밋을 수행함"""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, args)
            conn.commit()
            return cursor.rowcount
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def execute_many(query, args_list):
    """대량 INSERT 전용: 리스트 데이터를 한 번에 넣음"""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(query, args_list)
            conn.commit()
            return cursor.rowcount
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

####### LLM이 생성한 쿼리 수정
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

####### 데이터 조회
def get_data(query, args=None):
    """SELECT 전용: 결과를 반환함"""
    conn = _get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(query, args)
            return cursor.fetchall()
    finally:
        conn.close()
        
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

######## 자주 쓰는 쿼리 정의
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

def get_member_id(username):
    query = f"SELECT user_id FROM members WHERE username = '{username}'"
    result = get_data(query)
    return result[0]["user_id"] if result else None

def get_contact(user_id, target):
    query = f"""
    SELECT contact_id, contact_name, relationship, target_currency_code
    FROM contacts
    WHERE user_id = {user_id}
    AND contact_name = '{target}'
    """
    result = get_data(query)
    return result[0] if result else None

def get_all_contacts(user_id):
    query = f"SELECT contact_name, relationship FROM contacts WHERE user_id = {user_id}"
    return get_data(query)

def get_primary_account(user_id):
    query = f"""
    SELECT account_id, balance
    FROM accounts
    WHERE user_id = {user_id}
    AND is_primary = 1
    """
    result = get_data(query)
    return result[0] if result else None

def get_user_password(username):
    query = f"SELECT pin_code FROM members WHERE username = '{username}'"
    result = get_data(query)
    return result[0]["pin_code"] if result else None

def get_exchange_rate(currency):
    if currency == "KRW":
        return 1.0

    query = f"""
    SELECT send_rate
    FROM exchange_rates
    WHERE currency_code = '{currency}'
    ORDER BY reference_date DESC
    LIMIT 1
    """
    result = get_data(query)
    if not result:
        return None
    return float(result[0]["send_rate"])

def update_balance(account_id, new_balance):
    query = f"UPDATE accounts SET balance = {new_balance} WHERE account_id = {account_id}"
    execute_query(query)

def insert_ledger(
    account_id, contact_id, amount_krw, balance_after,
    exchange_rate, target_amount, target_currency
):
    query = f"""
    INSERT INTO ledger (
        account_id, contact_id, transaction_type, amount, balance_after,
        exchange_rate, target_amount, target_currency_code, description, category
    )
    VALUES (
        {account_id}, {contact_id}, 'TRANSFER', {-amount_krw}, {balance_after},
        {exchange_rate}, {target_amount}, '{target_currency}', '송금', '이체'
    )
    """
    execute_query(query)


##### View 생성
def create_user_views(username: str):
    """
    로그인한 사용자의 전용 View들 생성
    """
    user_id = get_member_id(username)
    
    if not user_id:
        raise ValueError("사용자를 찾을 수 없습니다.")

    # 사용자 기본 정보
    profile_view_sql = f"""
        CREATE OR REPLACE VIEW current_user_profile AS
        SELECT user_id, username, korean_name
        FROM members
        WHERE user_id = {user_id}
    """

    # 사용자 계좌 정보
    accounts_view_sql = f"""
        CREATE OR REPLACE VIEW current_user_accounts AS
        SELECT account_id, balance, is_primary, bank_name, bank_code, account_number,account_alias
        FROM accounts
        WHERE user_id = {user_id}
    """

    # 사용자 거래 내역
    transactions_view_sql = f"""
        CREATE OR REPLACE VIEW current_user_transactions AS
        SELECT t.transaction_id,
               t.account_id,
               t.transaction_type,
               t.amount,
               t.balance_after,
               t.description,
               t.category,
               t.created_at
        FROM ledger t
        JOIN accounts a ON t.account_id = a.account_id
        WHERE a.user_id = {user_id}
    """

    execute_query(profile_view_sql)
    execute_query(accounts_view_sql)
    execute_query(transactions_view_sql)

    return [
        "current_user_profile",
        "current_user_accounts",
        "current_user_transactions"
    ]
