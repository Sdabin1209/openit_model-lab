# 0020. package-adapters의 created_at: --created-at > SOURCE_DATE_EPOCH > 거부

## 상태
채택 (2026-10-29, T22 후속, 헤드 지시). ADR 0014의 "기본값 1970-01-01" 부분을 대체한다(Supersedes 0014의 기본값 결정).

## 맥락
ADR 0014는 mtime 대신 입력에서만 시각을 받고, 없으면 1970-01-01T00:00:00Z를 썼다. 그 값은 실제 시각이 아니면서 유효한 시각처럼 매니페스트에 남는다(시각을 지어내는 것).

## 결정
우선순위: `--created-at` 인자 > 환경 변수 `SOURCE_DATE_EPOCH` > 둘 다 없으면 `ConfigError`(CLI 종료 코드 2)로 거부하고 아무것도 만들지 않는다. 거부 메시지에 두 가지 주는 법을 쓴다. `SOURCE_DATE_EPOCH`가 있으나 10진 정수가 아니면(빈 값, 음수, 소수, 날짜 문자열) 무시하지 않고 거부한다. 구현: `attach.resolve_created_at`.

## 왜
- 표준 관행: reproducible-builds.org의 `SOURCE_DATE_EPOCH` 규격. 값은 UTC 에포크 초의 10진 정수이고, 형식이 틀리면 빌드를 실패시켜야 한다.
- 산출물에 파일 시스템 상태나 실행 시각을 넣지 않으면 같은 입력이 같은 `content_hash`를 낸다(ADR 0014).
- 모르는 시각을 지어내지 않는다(워크스페이스 규칙: 확인하지 못한 것을 확인한 것처럼 쓰지 않는다).

## 검토한 대안과 버리는 이유
- 1970 기본값(ADR 0014): 위 이유로 버린다.
- 현재 시각: 실행마다 해시가 달라 멱등이 깨진다.

## 결과
얻는 것: 해시가 결정적이고 시각이 사실이거나 없다. 감수하는 것: 호출자가 항상 시각을 줘야 한다. 같은 패키지를 다시 만들 때 같은 시각을 줘야 같은 `content_hash`가 나온다. 시험: `tests/test_source_date_epoch.py`(기대값은 규격에서 도출), 다른 시험은 `tests/conftest.py`의 autouse 픽스처가 고정 `SOURCE_DATE_EPOCH`를 준다. 이 리포 안에서 `package-adapters`를 부르는 곳은 CLI, runbook, README, `train`의 다음 단계 안내뿐이었고 모두 맞췄다.

## 되돌리는 조건
학습 기록이 학습 시각을 담게 되면 그 값을 `--created-at`의 기본 출처로 추가하는 새 ADR을 쓴다.
