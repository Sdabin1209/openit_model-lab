# 0035. models-data 백업·복구 잡

- 상태: 채택됨 (계약 boundary-contracts v3.4.16, RUNTIME R10.1·R1.8·R9.2, DATA_OWNERSHIP 백업 행)

## 맥락
데이터마다 주인 영역이 하나이고, platform 백업 저장소의 저장 공간은 platform이지만 백업 묶음의 내용·보존·복구는 데이터 주인의 몫이다. model-registry의 데이터(볼륨 `models-data`, 컨테이너 `/data`)는 이 영역의 것이므로 이 영역이 백업·복구 잡을 이미지 라벨 `io.openit.jobs`로 선언한다. 이 영역에는 DB가 없고 데이터는 파일 트리(매니페스트 JSON, 평가 리포트, 어댑터)다([0002](0002-파일시스템과-JSON-메타-저장.md)).

## 결정
1. 잡 둘(`deploy-jobs.json`, 계약 `JobsDeclaration`으로 검증): `backup`(`python -m model_lab.service backup`), `restore`(`... restore [--snapshot ID|latest]`). `needs: ["models-data"]`, `secret_files: OPENIT_BACKUP_CREDENTIALS_FILE, OPENIT_BACKUP_REPO_PASSWORD_FILE`, `env: OPENIT_BACKUP_URL, OPENIT_EGRESS_PROXY`. 새 환경 변수 이름은 만들지 않았다(계약 CommonEnv의 이름뿐). `timeout_seconds`는 3600.
2. 노출 선언 `egress`에 `external:backup`을 더했다. 백업 저장소로는 자기 영역 게이트웨이 출구(`OPENIT_EGRESS_PROXY`, restic이 읽는 표준 `HTTPS_PROXY`로 전달)로만 나간다(R1.8). 주소는 https만, loopback만 평문(R1.1). 루트 인증서는 `SSL_CERT_FILE`.
3. 마지막 줄 `BackupJobResult`(내보내기 전에 계약으로 검증), 종료 코드 0 ok, 75 재시도 가능(저장소에 닿지 못함·시간 초과), 1 그 밖(입력 없음, 비어 있지 않은 곳으로 복구 등). 비밀·저장소 주소는 결과에 없다.
4. 백업은 `/data`를 복사본 없이 바로 restic에 준다(`.tmp`·`.lock` 제외). 게시는 어댑터·리포트를 먼저, 매니페스트를 마지막에 쓰고(R10.8) restic이 디렉터리를 이름 순(`adapters`, `eval_reports`, `ids.json`, `manifests`)으로 읽으므로 스냅숏에 매니페스트가 있으면 그 어댑터·리포트도 들어 있다. 복사본을 만들면 디스크가 2배가 된다(어댑터는 GB 단위일 수 있다).
5. 복구는 빈 레지스트리(게시된 매니페스트가 없음)에만 한다. restic으로 임시 폴더에 풀고, 최상위 항목을 복사한 뒤 모든 패키지의 매니페스트 해시·평가 리포트·어댑터 해시를 다시 검증한다. 검증이 실패하면 이번에 쓴 것을 지운다(전부 또는 아무것도).
6. 보존: `restic forget --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6`. restic 문서의 표준 정책 형식이고 **값은 시작값이며 측정하지 않았다**. 모델 패키지는 불변이라 최근 스냅숏이 이전 것을 포함한다. 시간 한도: 저장소 확인(`cat config`, `init`) 60초(restic은 닿지 않는 저장소를 기본으로 분 단위 재시도한다. 시작값, 측정 안 함), 본 작업 3300초(`timeout_seconds` 3600보다 짧게).
7. 이미지에 restic(배포판 패키지)과 ca-certificates를 더했다. 스모크(`scripts/smoke_registry.sh`)가 `restic version`과 입력 없는 백업 잡의 실패 모양(종료 코드 1, 마지막 줄 `failed`)을 본다.

## 왜
- restic REST는 계약 CommonEnv가 정한 백업 저장소 프로토콜이고 restic이 그 표준 클라이언트다. 다른 DB 영역(survey-service)의 백업 잡과 같은 모양(이름, 입력, 결과, 종료 코드)이다.
- 복구 잡을 같은 이미지에 둔 이유: 복구는 데이터 주인의 몫이고 검증 코드가 레지스트리 코드와 같은 모듈이다.

## 검토한 대안과 버리는 이유
- `/data`를 복사한 뒤 백업: 디스크 2배. 쓰기 순서가 이미 일관성을 보장한다.
- 잠금(`.lock`)을 잡고 백업: 어댑터가 클 때 게시가 오래 막힌다. 쓰기 순서로 충분하다.
- 비어 있지 않은 곳에 덮어쓰기 복구: 불변 패키지를 바꿀 수 있어 거부한다.

## 결과와 한계
시험(`tests/test_backup_job.py`): 선언을 계약 validator로, 실제 restic + 표준 rest-server 컨테이너로 백업→복구 왕복(패키지·어댑터·리포트 일치, 제외 항목 없음, 비밀 없음), 비어 있지 않은 곳 거부, 스냅숏 ID 복구, 없는 스냅숏 75, 닿지 않는 저장소 75, 검증 실패 시 흔적 없음, 평문 원격 주소 거부. 변이 확인 8건. 한계: 수 GB 규모와 실제 platform 백업 저장소·영역 게이트웨이 출구로는 시험하지 않았다(deploy 스택에서 확인). 복구 중간에 프로세스가 죽으면 부분 복사가 남을 수 있고 이 경우 다시 실행하면 "비어 있지 않음"으로 거부되므로 `/data`를 비우고 다시 한다(runbook).

## 되돌리는 조건
백업 저장소 프로토콜이나 데이터 저장 방식(DB 도입)이 바뀌면 새 ADR로 대체한다.
