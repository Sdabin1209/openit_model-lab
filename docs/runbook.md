# 런북

## deploy가 알아야 하는 것 (맨 위 요약)

이 리포지토리에는 진입점이 둘 있다.

| 진입점 | 무엇 | 어떻게 |
| --- | --- | --- |
| **model-registry 서비스** (I3 서버) | 상시 서비스. 모델 패키지 저장·조회 | 이미지 `Dockerfile`(비root uid 10001, 포트 8080, 볼륨 `/data` 하나). `GET /healthz`, `GET /readyz`. 로그는 표준 출력 |
| **run-once 잡** (학습·평가·패키징·게시) | 스케줄러가 부르는 일회성 명령. 서비스 아님, 이미지 없음(D22 (2)). GPU 호스트에서 돈다 | `python -m model_lab run-once --config <설정 파일>` |

### model-registry 서비스 환경 변수
| 이름 | 뜻 |
| --- | --- |
| `MODEL_REGISTRY_READ_TOKENS_FILE` (개발용 대체: `MODEL_REGISTRY_READ_TOKENS`) | 파일 내용이 `역할=토큰,역할=토큰` (예: `release-gate=...`). 읽기 경로만 허용 |
| `MODEL_REGISTRY_WRITE_TOKENS_FILE` (개발용 대체: `MODEL_REGISTRY_WRITE_TOKENS`) | 파일 내용이 `model-lab=토큰`. 게시(`POST /v1/packages`). 역할은 `model-lab`만 허용(시작할 때 검사) |
| `MODEL_REGISTRY_ROOT` | 저장 위치(기본 `/data`, 볼륨 하나). 파일 레지스트리 그대로 |
| `MODEL_REGISTRY_PORT` / `MODEL_REGISTRY_HOST` | 기본 8080 / 0.0.0.0 |
| `MODEL_REGISTRY_MAX_BODY` | 게시 본문 상한 바이트(기본 4GiB) |
비밀은 `boundary_contracts.read_secret`(D84)으로 읽는다: `NAME_FILE`(파일 경로)이 있으면 그 파일 내용(앞뒤 공백 제거), 없으면 `NAME` 환경 변수. 운영은 파일(`-v <비밀 폴더>:/run/secrets:ro`와 `…_FILE=/run/secrets/…`), 환경 변수는 개발용이다. 파일을 못 읽거나 비어 있으면 시작하지 않는다(종료 코드 2, 메시지에는 이름·경로만). 토큰이 하나도 없어도 시작하지 않는다. 토큰 값은 로그·`/readyz`·오류 본문에 나오지 않는다(`tests/test_secrets.py`). 경로·상태 코드는 `../boundary-contracts/HTTP.md`의 I3 절. `/readyz`는 저장소를 읽고 임시 파일을 쓸 수 있을 때 200.
이미지 라벨: `io.openit.service=model-registry`, `io.openit.revision`(빌드 인자 `REVISION`), `io.openit.contracts.boundary=v2.0.2`, `io.openit.contracts.survey-schema`/`turn-engine`/`model-runtime`은 `n/a`(서비스에 포함하지 않음). 확인: `scripts/smoke_registry.sh`(빌드 -> 빈 포트로 실행 -> healthz/readyz/토큰/게시/조회).

### 입력 저장소 선택 (설정의 `inputs.*`)
| 입력 | 팩토리 | 인자 |
| --- | --- | --- |
| 데이터셋(I2, 실제) | `model_lab.file_dataset_store:file_dataset_store` | `root` 또는 `root_env: <환경 변수>`: synthetic-data가 낸 `<root>/<dataset_id>/<version>/{manifest.json, meta.json, files/…}`의 루트. 이 리포지토리의 독립 읽기 구현(`DatasetStore` Protocol, 읽기 전용, 매니페스트·파일 해시 재확인) |
| 데이터셋(I2, 시험용) | `tests.doubles.dataset_store:from_dir` | 합성 v2 샘플로 만든 가짜 데이터. `configs/gpu-dev-fixture-*.yaml`이 쓴다 |
| 실패 사례(I7) | `boundary_contracts.clients:HttpFailureStore` | `base_url`/`base_url_env`, `token`/`token_env` |
| 문항(I1, 자리만) | `boundary_contracts.clients:HttpQuestionReader` | 같은 방식 |
실제 실행 설정 예: `configs/gpu-dev.yaml`(환경 변수 `DATASET_ROOT`, `MODEL_REGISTRY_URL`, `MODEL_REGISTRY_WRITE_TOKEN`, `FAILURE_STORE_URL`, `FAILURE_STORE_TOKEN`). 설정(`configs/*.yaml`, 프로파일 포함)에는 비밀 값을 직접 적을 수 없다: 키 이름에 `token`, `secret`, `password`, `credential`, `api_key`, `authorization`, `private_key`가 들어 있고 값이 문자열이면 시작을 거부한다(중첩 dict·list 포함, 오류에는 키 경로만). `*_env`는 환경 변수 이름을 받으므로 `[A-Z][A-Z0-9_]+` 형식이면 허용한다. 설정의 `token_env: NAME`은 `NAME_FILE`(파일 경로)이 있으면 그 파일을, 없으면 `NAME` 환경 변수를 읽는다(D84). `configs/gpu-dev-fixture-*.yaml`과 `configs/dryrun.yaml`은 시험용이다.

### 회귀 평가 (I7)
`evaluation.regression: {enabled, limit_cases, label}`로 켜고 끈다(기본 꺼짐). 켜면 `inputs.failure_store`에서 `review_status=CONFIRMED`이고 `expected`가 있는 최신 FailureCase를 읽어, 비식별 trace의 `parser_input`/`generator_input`을 입력으로 후보 어댑터를 평가하고 정답과의 일치를 보고서의 `regression` 절(`aggregate`, `by_failure_type`, `not_matching`)에 담는다. 종류는 `expected_kind`, 없으면 expected의 형태(`items` = 파서, `response_text` = 생성기)로 판별한다. trace가 없거나 입력이 없거나 입력 계약을 어기면 건너뛰고 사유(`skipped`: `trace_not_found`, `trace_input_missing`, `input_invalid`, `expected_invalid`, `kind_unknown`)와 건수·사례 ID를 남긴다. 임계치는 없다. 실패 사례 저장소 내용은 run_id에 들어가지 않으므로 집합이 달라졌을 때 새 run을 원하면 `label`을 바꾼다.

### 개발에서의 데이터·네트워크 절차 (한 단락, 운영의 네트워크 경계는 사용자와 정하기 전이라 **가정**)
데이터셋 이동: synthetic-data의 출력 폴더(데이터셋 루트)를 `rsync -a <루트>/ gpu-dev:openit-gpu/datasets/`로 gpu-dev에 옮기고 잡 환경에 `DATASET_ROOT=$HOME/openit-gpu/datasets`를 준다(이미 게시된 버전은 불변이라 증분 rsync가 안전하다). 레지스트리 접근: gpu-dev에서는 서빙 서버의 model-registry가 직접 닿지 않으므로 개발에서는 서버 쪽에서 SSH 역터널을 연다: `ssh -N -R 127.0.0.1:18080:127.0.0.1:<레지스트리 포트> gpu-dev`(실패 사례 저장소도 같은 방식으로 다른 포트). 그러면 호스트에서 `MODEL_REGISTRY_URL=http://127.0.0.1:18080`이다. 쓰기 토큰은 명령줄에 두지 않고 표준 입력으로 넘긴다. 개발에서는 환경 변수 대체를 쓴다(운영은 `MODEL_REGISTRY_WRITE_TOKEN_FILE`로 권한 0600 파일을 가리킨다): `printf %s "$TOKEN" | ssh gpu-dev 'read -r MODEL_REGISTRY_WRITE_TOKEN; export MODEL_REGISTRY_WRITE_TOKEN MODEL_REGISTRY_URL=http://127.0.0.1:18080 DATASET_ROOT=$HOME/openit-gpu/datasets; cd openit-gpu/model-lab && setsid nohup flock -w 1800 ~/openit-gpu/.gpu.lock .venv/bin/python -m model_lab run-once --config configs/gpu-dev.yaml > run.log 2>&1 &'`. 터널이 끊기면 게시가 `UNAVAILABLE`(종료 코드 3)로 멈추고 학습 결과는 남으므로 터널을 다시 열고 같은 명령을 실행한다. 운영(실제 스테이션)에서 어떤 주소·인증·방화벽으로 닿게 할지는 정해지지 않았다.

### run-once 잡
- 명령: `python -m model_lab run-once --config <설정 파일>` (또는 `model-lab run-once`). 출력은 JSON 한 줄: `{"action": "ran"|"skip", "run_id": ..., "dataset_version": ...}`.
- 종료 코드: 0 = 실행을 끝냈거나 할 일 없음, 2 = 오류(설정·데이터·환경. 재시도해도 같음), 3 = `UNAVAILABLE`(이웃·레지스트리 일시 장애, 같은 명령 재실행 가능).
- 멱등: 같은 (설정, 데이터셋 버전, 시드, 라이브러리 버전)이면 같은 `runs/<run_id>/`를 가리키고, 끝난 run은 `skip`. 중단된 run은 이어서(학습은 체크포인트부터). 같은 시각에 두 번 부르지 않는다(스케줄러가 직렬화).
- 작업 폴더: `runs_dir`(설정). 비밀 값은 설정 파일에 없고 환경 변수로만 받는다.
- 설정 형식(요약, 정본은 `model_lab/config.py`의 `CONFIG_SCHEMA`와 `docs/design.md` 3절). 예: `configs/gpu-dev-fixture-smoke.yaml`.
  - `profile`: 학습 프로파일(`profiles/gpu-dev-12g.yaml`: 기반 모델, 양자화, 최대 길이, 배치, 학습률, 에폭, GPU 수·메모리 비율, `checks.max_lora_rank`)
  - `inputs.dataset`: `{id, version?}` (version 생략 = 최신), `inputs.dataset_store`: 읽기 SDK 팩토리
  - `registry`: 게시 대상. 로컬 `{kind: file, path}` 또는 원격 `{kind: http, base_url, token_env, timeout}`(`boundary_contracts.clients.HttpModelRegistry`). `token_env`가 가리키는 이름의 `_FILE` 경로 파일 또는 환경 변수(쓰기 토큰)를 잡 실행 환경에 준다
  - 이웃 읽기 SDK는 모두 `{factory: "pkg.mod:fn", args: {..., token_env: 변수}}`로 고른다. `*_env` 인자는 환경 변수 값으로 바뀐다. `inputs.question_reader`, `inputs.failure_store` 자리는 열려 있으나 아직 쓰지 않는다
- 필요 환경: GPU 호스트에 `requirements.lock` + `requirements-train.txt`(torch 등), 모델 캐시(`~/.cache/huggingface`), `flock ~/openit-gpu/.gpu.lock`으로 GPU 직렬화(D47).
- 다른 호스트의 서버에 게시할 때: GPU 호스트에서 서버 주소로 HTTP가 닿아야 하고, 쓰기 토큰은 잡 실행 환경에만 둔다. 어댑터(수십~수백 MB)는 게시 때 tar로 올라간다.

## 환경 (CPU)
```
python3 -m venv .venv
.venv/bin/pip install -e ../boundary-contracts -e ../turn-engine -e ../model-runtime pytest pyyaml
# 태그로 설치(CI·이미지): .venv/bin/pip install -r requirements.txt   (requirements.lock이 확인된 전체 버전)
.venv/bin/python -m pytest -q                      # 152건 통과
```
D22 (2): 서비스가 없는 run-once 잡이라 Dockerfile 없이 `requirements.lock`으로 환경을 고정한다.

## CPU로 파이프라인 끝까지 (모델 없이)
```
.venv/bin/python scripts/make_fixture_dataset.py fixtures/store      # 합성 v2 샘플로 시험용 데이터셋 저장소(I2 대역)를 만든다
.venv/bin/python -m model_lab run --config configs/dryrun.yaml       # 학습(dry-run) -> 평가(fake-oracle) -> 패키징 -> 게시
.venv/bin/python -m model_lab run-once --config configs/dryrun.yaml  # 두 번째부터 skip
.venv/bin/python -m model_lab status --config configs/dryrun.yaml
```
`--stop-after <단계>`(`load_dataset|transform|train|evaluate|package|publish`)로 중간에서 멈출 수 있고, 같은 명령을 다시 실행하면 이어 간다.

## 학습 방법을 바꾸려면
설정 파일(또는 프로파일)의 `training`, `base_model`을 고친다. 코드를 고치지 않는다. 설정이 바뀌면 `config_hash`와 `run_id`가 바뀌어 새 run이 된다. 새 하드웨어는 `profiles/<이름>.yaml`을 추가하고 설정의 `profile:`이 가리키게 한다.

## GPU 호스트(gpu-dev) 실행 (D47)
**SSH 옵션(이 환경)**: 기본 SSH는 키 교환(sntrup761 후양자 알고리즘의 큰 패킷)에서 멈춘다. 모든 `ssh`에 `-o KexAlgorithms=curve25519-sha256`을, rsync에는 `-e "ssh -o BatchMode=yes -o ConnectTimeout=15 -o KexAlgorithms=curve25519-sha256"`을 붙인다. 사용자의 `~/.ssh/config`는 고치지 않는다. 아래 예시의 `ssh gpu-dev`, `rsync … gpu-dev:`는 이 옵션을 붙인 것으로 읽는다. 리포지토리 루트를 통째로 rsync하면 중간에 `code 255`로 끊기는 일이 있어(원인 미확인) 하위 폴더(`model_lab/ configs/ scripts/ tests/ profiles/`)를 하나씩 보내니 안정적이었다.
1. 코드 보내기(비밀 값 없음, 호스트에서 GitHub 접속 없음):
   `rsync -a --exclude .venv --exclude .git --exclude runs --exclude fixtures ./ gpu-dev:openit-gpu/model-lab/`
   호스트에는 이웃 폴더가 없다. 태그에서 만든 wheel과 문진 스키마 사본을 `vendor/`(gitignore)에 두고 보낸다:
   `pip wheel --no-deps -w vendor/wheels "git+file://<경로>/boundary-contracts@v2.0.2"` (turn-engine@v0.4.0, model-runtime@v0.7.3도 같이; `requirements.txt` 기준), `vendor/survey-schema/{schemas,CONTRACT_VERSION}`.
2. 호스트 환경(처음 한 번): `cd ~/openit-gpu/model-lab && python3 -m venv .venv && .venv/bin/pip install vendor/wheels/*.whl pyyaml pytest -r requirements-train.txt`
3. 시험 데이터: `.venv/bin/python scripts/make_fixture_dataset.py fixtures/store`
4. 실행(긴 작업은 분리하고 로그를 본다. GPU 명령은 반드시 락으로 감싼다):
   `setsid nohup flock -w 1800 ~/openit-gpu/.gpu.lock timeout 3000 .venv/bin/python -m model_lab run --config configs/gpu-dev-fixture-smoke.yaml > smoke.log 2>&1 &`
5. 결과 가져오기: `rsync -a gpu-dev:openit-gpu/model-lab/runs/ runs/gpu/` 그리고 이쪽에서 게시한다(결과의 어댑터와 `run.json`, `package/*.manifest.json`, `eval/*.json`). 호스트의 레지스트리는 시험용이다.
6. 중단했다면 같은 명령을 다시 실행한다. 학습은 `runs/<run_id>/work/<ROLE>/ckpt`에서 이어 한다.
호스트 SSH가 두 번 실패하면 멈추고 STATUS에 적는다(반복 재시도 루프 금지). 서버는 항상 켜져 있지 않다.

## 자동 실행(스케줄러용)
`model-lab run-once --config <설정>`을 주기적으로 부르면 된다. 새 데이터셋 버전이 없으면 `{"action": "skip"}`을 내고 끝난다. 같은 시각에 두 번 부르지 않는다(같은 run 디렉터리를 쓴다. 필요하면 스케줄러가 직렬화한다).

## GPU 호스트 디스크와 모델 캐시 (D61)
- **WSL의 `df`는 호스트 여유를 알려 주지 않는다.** 가상 디스크 크기(1007GB)만 보여 주고 호스트 C 드라이브의 실제 여유가 아니다(`/mnt/c` 마운트도 없고 powershell 연동도 없다). 또한 **WSL 안에서 파일을 지워도 vhdx 파일은 줄지 않는다**(가상 디스크는 자동으로 작아지지 않는다). 그래서 **모델 내려받기 예산(아래 `footprint_guard`)은 `df`를 쓰지 않는다.** 반대로 학습 전 점검(`train`, `train-doctor`)과 레지스트리 게시는 출력/저장 폴더가 놓인 파일 시스템의 `df`를 쓴다(ML-2 정정: 이전 문장 "df 점검은 쓰지 않는다"는 코드와 어긋났다). 학습 설정 `output.disk_policy`가 `block`(기본: 여유 `min_free_gb` 미만이면 학습 전에 멈춤, doctor는 FAIL) | `warn`(doctor만 WARN) | `off`다. `df`가 호스트 여유를 보여 주지 않는 WSL 호스트는 `warn`으로 두고 사용량 예산을 따로 지킨다. 어느 값을 쓸지는 헤드·사용자 결정이다.
- 대신 **우리 작업의 사용량 예산**을 둔다. 비교 스크립트는 설정 `max_footprint_gb`(기본 20)를 두고, 모델을 내려받기 전에 (HF 캐시 크기 `huggingface_hub.scan_cache_dir()` + `~/openit-gpu` 크기 + 후보 예상 크기 `approx_gb`)가 예산을 넘으면 그 후보 전에 멈춘다(`footprint_guard`). 이미 완전히 받은 모델은 예상 크기를 더하지 않는다. 자동 삭제는 하지 않는다.
- 캐시 정리: `python scripts/clean_model_cache.py`(목록과 크기), `python scripts/clean_model_cache.py <모델 ID>`(dry-run: 지울 때 확보되는 크기), `... <모델 ID> --yes`(삭제). 캐시는 blob을 공유하는 구조라 디렉터리를 직접 지우지 않고 `scan_cache_dir().delete_revisions(...)`를 쓴다. gpu-dev는 사용자 워크스테이션이므로 **삭제는 사용자 승인 뒤에만** 한다.
- 2026-10-15 호스트 관찰: 작업 폴더 `~/openit-gpu` 약 15GB(가상환경이 대부분), 모델 캐시는 사용자 위임으로 비교용 모델을 지운 뒤 `Qwen/Qwen2.5-1.5B-Instruct` 2.9GB만 남아 있다(파이프라인 스모크가 쓴다). 따라서 새 모델 하나를 받으면 기본 예산 20GB를 쉽게 넘는다. 큰 기반 모델이 정해지면 `max_footprint_gb`를 사용자와 정해 설정에서 올린다.

## 문제 확인
| 증상 | 확인 |
| --- | --- |
| 종료 코드 2, `ConfigError` | 설정 형식 위반(알 수 없는 키 포함). 메시지의 경로를 본다 |
| `INTEGRITY_ERROR` | 데이터셋 파일/레지스트리 파일의 해시 불일치. 데이터셋을 다시 읽는다(`runs/<id>/dataset` 삭제 후 재실행) |
| `CONFLICT`(게시) | 같은 package_id·version에 다른 어댑터. 학습 비결정성 때문에 run 디렉터리를 지우고 다시 학습하면 생긴다. 기존 run 디렉터리를 재사용한다 |
| `TrainerUnavailable` | torch/CUDA 없음. `requirements-train.txt`와 GPU 확인 |
| `UnsupportedServingConfig` | `serving.*.constraint`가 평가 백엔드(`hf-transformers`/`fake`)와 다르다 |


## 학습 서버 데이터셋 확인과 시스템 프롬프트 (2.0.0)
- 서버 데이터는 읽기만 한다(`ssh openit cat ~/taehyeon/data/dataset_10_14/lora_{a,b}_{train,val,test}.jsonl`). 받은 파일은 `runs/_server_ds/`(gitignore)에 두고 `.venv/bin/python scripts/check_server_dataset.py runs/_server_ds`로 모든 줄의 계약 검증과 글자 단위 왕복을 확인한다.
- `prompts/lora_{a,b}.system.txt`는 서버 데이터의 system 메시지를 바이트 그대로 둔 것이다. 데이터가 바뀌면 `tests/test_packager_v2.py::test_repo_prompts_are_the_ones_in_the_server_dataset_rows`가 고정 샘플 줄과의 불일치를 알려 주지만 새 데이터 전체와의 비교는 `read_server_file`로 직접 한다(시스템 프롬프트가 한 종이 아니면 중단한다).

## 학습 실행 감싸기: 설정 + 명령 + doctor (D78, G06)

학습의 정본은 학습 서버의 코드(`train_lora_a.py`, `train_lora_b.py`)다. 이 리포지토리는 그 코드를 복사하지 않고 부르기만 한다. `trainers/hf.py`(파이프라인용 사본)는 서버 정본과 다르므로 **서버와 같은 어댑터를 만들 수 없다**(비정본, 파이프라인 검증용).

현재 상태: 설정 검증·명령 조립·dry-run·doctor·산출물 확인은 대역 스크립트로 시험했다(`tests/test_train_wrap.py`). 서버 스크립트의 인자 이름은 서버 코드로 확인했으나(`--help` 실행은 안 함) **산출물 위치는 확인하지 못했고**, 진짜 학습은 GPU가 필요해 해 보지 못했다 [구멍: 산출물 위치, GPU].

1. **[학습 서버에서] 점검.** 이 리포지토리를 서버에 두고(또는 `.venv`에 설치) 실행한다.
```
.venv/bin/python -m model_lab train-doctor --config configs/train.yaml
```
   `[OK]` `[WARN]` `[FAIL]` `[HOLE]`과 해결 안내가 나온다. FAIL은 고친다. `HOLE`은 "여기서는 확인할 수 없음"이다(GPU가 없는 PC에서 GPU는 HOLE). 점검 항목: 서버 스크립트 경로, 데이터셋 파일, GPU(nvidia-smi 또는 torch.cuda), 서버 인자 이름 확인 여부, 파이썬 의존(`server.python`으로 실행), 기반 모델 캐시, 프로파일, 디스크.
2. **인자 이름 (채워져 있음).** `configs/train.yaml`(A, 8B)과 `configs/train-b.yaml`(B, 8B)의 `server.arg_map`은 헤드가 2026-10-27에 서버 `~/taehyeon/train_lora_a.py`, `~/myungchan/train_lora_b.py`의 `add_argument`를 읽어 채웠다(`args_verified: true`의 뜻은 "인자 이름을 서버 코드로 확인함"이고 근거는 `args_evidence`에 있다). **스크립트 `--help`를 실제로 실행해 본 것은 아니다.** 서버 스크립트가 바뀌면 `train-doctor`가 알려 주지 못하므로 `--help`와 `arg_map`을 다시 대조한다. 값 없는 플래그(`--dry_run`)는 불리언 하이퍼파라미터로 쓴다(true면 플래그만, false면 넘기지 않음). 설정 값은 서버 실제 v2 학습의 `run_config.json`(A `a_8b_v2`, B `b_8b_1014`)과 같고 시험이 명령줄을 대조한다. 어댑터가 `out_dir` 바로 아래에 나오는지는 [미확인]이라 `output.adapter_dir`은 첫 실행의 산출물 확인 결과에 맞춘다. B는 `configs/train-b.yaml`을 쓴다(`code_dir: ~/myungchan`, 데이터 경로에 공백이 있다).
3. **명령 미리 보기** (실행 안 함): `.venv/bin/python -m model_lab train --config configs/train.yaml --dry-run` - 조립한 명령, 작업 폴더, 예상 어댑터 경로, 기록 파일 경로, 주의(넘기지 못한 값)가 나온다.
4. **[GPU 서버에서] 학습**: `.venv/bin/python -m model_lab train --config configs/train.yaml`. 서버 스크립트가 끝나면 어댑터 디렉터리에 `adapter_model.safetensors`와 `adapter_config.json`이 있는지, `base_model_name_or_path`가 설정의 기반 모델과 같은지, rank가 `output.max_lora_rank` 이하인지 확인하고 `<adapter_dir>.train-wrap.json`을 쓴다. 이 기록의 `dataset.content_hash`가 비어 있으면 서버 run 기록(`run.json`의 `dataset`)에서 찾아 `package-adapters --dataset-hash`에 준다. 기반 모델 커밋(`base_model_revision`)도 서버 run 기록에서 찾아 `--base-revision`으로 준다.
5. **학습 결과 -> 모델 패키지**: A와 B를 각각 학습한 뒤 아래 "학습 서버 어댑터를 얹는 절차"의 명령을 그대로 쓴다. 대응은 다음과 같다.

| 학습 감싸기 | package-adapters 인자 |
| --- | --- |
| A의 `output.adapter_dir` / B의 `output.adapter_dir` | `--adapter-a` / `--adapter-b` |
| `base_model.profile` | `--profile` |
| `dataset.id` / `dataset.version` / `dataset.content_hash` | `--dataset-id` / `--dataset-version` / `--dataset-hash` |
| 기록의 `base_model.base_model_revision` | `--base-revision` |
| 학습에 쓴 시스템 프롬프트 파일(서버 데이터 줄 안의 프롬프트와 같은 `prompts/lora_a.system.txt`, `lora_b.system.txt`) | `--prompt-a` / `--prompt-b` |
```
.venv/bin/python -m model_lab package-adapters --config configs/gpu-dev.yaml --profile qwen3-4b \
  --adapter-a <A의 adapter_dir> --adapter-b <B의 adapter_dir> \
  --prompt-a prompts/lora_a.system.txt --prompt-b prompts/lora_b.system.txt \
  --dataset-id <dataset.id> --dataset-version <dataset.version> --dataset-hash <sha256:...> \
  --out runs/attach-<날짜> --no-publish
```

### 프로파일 표와 model-runtime 연결

| 프로파일 | 기반 모델 | `max_lora_rank` | 용도 |
|---|---|---|---|
| `gpu-dev-12g` | `Qwen/Qwen2.5-1.5B-Instruct`(임시) | 16 | 12GB 호스트 파이프라인 검증(학습 값 포함) |
| `qwen3-4b` | `Qwen/Qwen3-4B` | 16 | 학습 서버 4B 어댑터 얹기 |
| `qwen3-8b` | `Qwen/Qwen3-8B` | 16 | 24GB급(model-runtime `rtx4090-24g`) 8B 어댑터 얹기 |

- 14B 프로파일은 없다: 14B는 A만 학습되어 있어(`head/docs/impact-d74.md`) A·B를 함께 얹는 `package-adapters`에 쓸 수 없고, model-runtime 쪽도 14B는 bf16으로 24GB에 들어가지 않는다고 적고 있다.
- 연결(코드 확인): model-lab은 프로파일을 `profiles/<이름>.yaml` 파일 이름으로만 찾는다(`model_lab/attach.py: load_profile`; `package-adapters --profile <이름>` 또는 설정의 `profile:`, `train` 설정에서는 `base_model.profile`). model-runtime의 프로파일 이름(`rtx4090-24g`)과 코드로 연결되지 않는다. 맞는 짝은 사람이 `base_model` 이름과 `max_lora_rank` 값을 같게 유지해 보장한다. 어댑터의 `base_model_name_or_path`가 프로파일의 `base_model.id`와 다르거나 `r`이 상한을 넘으면 `package-adapters`가 거부하고 아무것도 게시하지 않는다(`tests/test_attach.py`).

## 학습 서버 어댑터를 얹는 절차 (D84)

학습 서버가 만든 LoRA 어댑터(A, B 각각 `adapter_model.safetensors` + `adapter_config.json`)를 모델 패키지로 만들어 model-registry에 게시한다. 파이프라인(학습·평가·패키징·게시)을 다시 돌리지 않는다. 명령은 `model-lab package-adapters`(`model_lab/attach.py`, 시험 `tests/test_attach.py`)다. 이 길은 **평가하지 않는다**: 게시되는 EvaluationReport는 지표 `evaluated`가 `n=0`이고 노트에 "평가하지 않았다"를 적는다. 평가는 별도다.

현재 상태: 가짜 어댑터(작은 safetensors)로 패키지가 만들어지고 파일 레지스트리에 게시·재게시(멱등)되고 어댑터 해시가 다시 맞는 것까지 시험했다. 진짜 어댑터로 실제 모델을 불러 서빙하는 것은 GPU가 필요해 확인하지 못했다 [구멍: GPU].

1. **[사용자가 직접] 어댑터를 가져온다.** 이 리포지토리의 세션은 학습 서버에 접속하지 않는다. 학습 서버의 어댑터 디렉터리(예: `<run>/adapters/LORA_A`, `<run>/adapters/LORA_B`)를 읽기만 하는 복사로 가져온다. 예(경로는 서버에서 확인): `rsync -a openit:<경로>/adapters/LORA_A/ ./attach/LORA_A/` 와 `LORA_B`. 학습한 데이터셋의 `id`, `version`, `content_hash`는 서버 run 기록(`run.json`의 `dataset`)에 있다.
2. 시스템 프롬프트는 학습에 쓴 것과 같은 파일을 준다(`prompts/lora_a.system.txt`, `prompts/lora_b.system.txt`). 파일 내용이 패키지의 `system_prompt.sha256`에 들어가므로 학습 때와 다르면 안 된다.
3. 패키지만 만들어 본다(게시 안 함):
```
.venv/bin/python -m model_lab package-adapters --config configs/gpu-dev.yaml --profile gpu-dev-12g \
  --adapter-a attach/LORA_A --adapter-b attach/LORA_B \
  --prompt-a prompts/lora_a.system.txt --prompt-b prompts/lora_b.system.txt \
  --dataset-id <id> --dataset-version <version> --dataset-hash <sha256:...> \
  --out runs/attach-<날짜> --no-publish
```
   출력은 역할별 `package_id`, `version`(`attach-<어댑터 해시 앞 12자>`), `content_hash`, `adapter_hash`다. 매니페스트·평가 보고서는 `--out/package/`, 어댑터(두 파일 + `system_prompt.txt`만 복사)는 `--out/adapters/`에 있다.
4. 게시한다: 위 명령에서 `--no-publish`를 뺀다. 게시 대상은 설정의 `registry`다(`configs/gpu-dev.yaml`은 `MODEL_REGISTRY_URL`, 쓰기 토큰 `MODEL_REGISTRY_WRITE_TOKEN_FILE` 또는 개발용 `MODEL_REGISTRY_WRITE_TOKEN`). 같은 입력으로 다시 실행해도 안전하다(같은 패키지는 200으로 그대로, 다른 내용이 같은 버전이면 `CONFLICT`).
5. 검사(실패하면 아무것도 게시하지 않고 종료 코드 2): 어댑터 디렉터리에 `adapter_model.safetensors`와 `adapter_config.json`이 있어야 한다. `adapter_config.json`의 `base_model_name_or_path`가 프로파일의 `base_model.id`와 같아야 한다(다르면 거부). 프로파일에 `checks.max_lora_rank`가 있으면 어댑터의 `r`이 그 이하여야 한다. 기반 모델 `revision`은 프로파일 값(`main`)이거나 `--base-revision`으로 준 값이다. 어댑터 안에는 해석된 커밋이 없어서, 학습 run 기록의 `base_model_revision`을 알면 `--base-revision`으로 주는 것이 정확하다.
6. 서빙 설정(`serving`)은 `--config`의 값이 매니페스트에 들어간다. 서빙 쪽 model-runtime이 기대하는 값(`prompt_template_version`, `relax_policy_version` 등)과 같아야 한다.

## 레지스트리 서비스 운영 한도와 복구 (ML-1~3, ML-6)
- 환경 변수(기본값): `MODEL_REGISTRY_SOCKET_TIMEOUT`(30초, 연결 하나의 소켓 timeout), `MODEL_REGISTRY_MAX_CONNECTIONS`(32, 넘으면 503), `MODEL_REGISTRY_MAX_PUBLISHES`(1, 동시 게시 상한, 넘으면 503), `MODEL_REGISTRY_AUTH_FAIL_LIMIT`(같은 주소에서 60초에 10회 인증 실패하면 그 주소는 잠시 503), `MODEL_REGISTRY_MIN_FREE_GB`(1). 503은 `UNAVAILABLE`이라 클라이언트가 나중에 다시 시도한다. 읽기 경로는 동시 게시 상한을 받지 않는다. 상한을 넘은 요청은 기다리지 않고 바로 거절한다.
- 디스크: 게시는 본문을 받기 전에 (본문 크기의 2배 + `MODEL_REGISTRY_MIN_FREE_GB`)를 저장 볼륨의 여유와 비교하고 모자라면 503으로 거절하며 경보 로그("경보: 디스크 여유 부족")를 남긴다. 본문 tar는 풀고 나면 지우고, 저장은 풀어 둔 임시 폴더에서 하드 링크로 올린다(같은 파일 시스템이 아니면 복사). 최대 사용량은 이전 약 3배에서 약 2배(본문 + 풀어 둔 사본)로 줄었다. 라이브러리 `FileModelRegistry`는 기본이 복사이고 서비스만 `link_on_commit=True`를 쓴다. 경보 기준을 운영에서 무엇으로 볼지(로그 감시)는 deploy 몫이다.
- 재시작 후 정상화: 서비스는 시작할 때 `.tmp`(요청 임시 폴더)와 `adapters/*/.tmp-*`, `manifests/**/.tmp-*` 잔해를 지운다. 게시가 중단된 뒤 같은 패키지를 다시 게시하면 `list_versions`에 버전이 한 번만 나온다(이전 버전이 남긴 중복은 읽을 때 걸러진다). SIGTERM을 받으면 진행 중 요청을 마치고 멈춘다(컨테이너 종료 유예 시간이 가장 큰 게시보다 길어야 한다).
- 게시자 기록(ML-6): 매니페스트는 `content_hash`로 봉인되어 서버가 고칠 수 없으므로 서버는 매니페스트 옆 `manifests/<패키지>/<버전>.published.json`에 {토큰 역할(`model-lab`), 접속 주소, 시각, content_hash}를 남긴다(토큰 값은 남기지 않는다). 같은 패키지를 다시 게시해도 최초 기록을 덮어쓰지 않는다. `FileModelRegistry.get_publisher`로 읽는다. 쓰기 역할이 `model-lab` 하나뿐이라 행위자 구분은 접속 주소까지다. **서명(게시자 증명)은 하지 않았다: 도입 여부는 사용자·헤드 결정이며 제안은 아래다.**
  - 제안(미정): 게시 때 매니페스트 content_hash에 대한 분리 서명(예: 키 관리가 쉬운 minisign/ssh-keygen -Y sign)을 `<버전>.sig`로 저장하고 release-gate가 읽을 때 검증한다. 필요한 결정: 키를 누가 어디에 두는지, 검증 실패 때 release-gate의 동작.
- 전송(ML-14): 서버는 평문 HTTP다. TLS 종료 지점·운영 주소·방화벽·토큰 회전은 사용자·헤드 결정(U5)이라 코드를 바꾸지 않았다. 개발은 SSH 역터널을 쓴다(위). 토큰은 정적이고 회전하려면 재시작이 필요하다. 읽기 토큰 역할 이름은 자유 문자열이다.

## 학습 계보와 서버 코드 해시 (ML-5, ML-12)
- `package-adapters`에 `--train-record-a/-b`로 `train`이 쓴 `*.train-wrap.json`을 주면 기록의 역할, 데이터셋 id/version/content_hash, 기반 모델, 어댑터 파일 sha256을 입력과 대조하고(어긋나면 거부) 서버 스크립트 sha256, config 해시, 데이터 파일 해시, 하이퍼파라미터를 패키지 매니페스트 `ext."model-lab".lineage`에 넣는다. 기록이 없는 역할은 `lineage.train_record_sha256: null`로 표시하고 결과의 `warnings`와 표준 오류에 경고를 낸다. 기록은 학습 실행기의 자기 보고라 서버에서 실제로 그렇게 학습했다는 증명은 아니다. 정식 계약 필드(`lineage`)로 승격하려면 변경 요청이 필요하다(헤드 몫, 아직 쓰지 않았다). 기반 모델 revision은 기록의 `base_model_revision`이 비어 있으면 여전히 `--base-revision`으로 사람이 준다.
- 학습 설정 `server.script_sha256.<역할>`에 `arg_map`을 확인한 시점의 서버 스크립트 sha256을 적는다. `train-doctor`와 `train`(실행 전)이 스크립트가 읽히는 환경에서 현재 해시와 비교해 다르면 FAIL/중단하고(서버 코드가 바뀌어 `arg_map`을 다시 확인해야 한다), 값이 없으면 현재 해시를 알려 주며 WARN이다. 이 PC처럼 스크립트가 읽히지 않는 곳에서는 비교하지 못했다고만 말한다. 이 영역은 서버에 접속하지 않으므로 `configs/train.yaml`의 `script_sha256`은 비어 있다: 서버에서 `train-doctor`를 한 번 돌려 나온 해시를 헤드가 확인해 채운다(미정). 서버 절대 경로·타인 홈 경로를 설정에서 분리하는 일(프로파일 변수)은 아직 하지 않았다.
