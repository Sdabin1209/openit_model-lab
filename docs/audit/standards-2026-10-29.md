# model-lab 표준성·근거 전수 점검 (2026-10-29, D89)

기준서: `head/docs/audit/standards-audit-spec.md`. 점검 방식은 읽기 전용이다. 코드·설정·시험을 고치지 않았다. 비밀 값(환경 변수, 토큰, 키)은 읽거나 출력하지 않았다. 실행한 것은 PyYAML 해석 확인 한 번(`.venv/bin/python -I`, 인라인 문자열만)이다. 그 밖의 항목은 코드와 문서를 읽어 판단했다.

읽은 것: `CLAUDE.md`, `README.md`, `docs/design.md`, `docs/adr/0001~0013`, `docs/runbook.md`(일부), `model_lab/` 전체 중 아래 증거 열의 파일, `Dockerfile`, `requirements*`, `pyproject.toml`, `scripts/setup-env.sh`, `scripts/smoke_registry.sh`, `configs/train.yaml`, `configs/gpu-dev.yaml`, `profiles/gpu-dev-12g.yaml`. 읽기 허락된 `boundary-contracts`의 `CONVENTIONS.md`, `HTTP.md`, `canonical.py`는 해시·Id·목록 규약 확인에만 썼다.

분류: S = 표준 + 근거, S- = 표준이나 근거 기록 없음, N = 표준 아님 + 근거(대안 비교) 있음, N- = 표준 아님 + 근거 없음, O = 가져다 쓸 것을 직접 만듦, ? = 표준 여부 확인 못 함.

표의 "근거"는 이 리포 안의 기록 위치(ADR, D번호, 주석)다. "없음"은 찾지 못했다는 뜻이다. 줄 번호는 점검 시점(HEAD a4c3814) 기준이다.

## 총괄

| 분류 | 개수 |
| --- | --- |
| S | 30 |
| S- | 12 |
| N | 34 |
| N- | 31 |
| O | 3 |
| ? | 5 |
| 합계 | 115 |

주요 관찰(자세한 내용은 표와 끝의 목록):
- 값(숫자) 대부분이 측정 없이 정해졌다. ADR 0007이 스스로 "측정하지 않은 시작값"이라고 밝혔고 N-가 많은 이유다. ADR 밖의 값(클라이언트 timeout 300초, HEALTHCHECK 간격, 학습기 기본 하이퍼파라미터, 시드 범위 등)은 근거 기록 자체가 없다.
- 평문 HTTP 위의 Bearer 토큰(H12)은 알려진 열린 항목이다(U5/ML-14). 근거는 "결정 대기"이고 해소된 것이 아니다.
- 직접 만든 것 3건(O): 서비스 운영 기능(연결 상한·graceful drain 등), 파이프라인용 학습 루프, 파이프라인 단계 캐시·run 기록.
- 설정 해석에서 YAML 1.1 함정이 실제로 확인됐다(C2). 문서에 대응이 없다.
- `package-adapters`의 `created_at`이 어댑터 파일 mtime이라 복사 방법에 따라 같은 어댑터가 다른 패키지가 될 수 있다(T22).

## 1. 레지스트리(I3)·저장·해시 형식

| ID | 요소 | 분류 | 표준 | 근거 | 증거 |
| --- | --- | --- | --- | --- | --- |
| R1 | 저장소는 파일 시스템 + JSON 메타, DB 없음 | N | 표준 없음 | ADR 0002(대안 SQLite·PostgreSQL을 사후에 비교, 당시 비교 기록 없음 명시, 용량·처리량 측정 안 함) | `registry.py:208-217`, `registry.py:266-289` |
| R2 | I3 레지스트리를 직접 구현(MLflow·HF Hub·OCI 대신) | N | 표준 없음(OCI Distribution 등은 대안) | ADR 0001. 사후 비교이고 외부 제품 사양은 "이번에 확인하지 않았다(추정)"고 ADR이 밝힘 | `registry.py:60-160`, ADR 0001 "검토한 대안" |
| R3 | 원자적 쓰기: 임시 파일 `fsync` 후 `os.replace`, 매니페스트를 마지막에 씀 | S | POSIX `rename(2)` 원자성 | ADR 0002 "왜". 디렉터리 `fsync` 생략은 ADR이 인정 | `registry.py:241-248`, `registry.py:288-289` |
| R4 | `fcntl.flock`으로 게시 직렬화(단일 호스트 전제) | S | `flock(2)`(Linux/BSD 문서) | ADR 0002, 0007 | `registry.py:219-228` |
| R5 | `content_hash` = JCS 정규화 JSON의 SHA-256, 게시·조회 때 재계산 | S | RFC 8785(JCS) + SHA-256 | ADR 0003, CONVENTIONS 2절 | `registry.py:88-93`, `registry.py:120-121` |
| R6 | `adapter_hash` = `directory_hash`(파일별 {경로, sha256} 목록을 경로순 정렬해 JCS 해시) | N- | 이름 있는 표준 아님(OCI digest·Git tree 등과 다름) | 이 영역 기록 없음. 계약(헤드 소유)이 정함: CONVENTIONS 18행은 "기준 구현 하나"만 말하고 방식 선택의 대안 비교는 보지 못함 | `boundary_contracts/canonical.py:116-125`, `registry.py:100-101`, ADR 0003 |
| R7 | 읽는 쪽 재검증(`open_adapter`가 풀고 나서 해시 재계산) | S | CONVENTIONS 2절(계약). 외부 표준 이름은 없음 | ADR 0003 "왜" | `registry.py:130-140` |
| R8 | 서명 없음(자기일관성 해시뿐) | N | 대안으로 제안된 것은 minisign/`ssh-keygen -Y sign`(runbook) | ADR 0003: 키 보관 주체 미결정이라 구현 안 함, 한계 명시 | ADR 0003, `docs/runbook.md:176` |
| R9 | Id 규칙 `[A-Za-z0-9][A-Za-z0-9._:-]*` 최대 128자, 경로 탈출 방지 | S | 계약 CONVENTIONS.md:100 | 계약에 이유("파일 경로에 쓸 수 있어야") | `registry.py:25-30`, `registry.py:250-264` |
| R10 | 목록 페이지네이션: 정수 offset을 cursor 문자열로 | S | 계약 HTTP.md:9, CONVENTIONS.md:47 | 계약 | `registry.py:148-160` |
| R11 | 디렉터리 배치(`manifests/<id>/<ver>.json`, `_order.json`, `ids.json`, `adapters/`, `eval_reports/`) | N | 표준 없음 | ADR 0002 결정. 버전 표지가 없어 배치 변경 수단이 없다는 한계도 ADR이 적음 | `registry.py:230-231`, `registry.py:266-289` |
| R12 | 게시자 기록 `<ver>.published.json`(역할·주소·시각) | N | 표준 없음 | ADR 0003 결과, runbook ML-6 | `registry.py:285-287` |
| R13 | 시작 때 `.tmp-*` 잔해 정리(`recover`) | N | 표준 없음 | ADR 0002(ML-3 결함 수정 기록) | `registry.py:309-318`, `service/app.py:99-106` |
| R14 | 보존 기간·삭제·백업: 정책 없음(불변 보관만, `runs/`도 정리 규칙 없음) | N- | 표준 확인 못 함 | ADR 0002는 "백업은 디렉터리 복사"만. 보존 기간과 용량은 측정·결정 기록 없음 | ADR 0002 "결과", `.gitignore`의 `runs/` |
| R15 | 설정의 `package_id` 불변(이름이 현재 용어와 달라도 유지) | S | 식별자 불변 관행(ADR이 명시) | ADR 0013 | `config.py:116`, ADR 0013 |
| R16 | 메모리 구현(가짜)을 같은 규칙 코드 위에 함께 배포 | S | 워크스페이스 규칙(D22) | ADR 0001 | `registry.py:163-205` |

## 2. HTTP 서비스·인증·컨테이너

| ID | 요소 | 분류 | 표준 | 근거 | 증거 |
| --- | --- | --- | --- | --- | --- |
| H1 | 서버 구현: 표준 라이브러리 `ThreadingHTTPServer`(프레임워크·WSGI 서버 없음) | N | 표준 없음. Python 문서가 `http.server`를 운영용으로 권하지 않는다고 알고 있으나 이번에 원문은 확인하지 못함 | ADR 0010(직접 기록 없음, 사후 판단, 이득은 "측정 안 함") | `service/app.py:31`, `service/app.py:392-399` |
| H2 | 연결 상한, 우아한 종료·drain, 느린 클라이언트 방어, 거절 전 본문 버리기를 직접 구현 | O | gunicorn·uvicorn 등 서버가 제공하는 기능 | ADR 0010·0007이 "의존을 늘리지 않으려고"라고 쓰나, 대안 서버로 같은 기능을 시험한 기록은 없음. 직접 유지 비용은 ADR 0001이 인정 | `service/app.py:392-460`, `service/app.py:40-44` |
| H3 | 오류 코드 → HTTP 상태 매핑(400/403/404/409/412/422/503) | S- | 계약 HTTP.md 9절, RFC 9110 상태 의미 | 계약. 이 영역 문서에 선택 이유 없음 | `service/app.py:40` |
| H4 | `/healthz`, `/readyz` | S | 워크스페이스 규칙(D22), 컨테이너 probe 관행 | runbook, 워크스페이스 규칙 | `service/app.py:249-251`, `service/app.py:116-124` |
| H5 | 인증: `Authorization: Bearer`, 역할=토큰, 읽기/쓰기 분리 | S | RFC 6750(Bearer). ADR은 이름 없이 HTTP.md만 인용 | ADR 0009, HTTP.md:11 | `service/app.py:137-157` |
| H6 | 토큰 비교 `hmac.compare_digest`(시간 일정) | S | Python `hmac.compare_digest` 문서의 권고 | ADR 0009 | `service/app.py:137-146` |
| H7 | 토큰 발급·회전·폐기: 사람이 무작위 값을 만들어 파일에 넣고, 회전은 재시작, 폐기 수단 없음 | N- | 표준 확인 못 함(비밀 관리 도구·단기 자격이 일반 관행이라는 점만 앎) | ADR 0009 결과가 "정적, 재시작 필요"를 감수한다고만 적음. 대안 비교 없음 | `service/app.py:5-6`(머리말), ADR 0009 "결과" |
| H8 | 비밀을 `NAME_FILE`(`/run/secrets` 마운트)로 받음 | S | Docker secrets 파일 관례. 워크스페이스 D84 | D84(env 출력 노출 사고) | `service/app.py:3-7`, `scripts/smoke_registry.sh:19-27` |
| H9 | 토큰 최소 길이 32자 | ? | OWASP 세션 관리 치트시트와 NIST SP 800-63B를 ADR이 인용하나 "원문을 다시 확인하지 않았고 기억한 수준"이라고 ADR이 밝힘 | ADR 0009, `app.py` 주석(65-68행 부근) | `service/app.py:67`, `service/app.py:70-77` |
| H10 | 토큰 "서로 다른 글자 8개 이상" 검사 | N- | 표준 없음 | 근거 기록 없음(ADR은 길이만 설명) | `service/app.py:75` |
| H11 | 인증 실패 제한(주소별 슬라이딩 윈도): 직접 구현, 한도 초과 시 503 | N | 속도 제한의 표준 응답은 429(RFC 6585)이나 계약이 503 사용 | ADR 0007·0009("무차별 대입 방어가 아니라 로그·CPU 보호", 라이브러리 비교는 한 줄) | `service/app.py:122-133`, `service/app.py:265` |
| H12 | TLS 없는 평문 HTTP 위에서 Bearer 사용 | N | RFC 6750은 Bearer를 TLS 위에서 쓰라고 요구한다고 알고 있으나 조문은 이번에 확인하지 못함 | ADR 0009·0010, runbook ML-14: TLS 종료 지점이 미정(U5). 해소된 것이 아니라 결정 대기 | `service/app.py:488`, `docs/runbook.md:177` |
| H13 | 쓰기 역할은 `model-lab` 하나, 읽기 토큰의 쓰기는 403 | S | 최소 권한 원칙(NIST SP 800-53 AC-6) | ADR 0009, 브리프 6절 | `service/app.py:42`, `service/app.py:79-83`, `service/app.py:147-156` |
| H14 | tar 전송, 풀 때 `..`·절대 경로·비일반 파일·총 크기 검사를 직접 구현(`tarfile` 추출 필터 대신 항목별 복사) | S- | 계약 HTTP.md:56(tar 전송), 경로 탈출 방지 관행(CWE-22). Python 3.12의 추출 필터(PEP 706)는 쓰지 않음 | 모듈 docstring이 HTTP.md를 가리킬 뿐, 직접 해제를 고른 이유 기록 없음 | `service/tarutil.py:24-49` |
| H15 | tar 만들 때 uid/gid=0, mtime=0, 이름순 정렬 | S- | 재현 가능한 아카이브 관행. 표준 이름은 확인 못 함 | 근거 기록 없음 | `service/tarutil.py:10-17` |
| H16 | 로그는 표준 출력, 경로만(쿼리·토큰·본문 제외) | S | 12-factor XI(로그), 워크스페이스 규칙 | 서버 머리말, ADR 0009 | `service/app.py:16`, `service/app.py:157-158` |
| H17 | 컨테이너: 비root(uid 10001), `HEALTHCHECK`, 라벨 6종, 빌드 인자 없으면 실패 | S | Docker 모범 사례, 워크스페이스 D22·D50 | `Dockerfile` 주석("왜") | `Dockerfile:22-43` |
| H18 | 기반 이미지 `python:3.12-slim`을 태그로만 지정(다이제스트 고정 아님) | ? | 다이제스트 고정이 표준 관행인지 이번에 문서로 확인하지 못함 | 근거 기록 없음 | `Dockerfile:10`, `Dockerfile:20` |
| H19 | 빌드 단계에서 `ssh-keyscan github.com >> known_hosts \|\| true`(호스트 키를 처음 본 값으로 신뢰, 실패해도 계속) | N- | 표준 아님(호스트 키 지문 고정 또는 HTTPS가 일반 대안이라는 점만 앎) | 근거 기록 없음 | `Dockerfile:15` |
| H20 | 비공개 태그 의존을 BuildKit `--mount=type=ssh`로 설치, 태그 고정 | S | Docker BuildKit SSH 마운트 문서, 워크스페이스 규칙(태그 설치) | `Dockerfile` 머리말 주석 | `Dockerfile:1-7`, `Dockerfile:17-18` |
| H21 | 동시성 모델: 연결당 스레드, 게시는 세마포어 1 + 파일 잠금 | N | 표준 없음 | ADR 0010·0007(대기열 대신 즉시 503, 사유 서술) | `service/app.py:79-95`, `service/app.py:392-399` |

## 3. 값(timeout·한도·기본값)의 출처

"측정"은 이 리포에서 부하·용량을 재어 얻은 값을 뜻한다. 아래 표에서 측정으로 얻은 값은 하나도 없다.

| ID | 값 | 분류 | 표준 | 값의 출처 | 증거 |
| --- | --- | --- | --- | --- | --- |
| V1 | 모델 내려받기 예산 `max_footprint_gb: 20` | N- | 없음 | ADR 0007: "근거 기록 없음, 측정 안 함" | ADR 0007 표 1행(`configs/compare/*.yaml`은 열지 않음) |
| V2 | 학습 전 `min_free_gb` 기본 30 | N- | 없음 | 코드 주석: "근거 기록 없음, 측정 안 함, 출처 커밋 못 찾음" | `train_wrap.py:195`, `train_wrap.py:232` |
| V3 | 게시 여유 = 본문 2배 | N | 없음 | 제약에서 도출(본문 tar와 풀어 둔 사본이 동시에 있는 순간). 측정 아님 | `service/app.py:109-111` |
| V4 | 게시 여유의 가산 1GB | N- | 없음 | 근거 기록 없음 | `service/app.py:466` 부근 주석 |
| V5 | 본문 상한 4GiB | N- | 없음 | "어댑터 크기의 여유값, 근거 기록 없음, 측정 안 함" | `service/app.py:468`, `service/app.py:478` |
| V6 | 소켓 timeout 30초 | N- | 없음 | "정상 클라이언트가 1MiB 조각을 잇는다는 가정, 측정 안 함" | `service/app.py:462-465`, `service/app.py:479` |
| V7 | 동시 연결 32 | N- | 없음 | 측정 안 함 | `service/app.py:480` |
| V8 | 동시 게시 1 | N | 없음 | 파일 잠금으로 어차피 직렬화된다는 제약에서 도출 | ADR 0007, `service/app.py:481` |
| V9 | 인증 실패 10회/60초/주소 | N- | 없음 | ADR 0007: "표준 값이 아니다, 측정 안 함" | `service/app.py:482` |
| V10 | 종료 유예 8초 | N | Docker 기본 정지 유예 10초(문서 사실)에서 상한을 도출 | 8 자체는 측정 안 함 | `service/app.py:467`, `service/app.py:484` |
| V11 | 거절 전 본문 버리기 1MiB, 0.25초 | N- | 없음 | 둘 다 "근거 기록 없음(측정 안 함)" | `service/app.py:43-44` |
| V12 | HTTP 클라이언트 timeout 300초(데이터셋·레지스트리) | N- | 없음 | 근거 기록 없음 | `configs/gpu-dev.yaml:11`, `configs/gpu-dev.yaml:29` |
| V13 | `HEALTHCHECK` 간격 15초, timeout 3초 | N- | 없음 | 근거 기록 없음 | `Dockerfile:34` |
| V14 | 설치 재시도 1회, 대기 10초 | N- | 없음 | 재시도 1회는 "일시적 네트워크 실패" 주석, 10초는 근거 없음 | `scripts/setup-env.sh:22-26` |
| V15 | 하위 프로세스 timeout: GPU 조회 15초, doctor 의존 점검 60초 | N- | 없음 | 근거 기록 없음 | `train_wrap.py:180`, `train_wrap.py:316` |
| V16 | 운영 절차의 `flock -w 1800`, `timeout 3000`, `ConnectTimeout=15` | N- | 없음 | 근거 기록 없음 | `docs/runbook.md:72`, `docs/runbook.md:80` |
| V17 | 시드 범위 0 이상 2^31-1 이하 | N- | 없음 | 근거 기록 없음 | `config.py:94` |
| V18 | 프로파일 하이퍼파라미터(r 16, alpha 32, dropout 0.05, lr 2e-4, warmup 0.03, 메모리 비율 0.85, 체크포인트 25스텝 등) | N- | 없음 | 프로파일 머리말이 "파이프라인 검증용, 성능 근거 아님"이라고만 적음. 값 근거 기록 없음 | `profiles/gpu-dev-12g.yaml:1-26` |
| V19 | `configs/train.yaml` 하이퍼파라미터(lr 2e-4, 3 epoch, batch 2, accum 4, max_len 4096, seed 42, r 16 등) | N | 없음 | 출처는 서버 정본의 실제 `run_config.json`(헤드가 읽음). 선정 근거는 서버(학습 담당) 쪽에 있고 이 리포에는 없음. 시험이 명령줄을 대조 | `configs/train.yaml:5`, `configs/train.yaml:44-56` |
| V20 | 학습기 코드 안의 기본값: `AdamW`, weight_decay 0, 기울기 클립 1.0, LoRA r 16·alpha 32·dropout 0.05·대상 모듈 4종, lr 2e-4, 체크포인트 50스텝 | N- | 없음(QLoRA 논문류의 관례값과 비슷하나 인용 없음) | 근거 기록 없음 | `trainers/hf.py:114-117`, `trainers/hf.py:151-154`, `trainers/hf.py:168`, `trainers/hf.py:184` |
| V21 | 데이터 순서 시드 `seed * 1009 + epoch` | N- | 없음 | 1009의 이유 기록 없음 | `trainers/hf.py:146` |
| V22 | 파일 읽기·내려받기 조각 1MiB | N- | 없음 | 근거 기록 없음 | `datasets.py:51`, `service/tarutil.py:46`, `train_wrap.py:171` |

## 4. 학습 파이프라인·서버 연동·패키징·의존

| ID | 요소 | 분류 | 표준 | 근거 | 증거 |
| --- | --- | --- | --- | --- | --- |
| T1 | 학습은 서버 스크립트를 부르는 얇은 감싸기(복사·재구현 안 함) | N | 표준 없음 | D78(사용자 결정), ADR 0004(대안 비교 있음) | `train_wrap.py:245-256` |
| T2 | 명령은 셸을 거치지 않는 인자 목록, 불리언은 값 없는 플래그 | S- | Python `subprocess`의 `shell=False` 관행 | docstring이 "셸을 거치지 않는다"고만 적음. 이유 기록 없음 | `train_wrap.py:137-157`, `train_wrap.py:249` |
| T3 | 서버 인자 이름 표 `server.arg_map` + `args_verified`/`args_evidence` | N | 표준 없음 | ADR 0004·0005. 인자 이름은 사람이 코드를 읽어 확인한 값이며 `--help` 실행으로 확인한 것이 아님(ADR이 밝힘) | `train_wrap.py:34-75`, `configs/train.yaml:11-31` |
| T4 | 서버 스크립트 sha256으로 코드 변경 감지 | N | 해시 고정 관행(이름 있는 표준 확인 못 함) | ADR 0004 결과(ML-12). 서버에 접속하지 않고 읽히는 환경에서만 비교 | `train_wrap.py:204-216` |
| T5 | 산출물 형식 확인: `adapter_model.safetensors` + `adapter_config.json`(`base_model_name_or_path`, `r`) | S | Hugging Face PEFT 어댑터 저장 형식, safetensors | ADR 0006 | `attach.py:25`, `attach.py:40-59` |
| T6 | 학습 기록 `*.train-wrap.json`(실행기의 자기 보고)을 계보로 사용 | N | 표준 없음 | ADR 0006 결과(ML-5): 자기 보고라는 한계를 ADR이 인정 | `train_wrap.py:259-281`, `attach.py:80-107` |
| T7 | 서버 접속 방식: 이 영역 세션은 접속하지 않고 사람이 읽기 전용 `rsync`로 어댑터를 가져옴 | S- | OpenSSH + rsync | runbook 156행이 절차를 적음. 선택 이유는 읽기 전용 정책 외 기록 없음 | `docs/runbook.md:156` |
| T8 | GPU 호스트 SSH에 `-o KexAlgorithms=curve25519-sha256` 강제 | N | RFC 8731(해당 키 교환 방식 정의). 후양자 방식을 끄는 선택 | runbook 72행: 기본 키 교환이 멈추는 증상 관찰. 원인은 확인하지 않음. 우회 성격 | `docs/runbook.md:72` |
| T9 | 코드를 하위 폴더별로 나눠 `rsync`(루트 통째는 `code 255`로 끊김) | N- | 표준 아님 | "원인 미확인"이라고만 적음. 근본 원인 조사 기록 없음 | `docs/runbook.md:72` |
| T10 | 개발 중 레지스트리 접근은 SSH 역터널 `-R 127.0.0.1:...` | S | OpenSSH `ssh -R` | runbook(호스트에서 직접 닿지 않기 때문), 운영 경계는 가정이라고 명시 | `docs/runbook.md:37`, `configs/gpu-dev.yaml:4` |
| T11 | 서버 학습 스크립트 경로의 학습 재개: 이 감싸기는 재개 기능이 없고(`subprocess.run` 한 번) 서버 스크립트가 재개를 지원하는지 확인한 기록도 없음 | ? | 확인 못 함 | 근거 기록 없음. README가 서버 스크립트의 `--help` 출력을 확인하지 못했다고 적음 | `train_wrap.py:245-256`, README "확인 못 한 것" |
| T12 | 파이프라인용 학습기를 직접 작성: 학습 루프, 선형 warmup·감쇠 스케줄러, 체크포인트·재개 형식(어댑터+옵티마이저+스케줄러+`state.json`) | O | `transformers.Trainer`(`resume_from_checkpoint`), `get_linear_schedule_with_warmup`, TRL `SFTTrainer`(서버 스크립트가 `trl`을 쓰는 것이 `python_modules`에 보임) | ADR 0004는 "비정본이고 사용처가 있어 남긴다"만 적음. 직접 작성한 이유는 없음. 재개한 학습이 비트 단위로 같은지는 미확인(design 4절) | `trainers/hf.py:88-216`, `train_wrap.py:312` |
| T13 | 4비트 NF4 + 이중 양자화 `bitsandbytes`, bf16 | S | QLoRA(Dettmers 외, 2023) 설정과 같은 구성 | 프로파일 주석(12GB 호스트). 코드 쪽 이유 기록은 없음 | `trainers/hf.py:56-62`, `profiles/gpu-dev-12g.yaml` |
| T14 | 학습·평가 의존으로 HF 스택(transformers, peft, accelerate, bitsandbytes)과 Qwen 계열 기반 모델을 씀 | S- | Hugging Face PEFT/transformers 관행 | 의존 핵심 선택 ADR 없음(워크스페이스 규칙은 ADR 요구). 기반 모델 선정은 헤드 몫이고 `docs/base-model-comparison.md`는 열지 않음 | `requirements-train.txt:3-7`, `profiles/qwen3-8b.yaml:7` |
| T15 | `requirements-train.txt`는 버전 미고정이고 머리 주석은 "아직 잠그지 않았다"인데 `requirements-train.lock`이 이미 있음(주석이 낡음) | S- | 잠금 파일로 고정(워크스페이스 규칙) | lock 파일 머리말에 freeze 기록(2026-10-15). 주석 불일치 | `requirements-train.txt:1-2`, `requirements-train.lock:1` |
| T16 | 계약·엔진·런타임을 Git 태그로 핀하고 `requirements.lock`으로 고정 | S | 워크스페이스 규칙(D18, D22) | `requirements.txt` 머리말, `scripts/setup-env.sh` 주석 | `requirements.txt:1-4`, `requirements.lock` |
| T17 | `jsonschema`, `PyYAML`을 핵심 의존으로 선택(`>=` 범위, lock으로 고정) | S- | JSON Schema 2020-12, YAML | 의존 선택 ADR 없음(ADR 0005가 jsonschema 선택 이유를 일부 언급) | `requirements.txt:5-6`, `requirements.lock` |
| T18 | 파이프라인 단계 캐시·재개·run 기록(`run.json`, 단계별 상태·해시)을 직접 구현 | O | DVC, MLflow Tracking, Make류 단계 캐시 | D38/D39가 "설정 하나로 반복 실행"을 요구한 것 외에 DVC·MLflow 등과의 비교 기록 없음 | `pipeline.py:85-173`, `pipeline.py:177-258` |
| T19 | `run_id`는 SHA-256 앞 16자, 패키지 버전은 `<데이터셋 버전>-<run_key 앞 8자>`, attach 버전은 해시 앞 12자 | N- | 없음 | 절단 길이의 근거 없음(충돌 시 레지스트리가 CONFLICT로 막음) | `pipeline.py:114`, `pipeline.py:364`, `attach.py:163` |
| T20 | `run-once` 멱등(이미 끝난 run은 skip), 스케줄러는 deploy 몫 | S | 워크스페이스 D39 | D39, runbook | `pipeline.py:411-418` |
| T21 | 같은 `run_id` 디렉터리에 대한 동시 실행을 코드로 막지 않음(스케줄러 직렬화에 의존) | N | 없음 | runbook "같은 시각에 두 번 부르지 않는다" | `pipeline.py:152-173`, `docs/runbook.md` 자동 실행 절 |
| T22 | `package-adapters`의 `created_at`을 어댑터·프롬프트 파일의 최신 mtime으로 정함 | N- | 없음(재현 빌드의 `SOURCE_DATE_EPOCH` 같은 방식과 다름) | 멱등 목적은 ADR 0006에 있으나 mtime을 고른 이유는 기록 없음. 복사 방법(예: `cp`와 `rsync -a`)에 따라 mtime이 달라지면 `content_hash`가 달라져 같은 어댑터가 `CONFLICT`가 될 수 있음(코드로 읽음, 실행 안 함) | `attach.py:62-64`, `attach.py:138` |
| T23 | 평가하지 않은 패키지의 리포트가 `schema_valid_raw` 값 0.0, n=0 | N | 계약 지표 레지스트리에 "평가 안 함" 표현이 없음 | 코드 주석과 ADR 0006(n=0은 계산 못 했다는 뜻) | `attach.py:67-77` |
| T24 | HF 모델 캐시 위치를 `HF_HUB_CACHE`/`HF_HOME`/`models--` 디렉터리 배치로 직접 해석하고 transformers 비공개 속성 `config._commit_hash`를 읽음 | N- | `huggingface_hub`의 캐시 API가 대안 | 근거 기록 없음(`scripts/clean_model_cache.py`는 `scan_cache_dir`를 씀) | `train_wrap.py:325-326`, `trainers/hf.py:74-85` |
| T25 | 캐시 정리는 `huggingface_hub.scan_cache_dir().delete_revisions` | S | `huggingface_hub` 공식 API | runbook D61 절 | `docs/runbook.md`(GPU 호스트 디스크 절), `scripts/clean_model_cache.py` |
| T26 | 기반 모델로 Qwen3-8B, 4B, 임시로 Qwen2.5-1.5B | ? | 판단 못 함 | 프로파일 주석은 "임시, 실제 선정은 헤드". 비교 문서는 열지 않음 | `profiles/gpu-dev-12g.yaml:3-5`, README 문서 표 |

## 5. 평가 지표·데이터 분할·누수

| ID | 요소 | 분류 | 표준 | 근거 | 증거 |
| --- | --- | --- | --- | --- | --- |
| E1 | 지표 이름·정의는 계약 METRICS.md v2 레지스트리가 정본, 미등록 이름은 최상위에 내지 않고 `ext`에 둠, 미등록 이름의 보고서는 게시 거부 | S | 계약 METRICS.md v2(`metric_name_problems`) | ADR 0011, `metric_names.py` 주석(ML-01) | `metrics.py:119-129`, `metrics.py:196-199`, `metric_names.py:13-25` |
| E2 | 학습 서버 이름 지표(`action_acc` 등)를 호환·대조용으로 함께 계산(같은 계산은 레지스트리 이름만 게시) | N | 없음 | ADR 0011, `metric_names.py` 주석, CR-L011 | `metrics.py:27-37`, `metric_names.py:13-25` |
| E3 | `value_precision/recall` = (key, value, status) 일치 | S | 정밀도·재현율 표준 정의. 일치 단위는 임시 기본값 D96(학습 담당 확인 전). ADR이 "분류 평가의 표준 정의"라 쓰나 출처 이름은 안 적음 | ADR 0011 | `metrics.py:83-84` |
| E4 | `response_length_chars_p95`는 nearest-rank 백분위 | S | nearest-rank 방법(통계의 단순 정의). 출처 문서명은 확인 못 함 | 코드 "왜" 주석, ADR 0011 | `metrics.py:39-42` |
| E5 | `semantic_violation_rate`는 turn-engine `validate_extraction`을 그대로 호출 | S | 서비스와 같은 검증기 재사용 | ADR 0011(D97) | `metrics.py:92-93`, `metrics.py:147-150` |
| E6 | LoRA-B 문장 규칙: 금지 표현 목록, 문장 수 4/5, 핵심 단어 1/3 포함, action별 필수 재료표 | N | 없음 | 코드·notes에 "잠정, 담당자 확인 전, 값은 서버 평가를 따름"이라고 명시. 측정 아님 | `text_rules.py:11`, `text_rules.py:56-71`, `text_rules.py:90` |
| E7 | 한글 수사를 직접 파싱해 "새 숫자" 판정 | N- | 라이브러리 대안 비교 없음 | 십·백·천이 있을 때만 수로 본다는 주석 외 기록 없음 | `text_rules.py:22-40` |
| E8 | `coverage_seen_unseen_gap` = 본 문항 세트 일치율 − 보지 못한 문항 세트 일치율 | N | 없음 | 보고서 notes, D32 | `metrics.py:154-157` |
| E9 | 평가는 온도 0(결정적 디코딩)에서만 | S | D48. 온도 > 0 재현성은 약속하지 않음 | 코드 주석 | `pipeline.py:312-314` |
| E10 | 분할은 상류 데이터셋의 train/val/test를 그대로 쓰고 이 영역은 나누지 않음. "보지 못한 문항 세트"는 문항 세트 단위로 판정 | N | 표준 없음 | D32, design 5·6절 | `transform.py` SERVER_SPLIT, `datasets.py:83-89` |
| E11 | 누수 검사는 `scenario_id`·`sample_id` 겹침만 봄(발화·프롬프트 본문 중복은 보지 않음) | N | 표준 없음. 그룹 단위 분할이 일반 관행 | 브리프 4절(design 5절이 인용). 본문 근접 중복 미검사는 한계로 문서에 없음 | `datasets.py:92-98`, `pipeline.py:216` |
| E12 | 평가 `limit_samples`와 `max_train_samples`는 무작위 추출이 아니라 앞에서 N개 자르기 | N- | 없음 | 근거 기록 없음. 정렬 순서에 편향될 수 있음 | `pipeline.py:299`, `trainers/hf.py:123-124` |
| E13 | 최대 길이 초과 학습 샘플은 건너뛰고 건수만 기록 | N | 없음 | 모듈 docstring | `trainers/hf.py:125-132` |
| E14 | 샘플에 `question_set`이 없으면 파일의 문항 세트가 하나일 때만 채우고 여럿이면 '미상' | N | 없음 | 코드 주석(서버 데이터에 없는 필드) | `pipeline.py:212-213` |
| E15 | 같은 프롬프트에 다른 정답인 학습 샘플 수를 기록만 하고 중단하지 않음 | N | 없음 | CR-L001 | `pipeline.py:227` |
| E16 | 평가 경로 시험은 정답을 그대로 내는 가짜 모델로만 돎 | N | 없음 | ADR 0008(한계 명시: 지표 값이 모델 품질을 대표하지 않음) | `backends/fake.py`, ADR 0008 |

## 6. 설정 형식·난수·재현성·시각

| ID | 요소 | 분류 | 표준 | 근거 | 증거 |
| --- | --- | --- | --- | --- | --- |
| C1 | 설정은 YAML 한 파일, `yaml.safe_load` | S | YAML(PyYAML 구현), `safe_load` 사용 | ADR 0005(주석으로 값 근거를 적을 수 있음) | `config.py:166` |
| C2 | YAML 1.1 해석 위험에 대한 대응 없음. 실행 확인(`-I`, 인라인 문자열): `off`와 `no`가 `False`로, `0123`이 정수 83으로 읽히고, 중복 키는 에러 없이 마지막 값이 이긴다. 예: 문서가 권하는 `output.disk_policy: off`는 따옴표 없이 쓰면 `False`가 되어 스키마 enum에서 거부된다(조용히 틀리지는 않음) | N- | YAML 1.2 코어 스키마는 불리언을 `true/false`로만 정의함. PyYAML은 1.1 | ADR 0005·runbook에 언급 없음 | `config.py:166`, `train_wrap.py:72`, 실행 확인 |
| C3 | 설정 검증은 JSON Schema 2020-12, `additionalProperties: false` | S | JSON Schema 2020-12 | ADR 0005(오타가 조용히 무시되는 사고 방지) | `config.py:85-149`, `train_wrap.py:34-76` |
| C4 | 프로파일 병합 `deep_merge`(객체 재귀, 목록은 통째 교체) 직접 구현 | N- | 표준 아님(RFC 7386 JSON Merge Patch와 비슷하나 같지 않음) | design 3절이 동작을 서술할 뿐 방식 선택 이유 없음 | `config.py:152-160` |
| C5 | 설정 속 비밀 값 검사를 키 이름 부분 문자열(`token`, `secret` 등)로 함 | N | 표준 없음(시크릿 스캐너 도구가 대안) | D84, 오류에 값을 싣지 않음. 키 이름 규칙에 걸리지 않는 비밀은 못 잡음 | `config.py:174-194` |
| C6 | `module:callable` 문자열로 설정에서 임의 팩토리를 import·호출 | S- | PyPA entry point 표기와 같은 모양 | 근거 기록 없음. ADR 0005는 "Python 설정 파일은 임의 코드 위험"이라며 배척했는데, 이 방식은 설정이 import 가능한 아무 호출 대상을 실행하게 함 | `config.py:77-78`, `factories.py:36-47` |
| C7 | `config_hash` = JCS SHA-256, 위치·이름·게시 위치 필드 제외 | N | 없음 | design 4절 | `config.py:247-253` |
| C8 | 시드: `run.seed` 하나를 `random`·`torch.manual_seed`에 주고, 평가 생성은 요청별 `torch.manual_seed`, 서빙 `decoding.seed`는 별도 | S- | PyTorch Reproducibility 문서의 방식. numpy 시드는 코드에서 사용 확인 못 함 | design 4절(측정 결과). 시드 처리 선택 자체의 ADR 없음 | `trainers/hf.py:106-107`, `backends/hf.py:46` |
| C9 | 결정적 연산 설정: `use_deterministic_algorithms(warn_only)`, `CUBLAS_WORKSPACE_CONFIG`, cudnn 결정적·benchmark 끔 | S | PyTorch Reproducibility 문서 | 주석 + design 4절 측정 | `trainers/hf.py:96-101` |
| C10 | 프로파일은 `attn_implementation: eager`(sdpa는 같은 시드에서 어댑터가 달랐음) | N | 없음 | design 4절의 측정. RTX 4070 SUPER 한 대에서만 확인, 다른 GPU·드라이버에서는 약속 안 함 | `profiles/gpu-dev-12g.yaml:23`, design 4절 |
| C11 | 체크포인트에서 재개한 학습이 중단 없는 학습과 같은 해시인지 | ? | 확인 못 함 | design 4절: 10스텝 시험에서 달랐고 원인이 재개인지 환경인지 분리하지 못함. 옵티마이저·스케줄러는 저장하나 난수 상태는 저장하지 않음(코드로 확인) | `trainers/hf.py:205-215`, design 4절 |
| C12 | 시각은 UTC `YYYY-MM-DDTHH:MM:SSZ`, 시계는 주입 가능 | S- | RFC 3339 / ISO 8601 | 근거 기록 없음 | `pipeline.py:32-33` |
| C13 | 시각은 run 기록에만 두고 해시에서 제외, `created_at`은 처음 값을 고정 | N | 없음 | design 4절 | `pipeline.py:363`, `pipeline.py:111-112` |
| C14 | 평가 보고서에 발화·출력 원문을 넣지 않고 샘플 ID와 오류 이름만 | S- | 개인정보 최소화 관행 | design 6절, 코드 주석. 비식별 방식 자체는 이웃 영역 몫이라 이 영역에 선택 없음 | `datasets.py:69-71` |

## N-, O, ? 항목 한 줄 목록

N- (표준 아님, 근거 없음) 31건
- R6 `directory_hash` 방식: 이 영역 근거 없음, 대안(OCI digest 등) 비교 못 봄(계약 소유는 헤드).
- R14 보존 기간·삭제·백업 정책 없음.
- H7 토큰 발급·회전·폐기를 수동·정적으로 운영.
- H10 토큰 "서로 다른 글자 8개" 기준.
- H19 Dockerfile의 `ssh-keyscan ... || true`로 호스트 키를 처음 본 값으로 신뢰.
- V1 `max_footprint_gb` 20.
- V2 `min_free_gb` 30.
- V4 게시 여유 가산 1GB.
- V5 본문 상한 4GiB.
- V6 소켓 timeout 30초.
- V7 동시 연결 32.
- V9 인증 실패 10회/60초.
- V11 거절 전 본문 버리기 1MiB/0.25초.
- V12 클라이언트 timeout 300초.
- V13 HEALTHCHECK 15초/3초.
- V14 설치 재시도 대기 10초.
- V15 하위 프로세스 timeout 15초/60초.
- V16 runbook의 1800/3000/15초.
- V17 시드 범위 2^31-1.
- V18 프로파일 하이퍼파라미터와 메모리 비율.
- V20 학습기 코드 기본값(클립 1.0, weight_decay 0 등).
- V21 순서 시드의 1009.
- V22 읽기 조각 1MiB.
- T9 `rsync` 루트 통째 실패를 하위 폴더 분할로 우회(원인 미확인).
- T19 `run_id` 16자, 버전 8자·12자 절단.
- T22 `created_at`을 파일 mtime으로 정함(복사 방법에 따라 해시가 달라질 수 있음).
- T24 HF 캐시 배치 직접 해석과 비공개 속성 `_commit_hash` 사용.
- E7 한글 수사 직접 파서.
- E12 `limit_samples`와 `max_train_samples`를 앞에서 자르기.
- C2 YAML 1.1 함정(`off`→False, 중복 키 무음, 8진수) 대응 없음.
- C4 `deep_merge` 직접 구현.

O (재발명) 3건
- H2 연결 상한·graceful drain·slow-client 방어·본문 버리기를 직접 구현(`service/app.py:392-460`). 대안: WSGI/ASGI 서버.
- T12 파이프라인 학습 루프·스케줄러·체크포인트/재개 직접 작성(`trainers/hf.py:88-216`). 대안: `transformers.Trainer`, TRL. 직접 작성 이유 기록 없음.
- T18 단계 캐시·run 기록 오케스트레이션 직접 구현(`pipeline.py:85-258`). 대안: DVC, MLflow Tracking. 비교 기록 없음.

? (표준 여부 확인 못 함) 5건
- H9 토큰 최소 길이 32자: ADR이 OWASP·NIST를 기억으로 인용했고 원문 미확인.
- H18 기반 이미지를 다이제스트가 아닌 태그로만 지정: 표준 관행 여부 문서로 확인 못 함.
- T11 서버 학습 스크립트 경로의 학습 재개: 감싸기에 기능이 없고 서버 스크립트의 재개 지원은 확인된 바 없음.
- T26 기반 모델(Qwen 계열) 선정: 비교 문서를 열지 않았고 헤드 몫.
- C11 재개 학습의 비트 재현성: 원인 분리 못 함.

## 근거 기록이 비어 있는 곳(위 표에서 따로 보이게 정리)

- ADR 부재(워크스페이스 규칙상 ADR 대상): HF 의존 스택 선택(T14), 시드·난수 처리 방식(C8), 시각 형식(C12), tar 형식과 해제 방식(H14, H15), 보존 기간(R14), 토큰 수명주기(H7). ADR 0001, 0002, 0005, 0006, 0008, 0010은 "당시 비교 기록 없음"을 스스로 밝히고 사후 판단을 적었다.
- 표준을 이름으로 적은 곳은 ADR 0003(RFC 8785), 0009(OWASP·NIST 기억 인용), 0011(nearest-rank, 분류 평가 정의 이름 없음)뿐이다. 나머지 표준 이름은 이 점검에서 붙였고, 그 이름의 조문을 이번에 원문으로 확인한 것은 없다.

## 점검하지 못한 것

- `docs/brief.md`, `docs/boundary.md`, `docs/STATUS.md`, `docs/base-model-comparison.md`, `docs/change-requests/`, `CHANGELOG.md`는 열지 않았다. 브리프 4·6절을 근거로 적은 항목(E11, H13)은 design.md와 ADR이 인용한 내용만 봤다.
- 코드 중 `evaluate.py`, `regression.py`, `cli.py`, `schemas.py`, `file_dataset_store.py`(해시 재확인 줄만 확인), `transform.py`(머리말과 앞 60줄), `backends/hf.py`·`backends/fake.py`, `trainers/dryrun.py`, `service/app.py` 중 핸들러 본문 일부(게시 처리 300-370행의 세부 오류 경로)는 정독하지 않았다.
- `configs/` 중 `train-b.yaml`, `dryrun.yaml`, `gpu-dev-fixture-*.yaml`, `configs/compare/*.yaml`, `profiles/qwen3-*.yaml`의 값, `scripts/compare_base_models.py`, `scripts/determinism_probe.py`, `tests/` 전체는 열지 않았다. V1은 ADR 0007의 서술에 의존한다.
- `requirements-train.lock`, `requirements.lock`은 앞부분만 봤다. 잠금된 버전의 취약점·라이선스는 점검하지 않았다.
- 인용한 외부 표준(RFC 6750, RFC 8731, RFC 6585, RFC 3339, YAML 1.2, PEP 706, QLoRA, PyTorch Reproducibility, Docker secrets·BuildKit 문서, Python `http.server`·`hmac` 문서)의 원문은 이번에 확인하지 못했다. 알고 있는 수준에서 이름을 붙였고, 조문 번호나 문구를 단정한 곳은 없다.
- 실행한 것은 PyYAML 해석 확인 하나뿐이다. 시험(`pytest`), 이미지 빌드·스모크, 서비스 기동, 학습, 평가는 실행하지 않았다. 그래서 "동작한다"는 판단은 하지 않았고 이 문서의 모든 항목은 읽어서 판단한 것이다.
- 학습 서버와 GPU 호스트에는 접속하지 않았다. 서버 스크립트(`train_lora_*.py`)와 서버 `run_config.json`은 읽지 못했고, `train.yaml`의 값 출처는 설정 주석과 문서의 서술에 의존한다.
- `boundary-contracts`의 `METRICS.md` 정의 본문, `CONVENTIONS.md` 전체, `HTTP.md` 전체는 읽지 않았다(해시·Id·cursor 관련 줄만 확인).
