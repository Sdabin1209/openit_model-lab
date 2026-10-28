[철회: 이미 태그 설치 가능(requirements.lock 참조). `openit-survey-schema`가 survey-schema-v1.0.0 태그로 설치된다. 아래 전제는 틀렸다.]

# CR-L010: 문진 스키마를 태그로 설치할 수 있게

## 무엇이 부족한가
이웃 폴더가 없는 새 머신에서 `bash scripts/setup-env.sh`는 통과하지만 `pytest`는 45건 실패한다(151 통과). 원인: `tests/conftest.py`와 `configs/dryrun.yaml`이 문진 스키마(`TurnTrace.json` 등과 `CONTRACT_VERSION`)를 `../survey-service/schema` 또는 `vendor/survey-schema/`에서 찾는데, `boundary_contracts` 패키지에는 이 스키마가 없다(파이프라인 스키마만 있음). 워크스페이스 규칙(의존은 Git 태그 설치 + 잠금 파일)으로는 가져올 방법이 없다.
확인한 것: 새 clone에서 `vendor/survey-schema/`에 사본을 두면 196 통과/0 실패/0 건너뜀. 확인하지 못한 것: 다른 영역이 같은 문제를 겪는지.

## 제안 (헤드 결정)
survey-service가 `schema/`를 태그가 있는 설치 가능 패키지로 배포(예: `openit_survey_schema`, `schemas/`와 `CONTRACT_VERSION`을 패키지 데이터로 포함하고 경로를 돌려주는 함수 제공)하면, model-lab은 `requirements.txt`에 태그로 추가하고 `schema_dir`를 그 함수 값으로 바꾼다. 이웃 폴더와 `vendor/` 사본이 필요 없어진다.
