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
| `MODEL_REGISTRY_READ_TOKENS` | `역할=토큰,역할=토큰` (예: `release-gate=...`). 읽기 경로만 허용 |
| `MODEL_REGISTRY_WRITE_TOKENS` | `model-lab=토큰`. 게시(`POST /v1/packages`). 역할은 `model-lab`만 허용(시작할 때 검사) |
| `MODEL_REGISTRY_ROOT` | 저장 위치(기본 `/data`, 볼륨 하나). 파일 레지스트리 그대로 |
| `MODEL_REGISTRY_PORT` / `MODEL_REGISTRY_HOST` | 기본 8080 / 0.0.0.0 |
| `MODEL_REGISTRY_MAX_BODY` | 게시 본문 상한 바이트(기본 4GiB) |
토큰이 하나도 없으면 시작하지 않는다(종료 코드 2). 경로·상태 코드는 `../boundary-contracts/HTTP.md`의 I3 절. `/readyz`는 저장소를 읽고 임시 파일을 쓸 수 있을 때 200.
이미지 라벨: `io.openit.service=model-registry`, `io.openit.revision`(빌드 인자 `REVISION`), `io.openit.contracts.boundary=v1.1.0`, `io.openit.contracts.survey-schema`/`turn-engine`/`model-runtime`은 `n/a`(서비스에 포함하지 않음). 확인: `scripts/smoke_registry.sh`(빌드 -> 빈 포트로 실행 -> healthz/readyz/토큰/게시/조회).

### run-once 잡
- 명령: `python -m model_lab run-once --config <설정 파일>` (또는 `model-lab run-once`). 출력은 JSON 한 줄: `{"action": "ran"|"skip", "run_id": ..., "dataset_version": ...}`.
- 종료 코드: 0 = 실행을 끝냈거나 할 일 없음, 2 = 오류(설정·데이터·환경. 재시도해도 같음), 3 = `UNAVAILABLE`(이웃·레지스트리 일시 장애, 같은 명령 재실행 가능).
- 멱등: 같은 (설정, 데이터셋 버전, 시드, 라이브러리 버전)이면 같은 `runs/<run_id>/`를 가리키고, 끝난 run은 `skip`. 중단된 run은 이어서(학습은 체크포인트부터). 같은 시각에 두 번 부르지 않는다(스케줄러가 직렬화).
- 작업 폴더: `runs_dir`(설정). 비밀 값은 설정 파일에 없고 환경 변수로만 받는다.
- 설정 형식(요약, 정본은 `model_lab/config.py`의 `CONFIG_SCHEMA`와 `docs/design.md` 3절). 예: `configs/gpu-dev-smoke.yaml`.
  - `profile`: 학습 프로파일(`profiles/gpu-dev-12g.yaml`: 기반 모델, 양자화, 최대 길이, 배치, 학습률, 에폭, GPU 수·메모리 비율, `checks.max_lora_rank`)
  - `inputs.dataset`: `{id, version?}` (version 생략 = 최신), `inputs.dataset_store`: 읽기 SDK 팩토리
  - `registry`: 게시 대상. 로컬 `{kind: file, path}` 또는 원격 `{kind: http, base_url, token_env, timeout}`(`boundary_contracts.clients.HttpModelRegistry`, v1.1.1 이상 설치 필요). `token_env`가 가리키는 환경 변수(쓰기 토큰)를 잡 실행 환경에 준다
  - 이웃 읽기 SDK는 모두 `{factory: "pkg.mod:fn", args: {..., token_env: 변수}}`로 고른다. `*_env` 인자는 환경 변수 값으로 바뀐다. `inputs.question_reader`, `inputs.failure_store` 자리는 열려 있으나 아직 쓰지 않는다
- 필요 환경: GPU 호스트에 `requirements.lock` + `requirements-train.txt`(torch 등), 모델 캐시(`~/.cache/huggingface`), `flock ~/openit-gpu/.gpu.lock`으로 GPU 직렬화(D47).
- 다른 호스트의 서버에 게시할 때: GPU 호스트에서 서버 주소로 HTTP가 닿아야 하고, 쓰기 토큰은 잡 실행 환경에만 둔다. 어댑터(수십~수백 MB)는 게시 때 tar로 올라간다.

## 환경 (CPU)
```
python3 -m venv .venv
.venv/bin/pip install -e ../boundary-contracts -e ../turn-engine -e ../model-runtime pytest pyyaml
# 태그로 설치(CI·이미지): .venv/bin/pip install -r requirements.txt   (requirements.lock이 확인된 전체 버전)
.venv/bin/python -m pytest -q                      # 61건
```
D22 (2): 서비스가 없는 run-once 잡이라 Dockerfile 없이 `requirements.lock`으로 환경을 고정한다.

## CPU로 파이프라인 끝까지 (모델 없이)
```
.venv/bin/python scripts/make_fixture_dataset.py fixtures/store      # golden으로 시험용 데이터셋 저장소(I2 대역)를 만든다
.venv/bin/python -m model_lab run --config configs/dryrun.yaml       # 학습(dry-run) -> 평가(fake-oracle) -> 패키징 -> 게시
.venv/bin/python -m model_lab run-once --config configs/dryrun.yaml  # 두 번째부터 skip
.venv/bin/python -m model_lab status --config configs/dryrun.yaml
```
`--stop-after <단계>`(`load_dataset|transform|train|evaluate|package|publish`)로 중간에서 멈출 수 있고, 같은 명령을 다시 실행하면 이어 간다.

## 학습 방법을 바꾸려면
설정 파일(또는 프로파일)의 `training`, `base_model`을 고친다. 코드를 고치지 않는다. 설정이 바뀌면 `config_hash`와 `run_id`가 바뀌어 새 run이 된다. 새 하드웨어는 `profiles/<이름>.yaml`을 추가하고 설정의 `profile:`이 가리키게 한다.

## GPU 호스트(gpu-dev) 실행 (D47)
1. 코드 보내기(비밀 값 없음, 호스트에서 GitHub 접속 없음):
   `rsync -a --exclude .venv --exclude .git --exclude runs --exclude fixtures ./ gpu-dev:openit-gpu/model-lab/`
   호스트에는 이웃 폴더가 없다. 태그에서 만든 wheel과 문진 스키마 사본을 `vendor/`(gitignore)에 두고 보낸다:
   `pip wheel --no-deps -w vendor/wheels "git+file://<경로>/boundary-contracts@v1.1.0"` (turn-engine@v0.2.0, model-runtime@v0.3.0도 같이), `vendor/survey-schema/{schemas,CONTRACT_VERSION}`.
2. 호스트 환경(처음 한 번): `cd ~/openit-gpu/model-lab && python3 -m venv .venv && .venv/bin/pip install vendor/wheels/*.whl pyyaml pytest -r requirements-train.txt`
3. 시험 데이터: `.venv/bin/python scripts/make_fixture_dataset.py fixtures/store`
4. 실행(긴 작업은 분리하고 로그를 본다. GPU 명령은 반드시 락으로 감싼다):
   `setsid nohup flock -w 1800 ~/openit-gpu/.gpu.lock timeout 3000 .venv/bin/python -m model_lab run --config configs/gpu-dev-smoke.yaml > smoke.log 2>&1 &`
5. 결과 가져오기: `rsync -a gpu-dev:openit-gpu/model-lab/runs/ runs/gpu/` 그리고 이쪽에서 게시한다(결과의 어댑터와 `run.json`, `package/*.manifest.json`, `eval/*.json`). 호스트의 레지스트리는 시험용이다.
6. 중단했다면 같은 명령을 다시 실행한다. 학습은 `runs/<run_id>/work/<ROLE>/ckpt`에서 이어 한다.
호스트 SSH가 두 번 실패하면 멈추고 STATUS에 적는다(반복 재시도 루프 금지). 서버는 항상 켜져 있지 않다.

## 자동 실행(스케줄러용)
`model-lab run-once --config <설정>`을 주기적으로 부르면 된다. 새 데이터셋 버전이 없으면 `{"action": "skip"}`을 내고 끝난다. 같은 시각에 두 번 부르지 않는다(같은 run 디렉터리를 쓴다. 필요하면 스케줄러가 직렬화한다).

## 문제 확인
| 증상 | 확인 |
| --- | --- |
| 종료 코드 2, `ConfigError` | 설정 형식 위반(알 수 없는 키 포함). 메시지의 경로를 본다 |
| `INTEGRITY_ERROR` | 데이터셋 파일/레지스트리 파일의 해시 불일치. 데이터셋을 다시 읽는다(`runs/<id>/dataset` 삭제 후 재실행) |
| `CONFLICT`(게시) | 같은 package_id·version에 다른 어댑터. 학습 비결정성 때문에 run 디렉터리를 지우고 다시 학습하면 생긴다. 기존 run 디렉터리를 재사용한다 |
| `TrainerUnavailable` | torch/CUDA 없음. `requirements-train.txt`와 GPU 확인 |
| `UnsupportedServingConfig` | `serving.*.constraint`가 평가 백엔드(`hf-transformers`/`fake`)와 다르다 |
