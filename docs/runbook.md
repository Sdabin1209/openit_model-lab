# 런북

## 환경 (CPU)
```
python3 -m venv .venv
.venv/bin/pip install -e ../boundary-contracts -e ../turn-engine -e ../model-runtime pytest pyyaml
# 태그로 설치(CI·이미지): .venv/bin/pip install -r requirements.txt   (requirements.lock이 확인된 전체 버전)
.venv/bin/python -m pytest -q                      # 40건
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
