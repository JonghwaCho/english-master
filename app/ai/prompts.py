"""AI prompt templates for the various learning actions."""


def build_prompt(action: str, sentence: str) -> str:
    """Build a prompt for the given action and sentence."""
    if action == "literal":
        return f"""다음 영어 문장을 한국어로 직독직해해주세요.

문장: {sentence}

형식:
WORDS: 단어별 해석
CHUNKS: 청크별 해석
FULL: 전체 번역
TIP: 학습 팁
"""
    if action == "similar":
        return f"""다음 영어 문장과 같은 구조의 유사 문장 3개를 만들어주세요.
각 문장 아래에 한국어 번역을 달아주세요.

원문: {sentence}

형식:
1. English sentence
   한국어 번역
2. ...
3. ...
"""
    if action == "grammar":
        return f"""다음 영어 문장의 문법 구조를 한국어로 상세히 설명해주세요.

문장: {sentence}

다음을 포함해주세요:
- 주어/동사/목적어 분석
- 사용된 시제
- 문장 패턴
- 주요 문법 포인트
"""
    if action == "words":
        return f"""다음 문장에 나온 각 단어/구를 한국어로 설명해주세요.

문장: {sentence}

각 단어에 대해:
- 뜻
- 품사
- 발음 힌트
- 예문 (영어 + 번역)
- 💡 팁
"""
    if action == "quiz":
        return f"""다음 영어 문장을 기반으로 학습 퀴즈 3개를 만들어주세요.

문장: {sentence}

형식:
1. [선택형] 질문 - A/B/C/D 보기 - 정답
2. [어순] 단어 배열 퀴즈 - 정답
3. [번역] 한국어 → 영어 퀴즈 - 정답
"""
    raise ValueError(f"Unknown action: {action}")


def build_word_meaning_prompt(word: str) -> str:
    return f"""영어 단어 '{word}'의 한국어 뜻을 간결하게 설명해주세요.
형식: [품사] 뜻1, 뜻2
예시 형식: [명사] 사과, [동사] 적용하다
"""
