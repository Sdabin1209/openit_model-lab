# CR-L007 학습 서버 데이터셋을 샘플로 옮기며 생긴 질문 (수신: 헤드)

기준: boundary-contracts v2.0.0, 학습 서버 `dataset_10_14`(읽기 전용으로 확인). 아래 "확인"은 이 세션에서 직접 읽고 시험한 것이다.

## 확인한 것
- 서버 줄 10,264개(LoRA-A 5,077 = 3,982/549/546, LoRA-B 5,187 = 4,069/560/558) 전부 `ActionExtractionSample`/`ResponseSample`으로 검증을 통과하고, 서버 줄로 되돌렸을 때 messages가 글자 단위로 같다(`scripts/check_server_dataset.py`).
- 서버 줄의 필드는 `scenario_id`, `split`(train/val/test), `human_review`, `messages`뿐이다. `sample_id`, `origin`, `question_set`이 없다.
- 시스템 프롬프트는 역할마다 한 종이다(A 927자, B 281자). 분할 사이에 걸친 `scenario_id`는 없다. LoRA-B에는 같은 `scenario_id`가 둘 이상인 줄이 있다(train 5그룹, val 1그룹).
- LoRA-A 입력의 `state` 키는 정렬되어 있지 않다(5,077줄 중 1,014줄). 최상위 키는 정렬되어 있고 `state`의 각 항목은 `value`, `status` 순서다(알파벳 순이 아니다).

## 정해 달라 / 알려 달라
1. **시스템 프롬프트의 출처.** `*Sample`에는 프롬프트 필드가 없고 `DatasetManifest`에도 없다. 지금은 설정의 `roles.<역할>.system_prompt_file`(리포 `prompts/`에 서버 데이터의 system 메시지를 그대로 복사)로 받는다[제안, 임시]. 데이터셋과 프롬프트가 짝이므로 `DatasetManifest`가 프롬프트(경로·해시)를 담는 편이 맞다고 보지만 계약 변경이라 판단을 맡긴다.
2. **`sample_id` 규칙.** 서버 데이터에 없어 `{scenario_id}-{입력·정답 해시 12자}`로 만들었다[제안]. 데이터셋을 게시하는 쪽(synthetic-data의 역할이 미정)이 따로 정하면 그 값을 쓴다.
3. **`origin`.** 서버 줄에 구분이 없어 전부 `SYNTHETIC`으로 둔다[가정].
4. **`state` 키 순서와 서빙.** 학습 글자는 `state`의 삽입 순서를 담고 있다. 이 순서는 JCS 정렬을 거치면 사라지므로 서빙의 `render_prompt`(model-runtime)가 `state`를 어떤 순서로 쓰는지에 따라 학습 글자와 서빙 글자가 달라질 수 있다. model-lab은 샘플 파일에서 순서를 보존한다. model-runtime이 쓸 규칙을 알려 달라[미정].
5. **`question_set`.** 서버 데이터에 문항 세트 개념이 없어 샘플의 `question_set`을 비운다. 그러면 "보지 못한 문항 세트" 보고(D32)의 기준이 `DatasetManifest.files[].question_sets`뿐이다. 서버 데이터셋에서 문항 세트를 어떻게 나눌지[미정].
