from pathlib import Path
from datetime import datetime
import time

# 로그 출력
def print_log(step_name: str, status: str, start_time: float = None, extra_info: str = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if status == "start":
        print(f"[{now}] [{step_name}] 시작...",flush=True)
        return time.time()
    elif status == "end" and start_time is not None:
        elapsed = time.time() - start_time
        log_msg = f"[{now}] [{step_name}] 완료 (소요시간: {elapsed:.3f}초)"
        if extra_info:
            log_msg += f"\n   {extra_info}"
        print(log_msg,flush=True)
        return elapsed

# memory.md 초기화
def reset_global_context():
    MEMORY_DIR = Path("logs")
    MEMORY_FILE = MEMORY_DIR / "memory.md"

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        f.write("# 대화 기록\n\n")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{now}] [Memory] 대화 기록 파일(logs/memory.md)이 초기화되었습니다.")


# 프롬프트 경로 설정 및 로딩 함수
def read_prompt(prompt_dir: str, filename: str) -> str:
    file_path = prompt_dir / filename
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{now}] [Error] 프롬프트 파일을 찾을 수 없습니다: {file_path}")
        return ""
