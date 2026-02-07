import re

# 파일 경로 설정
INDEX_FILE = "extracted_terms.txt"
RESULT_FILE = "final_verification_strict.txt"

def compare_files():
    print(f"🔍 '{INDEX_FILE}' vs '{RESULT_FILE}' 비교 분석 시작...\n")
    
    # 1. 목차 파일 로드 (extracted_terms.txt)
    index_terms = set()
    try:
        with open(INDEX_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                
                # 패턴: "숫자. 용어" 형식에서 용어만 추출
                # 예: "1. 가계수지" -> "가계수지"
                match = re.match(r'^\d+\.\s*(?P<term>.*)', line)
                if match:
                    term = match.group('term').strip()
                    index_terms.add(term)
    except FileNotFoundError:
        print(f"❌ 오류: '{INDEX_FILE}' 파일을 찾을 수 없습니다.")
        return

    print(f"✅ 목차 원본 개수: {len(index_terms)}개")

    # 2. 결과 파일 로드 (final_verification_strict.txt)
    found_terms = set()
    try:
        with open(RESULT_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # 패턴: "[용어]" 형식에서 대괄호 제거
                if line.startswith('[') and line.endswith(']'):
                    term = line[1:-1].strip()
                    found_terms.add(term)
    except FileNotFoundError:
        print(f"❌ 오류: '{RESULT_FILE}' 파일을 찾을 수 없습니다.")
        return

    print(f"✅ 본문 추출 개수: {len(found_terms)}개")
    print("-" * 50)

    # 3. 차집합 구하기 (목차에는 있는데 본문엔 없는 것)
    missing_terms = sorted(list(index_terms - found_terms))
    
    print(f"🚨 누락된 용어: 총 {len(missing_terms)}개")
    print("=" * 50)
    for term in missing_terms:
        print(f"- {term}")
    print("=" * 50)
    
    print("\n[원인 분석 힌트]")
    print("1. 목차 용어에 '경제금융용...' 같은 노이즈가 붙어있지 않은가?")
    print("2. 본문 제목이 아주 길어서(괄호 등) 다르게 인식되지 않았는가?")

if __name__ == "__main__":
    compare_files()