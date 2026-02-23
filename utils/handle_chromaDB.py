import os
from datetime import datetime
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

from utils.handle_sql import get_data
from utils.agent_utils import print_log
# .env 로드
load_dotenv()

# ==========================================
# 1. 설정 (Configuration)
# ==========================================
current_script_path = os.path.abspath(__file__)
current_script_dir = os.path.dirname(current_script_path)
PERSIST_DIRECTORY = os.path.join(current_script_dir, "..", "data", "financial_terms")
PERSIST_DIRECTORY = os.path.normpath(PERSIST_DIRECTORY)

print(f"📍 확정된 저장 경로: {PERSIST_DIRECTORY}") # 확인용 출력

vectorstore = None
COLLECTION_NAME = "financial_terms"
BATCH_SIZE = 100

# ==========================================
# 2. ChromaDB 초기화
# ==========================================
openai_ef = embedding_functions.OpenAIEmbeddingFunction(
    api_key=os.getenv("OPENAI_API_KEY"),
    model_name="text-embedding-3-large"
)
client = chromadb.PersistentClient(path=PERSIST_DIRECTORY)
collection = client.get_or_create_collection(
    name=COLLECTION_NAME,
    embedding_function=openai_ef
)

def sync_mysql_to_chroma():
    print(f"저장 경로: {os.path.abspath(PERSIST_DIRECTORY)}")
    print("MySQL 데이터 조회 시작...")

    try:
        sql = "SELECT id, word, definition FROM terms WHERE definition IS NOT NULL"
        rows = get_data(sql)

        if not rows:
            print("저장할 데이터가 없습니다.")
            return

        print(f"총 {len(rows)}개의 데이터를 가져왔습니다.")

        ids_list = []
        documents_list = []
        metadatas_list = []

        for row in rows:
            doc_id = str(row['id'])
            content = f"{row['word']}: {row['definition']}"
            metadata = {
                "original_id": row['id'],
                "word": row['word']
            }

            ids_list.append(doc_id)
            documents_list.append(content)
            metadatas_list.append(metadata)

        print("💾 ChromaDB 저장(Upsert) 시작...")
        
        total_count = len(ids_list)
        
        for i in range(0, total_count, BATCH_SIZE):
            batch_ids = ids_list[i : i + BATCH_SIZE]
            batch_docs = documents_list[i : i + BATCH_SIZE]
            batch_metas = metadatas_list[i : i + BATCH_SIZE]
            collection.upsert(
                ids=batch_ids,
                documents=batch_docs,
                metadatas=batch_metas
            )
            current_progress = min(i + BATCH_SIZE, total_count)
            print(f"   - Progress: {current_progress} / {total_count} 완료")

        print("모든 데이터 동기화 완료!")

    except Exception as e:
        print(f"오류 발생: {e}")

def load_knowledge_base():
    """ChromaDB 연결 설정"""
    global vectorstore
    
    if vectorstore is not None:
        return vectorstore
    CHROMA_DB_PATH = "data/financial_terms"
    COLLECTION_NAME = "financial_terms"
    
    t0 = print_log("RAG ChromaDB 연결", "start")
    try:
        embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
        vectorstore = Chroma(
            persist_directory=str(CHROMA_DB_PATH),
            embedding_function=embeddings,
            collection_name=COLLECTION_NAME,
            collection_metadata={"hnsw:space": "l2"},
        )
        print_log("RAG ChromaDB 연결", "end", t0, extra_info=f"Metric: L2, 경로: {CHROMA_DB_PATH}")
    except Exception as e:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{now}] ❌ ChromaDB 연결 오류: {e}")
        vectorstore = None

    return vectorstore


if __name__ == "__main__":
    sync_mysql_to_chroma()