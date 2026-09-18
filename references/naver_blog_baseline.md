# HAMZZU_NAVER_REFERENCE_V1

This file is a mandatory production input for every new `naver_blog` run. It is not optional memory and must be loaded from the checked-out repository before generation and rendered-card QA.

## Canonical published references

1. https://blog.naver.com/himemi0412/224412841652 — 전기장판·라텍스: 짧은 질문형 도입, 앞부분의 분명한 금지 결론, 부드러운 생활 장면과 넓은 여백, 마지막 행동 세 가지.
2. https://blog.naver.com/himemi0412/224408237891 — 식기세척기 설치: 기본정보 카드, 실제 치수와 설치 맥락, 측정·하부장·연결·마지막 체크로 역할이 다른 카드 5장.
3. https://blog.naver.com/himemi0412/224404549601 — 정수기 철거비: 비용 질문에 조건부 답부터 제시하고 계약 상황·청구 항목·작업 범위·문의 문장으로 좁히는 구성.
4. https://blog.naver.com/himemi0412/224398911774 — 렌탈제품 판매: 소유권 결론을 먼저 밝히고 확인 순서·서비스 분리·최종 체크리스트로 이어지는 구성.

특정 카드의 문구·그림·구도·로고를 복제하지 않는다. 네 글에서 반복 확인되는 편집 원리와 햄쮸의 문장 리듬만 새 주제에 맞게 적용한다.

## Required writing baseline

- 한 글은 독자가 실제로 검색할 질문 하나에 답한다. 첫 2~4문장에서 조건이 붙은 답을 먼저 준다.
- `헷갈리죠`, `당황스럽잖아요`, `먼저 확인해요`, `오늘 할 일은`처럼 자연스럽고 현실적인 연결을 사용한다. 조사 글을 사용 후기처럼 꾸미지 않는다.
- 필요한 경우 `୨୧ 기본정보 ୨୧`와 아이콘·`│` 한 줄 항목을 사용한다. 모든 글에 기계적으로 강제하지 않는다.
- 소제목은 사양명이 아니라 독자의 다음 질문 또는 판단 단계가 되게 한다.
- 본문 흐름은 `질문 상황 → 앞부분 답 → 조건 구분 → 확인 순서/비교 → 오늘 할 행동`을 기본으로 하되 주제에 맞게 줄인다.
- 출처 URL·확인일·근거는 `fact_check_notes`와 카드 `source_urls`에 보존한다. 사용자가 별도로 요구하지 않은 공개 본문 끝의 `공식 확인 링크` 또는 `출처 및 확인 기준일` 섹션은 만들지 않는다.
- 이모티콘·`ㅎㅎ`·`:)`는 자연스러운 전환에만 소량 사용하고 제목·수치·모델명·안전 조건에는 넣지 않는다.

## Required card-news baseline

- 정확히 5장을 만들되 같은 텍스트 상자 틀을 다섯 번 반복하지 않는다. 대표 질문, 원리/이유, 조건 비교, 행동 확인, 마지막 판단처럼 각 장의 역할과 장면이 달라야 한다.
- 카드마다 생활 장면·사물·제품 맥락·측정 도식·계약서·체크리스트 중 최소 하나가 정보를 실제로 설명해야 한다.
- `square`와 `landscape_4_3`을 모두 허용한다. 한 세트 안에서는 하나로 통일하고, 주제와 정보 밀도에 맞게 선택한다.
- `soft_scene`, `playful_diagram`, `contract_notebook`, `clipboard_checklist` 중 주제에 맞는 한 시각 계열을 선택한다. 계열은 고정 템플릿이 아니라 색·여백·선·정보 장치의 방향이다.
- `예쁘게 만들어줘`처럼 추상적으로 지시하지 않는다. 주제와 정보 구조에 맞는 구체적인 디자인 언어 이름을 하나 선택하고, 그 이름에 묶인 타이포그래피·그리드·사진/도식/UI 요소·여백·색상/질감을 실제 렌더에 적용한다.
- 허용 디자인 언어는 Swiss Typography, Retro Tech UI, Bento Editorial, Monochrome Magazine, Neo Brutalism, Japanese Editorial, Quiet Luxury Editorial, Newspaper 2.0, Technical Manual, Screenshot Editorial, Prompt Playground, Terminal Noir, Fluorescent Minimal, Soft Swiss, Index / Catalogue, Cinematic Title Card, Zine Collage, Split Screen, Chrome Accent, Modular Poster다. 한 세트에는 하나만 선택하며 스타일 이름만 바꾼 둥근 상자형 반복은 실패다.
- 흰색은 `#FFFFFF`, 제목은 Cafe24 Ssurround, 작은 본문은 읽기 쉬운 고딕을 사용한다. 한 장은 중립 배경 외 주조색 1개와 포인트 1~2개, 전체 세트는 3~5색 안에서 반복한다.
- 큰 질문과 핵심 사물이 모바일 축소 화면에서 먼저 보여야 한다. 긴 설명은 본문으로 보내고 카드에는 짧은 판단 정보만 둔다.
- 실제 제품 정체성이 중요하면 권리가 확인된 실제/공식 이미지를 우선한다. 불확실하면 무브랜드 설명 도식을 사용하고 실제 제품 사진처럼 설명하지 않는다.

## Fail-closed review contract

최종 QA는 `REFERENCE_PASS`, `CONTENT_PASS`, `EDITOR_FORMAT_PASS`를 별도로 판단한다. 이 문서의 ID와 SHA-256이 실행 manifest에 없거나, 위 네 URL이 로드되지 않았거나, 원고·실제 렌더 카드가 위 기준과 명백히 어긋나면 `REFERENCE_PASS=false`이며 Notion `네이버 저장 요청`으로 넘기지 않는다.
