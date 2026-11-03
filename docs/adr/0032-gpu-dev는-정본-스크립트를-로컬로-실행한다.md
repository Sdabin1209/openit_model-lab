# 0032. gpu-dev는 서버 정본 스크립트를 가져와 이 PC에서 로컬로 실행한다

- 상태: 채택됨 (G06, D78). [0030](0030-train은-ssh로-서버-정본을-실행한다.md)의 "server.ssh를 지우면 서버 안에서 직접" 모드를 쓴다

## 맥락
[0031](0031-파이프라인-train-단계는-서버-정본을-부른다.md) 이후 파이프라인의 train 단계는 서버 정본만 부른다. gpu-dev 설정(`configs/gpu-dev*.yaml`)은 이 PC의 GPU(12GB)에서 파이프라인을 검증하는 것이 목적이라 학습 서버에 데이터를 올릴 수도, 올려서도 안 된다(`dataset.upload_root`는 사용자 결정 대기).

## 결정
1. `model-lab fetch-train-script --config configs/train.yaml [--dest train-scripts]`: 학습 서버의 정본 스크립트를 `ssh cat`으로 읽기만 해 로컬 폴더(`train-scripts/`, git 밖)에 저장한다. 설정의 `script_sha256`과 같을 때만 저장하고, 다르거나 비어 있으면 아무것도 쓰지 않고 거부한다. 임시 파일에 쓴 뒤 교체해 반쪽 파일이 남지 않는다.
2. gpu-dev용 학습 설정 `configs/train-gpu-dev-{a,b}.yaml`: `server.ssh` 없음, `code_dir: ../train-scripts`, 같은 `script_sha256`·`arg_map`(서버 설정과 같음, 시험이 대조), 기반 모델 `Qwen/Qwen2.5-1.5B-Instruct`, 하이퍼파라미터는 `profiles/gpu-dev-12g.yaml`(배치 1, 누적 8, 학습률 2e-4, 에폭 1, rank 16, alpha 32, dropout 0.05)의 값을 스크립트 인자로 옮겼다. 모든 `configs/gpu-dev*.yaml`의 역할이 이 설정을 가리킨다.
3. 로컬 모드에서 서버 스크립트는 어댑터를 `<out_dir>/adapter/`에 저장하므로 `output.server_out_dir`(로컬 경로)을 두고, 끝나면 `adapter_dir`로 복사한다(원격의 rsync에 해당). 파이프라인에서는 데이터 업로드 없이 변환 폴더를 그대로 `--data_dir`로 준다.
4. `dataset.upload_root`가 비었을 때의 거부 메시지가 사용자가 정할 것(서버에서 쓰기를 허락받은 폴더)과 적을 곳을 말한다.

## 왜
- 스크립트를 이 리포에 복사하지 않고(D78) 해시가 같은 바이트만 로컬 캐시에 둔다. 서버 코드가 바뀌면 해시 불일치로 가져오기·실행 둘 다 막힌다.
- gpu-dev는 파이프라인 검증용이다. 서버에 쓰지 않으니 서버 규칙(허락된 폴더, 기존 기록과 겹치지 않음)도 건드리지 않는다.
- 하이퍼파라미터는 프로파일(D47)에 이미 정해진 값을 따른다. 새로 정한 값이 없다.

## 검토한 대안과 버리는 이유
- gpu-dev 설정을 `trainer: dry-run`으로 되돌림: 서버 정본이 아닌 모의 학습으로 12GB GPU 검증의 의미가 없어진다.
- 스크립트를 리포에 vendor: 서버 코드 변경을 못 따라간다(0004).

## 결과
얻는 것: gpu-dev 파이프라인이 train 단계까지 서버 정본 경로로 간다(시험은 스크립트 대역). 감수하는 것: (1) 12GB에서 이 값으로 서버 스크립트(bf16 전체 적재, 양자화 없음)가 메모리에 들어가는지 **측정하지 않았다**. 실제 학습은 돌리지 않았다 [구멍: GPU]. (2) `server.python`은 학습 의존이 든 가상환경으로 사용자가 바꿔야 한다(`train-doctor`가 알려 준다). (3) 서버 스크립트는 재개를 지원하지 않아 gpu-dev의 resume 시험 설정(`*-resume-*`)은 학습을 처음부터 다시 한다.

## 되돌리는 조건
12GB에서 서버 스크립트가 들어가지 않으면 gpu-dev 전용 인자(예: 더 짧은 max_len)를 설정에서 바꾼다(코드 변경 없음). 서버 스크립트가 바뀌면 해시를 갱신하고 `fetch-train-script`를 다시 한다.
