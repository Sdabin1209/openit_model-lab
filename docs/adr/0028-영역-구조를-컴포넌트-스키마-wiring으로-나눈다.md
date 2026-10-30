# 0028. 영역 구조를 schemas / types / components / wiring으로 나눈다 (D117)

## 상태
채택 (2026-10-30, 헤드 지시서 U400, `head/docs/DESIGN.md`). 동작(와이어 형식, HTTP 응답, 잡 출력)은 바꾸지 않는다.

## 맥락
기존 `model_lab/`은 모듈이 서로 import하고(`pipeline` -> `evaluate`, `metrics` -> `evaluate`, `attach` -> `packager`, `registry`, `train_wrap`), 거부 조건이 코드 곳곳의 `if`/`raise`에 있었다. 구조 규칙 6개(D104)는 컴포넌트가 자기 입출력 스키마 밖을 모르게 하라고 한다.

## 결정
- 컴포넌트를 계약과 브리프의 업무 단계에서 위로부터 나눈다(`docs/components.md`). 컴포넌트는 `components/`에 파일 하나, 서로 import하지 않는다. 바깥 자원(저장소 클라이언트, 모델 세션, 다른 컴포넌트의 함수)은 wiring이 주입한다.
- 영역 내부 타입은 `schemas/`의 JSON Schema 한 곳이 정본이고 `types/`는 datamodel-code-generator(TypedDict)로 생성한다. 계약을 가리키는 `$ref`는 파일 이름 그대로 쓰고 `wiring/schemas.py`가 설치된 계약 패키지로 푼다.
- 영역 공용인 스키마 적재(`wiring/schemas.py`)와 오류 어휘(`wiring/errors.py`)는 wiring에 둔다. 컴포넌트가 이 둘을 import하는 것은 허용한다(규칙 5: 컴포넌트 사이의 스키마는 영역이 책임진다).
- 합친 것: 채점(`evaluate`+`text_rules`+`flow`)은 `eval_sample`, 보고·지표(`evaluate` 집계+`metrics`+`metric_names`+`regression.section`)는 `eval_report`. 이유: 같은 합계와 쪼개기 차원을 쓰는데 컴포넌트끼리 import할 수 없어 쪼개면 복사해야 한다.
- 주입한 것: `lora_train_hf`의 `load_base`를 HF 백엔드 둘에, `check_adapter`를 `server_train_wrap`에, `layout_of`/`verify_system_prompt`를 `package_build`에, 파일 레지스트리를 `link_registry_serve`에, `cached_commit`을 `lora_train_hf`에.
- 보내는 쪽 통신(`link_dataset_fetch`, `link_failure_read`, `link_registry_publish`)은 `openit_link.clients`(link v0.1.0, D118)로 클라이언트를 만든다. 받는 쪽 `link_registry_serve`는 link의 `App`이 본문 전체를 메모리에 읽어(최대 4GiB 스트리밍 게시를 못 한다) 아직 옮기지 못했다: `docs/change-requests/CR-L013-link-큰-본문-스트리밍과-서버-보호.md`.

## 왜
D117/D104가 정한 구조다. 대안 비교는 헤드의 DESIGN.md에 있다. 이 영역의 몫인 선택(합치기, 주입, errors 위치)의 근거는 위와 같다.

## 검토한 대안과 버리는 이유
- 컴포넌트를 더 잘게 나누기(채점/지표/회귀 절 각각): 공용 합계를 세 곳에 복사해야 해서 어긋날 위험이 크다.
- `errors.py`를 `types/`에 두기: `types/`는 생성물이라 손으로 쓰는 파일을 둘 수 없다.
- link `App`으로 registry 서버를 오늘 옮기기: 4GiB 스트리밍 게시, 동시 게시 상한, 인증 실패 제한을 잃어 동작이 바뀐다(시험 `test_server_properties`, `test_hardening`이 고정).

## 결과
- 얻는 것: 입력 검사가 `schemas/`(RunConfig, SecretFree, RunPlan, EvalServingPlan, TrainWrapConfig, ServerDatasetRow, DatasetRef, TrainRequest)로 모였다. 구조는 `tests/test_structure.py`가 강제한다.
- 감수하는 것: 스키마로 옮기지 못한 조건이 남았다(`schemas/README.md` 표). 받는 쪽 HTTP 규약 구현이 영역 코드에 남아 있다(CR-L013).

## 되돌리는 조건
D117이 바뀌거나, link가 스트리밍 본문을 지원하면 `link_registry_serve`를 link 위로 옮기고 이 ADR의 해당 항목을 새 ADR이 대체한다.
