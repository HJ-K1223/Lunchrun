# Lunch Run 질문 해석 규칙 수정 안내

현재 5단계 기본 가드레일까지 연결되어 `/api/chat`이 Groq으로 질문을 해석합니다.
지침·예시 파일을 수정하면 다음 채팅 호출에 반영됩니다. 모델·키·토큰 설정은 서버 재시작이 필요합니다.

## 직접 수정할 파일

| 원하는 변경 | 파일 | 수정 방법 |
|---|---|---|
| 질문 해석 방식·말투·추가 확인 규칙 | `app/prompts/system.txt` | 한국어 일반 텍스트를 수정합니다. |
| 질문 예시·기대하는 JSON | `app/prompts/examples.json` | 기존 객체를 복사해 question과 expected를 수정합니다. |
| 확인 질문·지원 범위·미확인 안내 문구 | `app/prompts/replies.json` | 키는 유지하고 한국어 문자열만 수정합니다. |
| 허용 의도·항목·숫자 범위 | `app/chat_policy.py` | Pydantic 필드와 검증 함수를 수정합니다. 기능 계약 변경이므로 연관 코드도 확인합니다. |
| 실제 메뉴 종류 | `app/services.py`의 MENUS | ID·이름·분류 등 카탈로그를 수정합니다. 예시의 menu_id도 확인합니다. |

예시에서 `current_conditions`, `radius_m`, `selected_menu_id`는 해석에 필요한 상황 설명입니다.
실행 시에는 최신 앱 상태를 별도로 전달합니다. 예시의 expected는 정답 예시이며 실제 모델 실행 결과가 아닙니다.
예산을 바꾸는 예시를 작성할 때에는 기존 예산도 함께 적어 현재 값과 변경안이 같아지지 않도록 합니다.

## 조건 예시를 추가하는 순서

1. system.txt에 원하는 해석 규칙을 추가합니다.
2. examples.json에 질문과 기대 결과를 추가합니다. JSON은 주석·마지막 쉼표를 허용하지 않습니다.
3. 변경 조건은 condition_patch에 요청한 항목만 넣습니다. 기존 조건 전체를 복사하지 않습니다.
4. 조건 변경이면 intent는 change_conditions입니다. 적용 버튼을 누르기 전에는 검색하지 않습니다.
5. 모호하거나 지원하지 않는 요청이면 clarify와 한국어 clarification을 작성합니다.
6. 가상환경이 정상인 PowerShell에서 아래 검사를 실행합니다.

`cd C:\TeamProject_1`

`.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_chat_policy.py -v`

전체 백엔드 검사: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

## 주의할 계약

- minutes 10~120, budget 1000~100000, radius_m 50~500의 정수입니다.
- spicy는 true/false, diet는 any/vegetarian/vegan입니다.
- 식당·가격·평점·좌표를 출력 스키마에 추가하지 않습니다. 실제 검색 결과는 백엔드에서 가져옵니다.
- clarification은 clarify에만 넣고 나머지 의도에서는 null로 둡니다.
- 범위나 옵션 변경은 프롬프트만 고치면 안 됩니다. 프론트 입력, 요청 스키마, 카카오 검색 검증도 함께 맞춰야 합니다.
- 프롬프트는 자연어 해석 지침입니다. 서버 검증은 잘못된 형식을 막지만 모델의 의미 해석 정확도까지 보장하지 않습니다.
- 향후 연결 시 변경안을 표시·저장하는 코드와 확정 조건 기준 재검증을 별도로 구현해야 합니다.
- 현재 Python 실행에 실패하면 테스트가 실행된 것이 아닙니다. 오류를 먼저 해결하고 다시 검사합니다.

## 3단계: Groq 호출 모듈 설정

`app/clients/groq.py`의 `GroqClient.interpret()`가 지침과 예시를 요청마다 읽습니다.
따라서 지침·예시 텍스트 변경은 다음 호출에 반영됩니다. JSON 문법 오류가 있으면 호출을 중단합니다.
현재 `/api/chat`은 이 모듈을 사용합니다. Groq이 실패하면 오류를 안내하며 규칙 기반 답변으로 대체하지 않습니다.

`.env`의 다음 설정을 사용하며 설정 변경 후에는 서버를 재시작해야 합니다.

| 변수 | 기본값 | 의미 |
|---|---|---|
| GROQ_API_KEY | 빈 값 | 서버 전용 인증 키. 미설정 시 AI 호출만 실패합니다. |
| GROQ_MODEL | openai/gpt-oss-20b | Groq에서 사용 가능한 모델 ID |
| GROQ_TIMEOUT_SECONDS | 30 | 전체 네트워크 요청 제한시간(초) |
| GROQ_MAX_COMPLETION_TOKENS | 1024 | 한 응답의 최대 생성 토큰 수(추론 포함) |

출력 토큰은 글자 수가 아닙니다. 입력 지침·질문 토큰은 별도로 사용량에 포함됩니다.
GPT-OSS에는 reasoning_effort=low, include_reasoning=false를 사용합니다.
추론을 숨겨도 추론 토큰 사용 자체가 없어지는 것은 아닙니다.
JSON이 잘리면 groq_truncated로 구분하며 자동 재시도나 자동 토큰 증가는 하지 않습니다.
그 경우 모델 응답과 사용량을 확인하고 출력 한도를 조정합니다(현재 설정 허용 범위 128~8192).

호출 모듈 검사: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_groq_client.py -v`

Groq 키를 프론트 파일·public-config·스크린샷·테스트 결과에 넣지 마세요.

## 4단계 동작 확인

서버 재시작과 브라우저 강력 새로고침 후 위치를 설정하고 점심 고르기로 이동합니다.
"짜장면 파는 식당 보여줘"는 현재 확정 조건에 맞는 경우에만 카카오를 검색합니다.
"예산 9000원, 반경 100m로 바꿔줘"는 변경값을 표시하고 적용 버튼을 누르기 전에는 상태를 바꾸지 않습니다.
적용 후에는 새 조건으로 다시 추천을 요청하세요. 이후 다른 질문·조건 변경이 있으면 이전 제안은 적용할 수 없습니다.
"비빔밥 설명해줘"는 앱 카탈로그 설명을 사용합니다. 실제 매장 정보는 추측하지 않습니다.
Groq 30초와 카카오 검색이 순차 실행되므로 브라우저 전체 채팅 요청 대기는 45초입니다.
헤더의 Groq 표시는 요청 전·해석 중·응답 확인·실패 상태를 나타냅니다.

### 별도 연결 상태 표시

점심 고르기 화면에서 `/api/groq/status`로 인증과 설정 모델 사용 가능 여부를 확인합니다.
초록색은 연결 확인됨, 빨간색은 키·권한·모델 문제로 연결 안 됨, 주황색은 통신·사용량 등으로 확인 실패입니다.
채팅 처리 상태와 별개이며 마지막 확인 시각을 함께 표시합니다. 생성 API 성공을 보장하지는 않습니다.
채팅 생성 요청은 사용하지 않습니다. 일반 확인은 60초 캐시를 사용하고 수동 재확인도 최소 5초 간격입니다.
서버 재시작과 Ctrl+F5 후 확인하세요. API가 없다는 오류는 구버전 서버가 계속 실행 중인지 확인합니다.
상태 관련 프론트 검사: `node --test tests/groq-status.test.cjs`

## 5단계 기본 가드레일과 직접 변경하는 방법

| 규칙 | 구현 위치 | 보장 수준 |
|---|---|---|
| 점심 서비스 범위·조건 변경 의도·모호한 질문 인식 | system.txt와 examples.json | AI 해석 지침. 표현에 따라 오해할 수 있으므로 실제 질문 검사 필요 |
| 허용 필드·메뉴 ID·타입·반경·의도와 변경안 일치 | chat_policy.py | 형식과 허용값을 서버에서 검증 |
| 메뉴가 확정 음식 조건에 맞는지 확인 | services.py의 eligible_menus와 recommend | 현재 예시 카탈로그 기준 검증. 실제 매장 식이·가격 보장은 아님 |
| 반경 밖 식당 제외 | clients/kakao.py | 응답 좌표를 기준으로 거리 재계산 |
| 조건 변경 확인·오래된 제안 차단 | frontend/app.js의 applyChatPatch | 현재 화면의 확인 절차. 인증·서버 저장 동의 기록 기능은 아님 |
| 확인 답변에 매장 정보 생성 금지 | replies.json와 services.py | AI 확인 문장을 표시하지 않고 서버 안내로 대체 |
| 알레르기·할랄·시간 보장 금지 | 지침과 unsupported 안내 | 관련 요청을 인식하는 것은 AI에 의존. 실제 검증 데이터 없음 |
| 키 비공개·임의 URL 호출 금지 | 고정 클라이언트 URL·제한된 요청 구성 | 키는 인증 헤더에만 사용, 모델에 키·좌표·도구 실행 권한 미전달 |

### 안내 문구만 바꾸기

replies.json의 `radius` 값을 원하는 한국어로 바꾸세요. 키나 JSON 문법은 유지합니다.
예: "반경은 50~500m까지예요. 원하는 거리를 입력해 주세요."
`unsupported`는 검증할 수 없는 조건 안내, `notice`는 추천 끝의 미확인 안내입니다.
텍스트는 HTML로 실행되지 않으며, 빈 문자열과 500자 초과 문장은 거부합니다.
문구에서 실제 데이터가 없는 가격·평점·안전 보장을 약속하지 마세요.

### 해석을 바꾸기

예를 들어 '걸어서 5분'을 지원하지 않는 조건으로 안내하려면 system.txt에 규칙을 적고,
examples.json에 질문, intent=clarify, clarification_kind=unsupported 예시를 추가합니다.
clarification에는 짧은 확인 문장을 넣되 실제 화면은 replies.json의 안내를 사용합니다.
유형: ambiguous / radius / location / unsupported / conflict / menu.
위 유형을 새로 만들면 chat_policy.py의 Literal과 load_replies의 필수 키, replies.json도 함께 수정합니다.
기존 예시에 clarification_kind가 없으면 ambiguous 안내를 사용합니다.

### 허용 범위 자체를 바꾸기

예: 최대 반경을 늘리려면 system.txt, examples.json, chat_policy.py, schemas.py,
clients/kakao.py의 RestaurantSearch, frontend/app.js의 반경 입력·검증·applyChatPatch를 함께 변경합니다.
replies.json의 radius 안내도 바꾸고 경계값 테스트를 추가합니다.
프롬프트만 바꿔도 서버 검증 범위는 달라지지 않습니다.

### 수정 후 검사

1. 기본 검사: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
2. 실제 Groq 검사(3회 사용량 발생): PowerShell에서 `$env:LUNCHRUN_LIVE_GROQ='1'` 설정 후
   `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_guardrail_live.py -v` 실행.
3. 실행 후 `Remove-Item Env:LUNCHRUN_LIVE_GROQ`로 실제 호출 활성화를 해제합니다.
4. 서버 재시작 및 브라우저 새로고침 후 변경안 적용과 검색 결과도 직접 확인합니다.

기본 검사는 구조·서버 동작을 확인하고, 실제 검사는 모델 해석을 표본으로 확인합니다.
어느 검사도 모든 문장이나 공격에 대한 완전한 차단을 보장하지 않습니다.
단어 차단 목록은 기본으로 두지 않습니다. '매운 음식 싫어' 같은 정상 문장을 잘못 차단할 수 있기 때문입니다.

system.txt / examples.json / replies.json은 다음 호출에 반영됩니다. Python 코드와 .env는 서버 재시작이 필요합니다.
