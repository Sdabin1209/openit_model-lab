# 0025. `scripts/setup-env.sh`는 표준 출력 마지막 줄에 SetupEnvReport를 낸다

## 상태
채택 (2026-10-30, 헤드 브리프 U43, 계약 boundary-contracts v2.6.0 `SetupEnvReport`, OPEN_GAPS U43).

## 맥락
deploy의 `doctor.sh`가 이웃 영역의 `.venv/bin/python` 경로와 시험 도구를 짐작했다. 영역이 서로의 구조를 모른다는 규칙(D104 규칙 1)에 어긋난다.

## 결정
- 시험 환경 준비의 진입점은 그대로 `bash scripts/setup-env.sh`(멱등, 인자 없음)이고, 표준 출력의 **마지막 줄**에 `SetupEnvReport` JSON 한 줄을 낸다: `area=model-lab`, `status`(ok/fail), `exit_code`, `seconds`, `environment{kind=python-venv, path=.venv, test_command=[.venv/bin/python, -m, pytest, -q]}`.
- 진행 기록(pip 출력 포함)은 전부 표준 오류다(스크립트 첫머리에서 원래 표준 출력을 3번 fd에 보관하고 1번을 2번으로 돌린다). 보고는 EXIT trap이 내므로 실패해도 `status=fail`과 종료 코드가 나온다(실제로 requirements.lock이 없는 폴더에서 실패 보고를 확인).
- 보고 문자열은 고정 값과 숫자뿐이라 `printf`로 만든다. 비밀은 다루지 않는다.

## 왜
계약이 정했다. deploy는 `environment`만 읽고 경로·시험 명령을 짐작하지 않는다. 시험 명령은 이 영역의 시험 전체(`pytest -q`)다.

## 검토한 대안과 버리는 이유
- Python으로 다시 쓰기: 환경을 만드는 스크립트가 그 환경의 Python에 기대는 순환이 생긴다.
- 보고를 별도 파일로: 계약이 표준 출력 마지막 줄로 정했다.

## 결과
- 시험: `tests/test_setup_env.py`(마지막 줄이 계약을 통과, 표준 출력에 다른 줄이 없음, 선언한 명령이 리포 루트에서 수집 성공). 전체 실행은 선언한 명령 그대로 따로 돌려 확인했다.
- 감수: 시험이 스크립트를 실제로 실행하므로 GitHub 접근(태그 설치)이 필요하다. 시험이 자기 자신을 다시 돌리지 않도록 선언한 명령은 `--collect-only`만 붙여 실행한다.

## 되돌리는 조건
계약 3.0.0에서 형식이 바뀌면 따른다.
