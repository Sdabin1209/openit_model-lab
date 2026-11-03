# 0031. 파이프라인 train 단계는 서버 정본 실행 경로를 부르고, transform은 서버 형식을 만든다

- 상태: 채택됨 (G06, D78). [0030](0030-train은-ssh로-서버-정본을-실행한다.md)을 이어 완성한다

## 맥락
`run`의 train 단계는 자체 학습기(`lora_train_hf`, prompt/completion 쌍 + 서빙 렌더링)를 썼다. 서버 정본 스크립트는 `messages`(system/user/assistant) 줄을 읽고 채팅 템플릿으로 인코딩한다(2026-11-03에 `ssh openit`으로 `train_lora_a.py`의 `load`/`encode`와 `lora_a_train.jsonl` 첫 줄을 읽음. 줄 키: `human_review`(선택), `messages`, `scenario_id`, `split`). 데이터 파일은 `<prefix>_{train,val,test}.jsonl`이다.

## 결정
1. `training.trainer`는 `server`(기본) 또는 `dry-run`이다. `hf`는 스키마에서 빼고 `make_trainer("hf")`는 폐기 오류를 낸다. 평가 백엔드가 쓰는 `lora_train_hf`의 모델 적재 함수는 그대로 둔다(학습기로는 쓰지 않는다).
2. transform: `trainer=server`이면 데이터셋 샘플을 서버 형식 줄(`server_row_convert.to_server_row`, 이미 서버 줄의 역변환으로 시험된 함수)로 `data/<role>/server/<prefix>_{train,val,test}.jsonl`에 쓴다. 학습 형식은 바꾸지 않는다. 시스템 프롬프트는 설정의 파일(서버 데이터의 system 메시지와 바이트가 같음, `tests/test_packager_v2.py`).
3. train: `ServerTrainer`가 (a) 그 폴더를 서버의 `dataset.upload_root/<run_id>/<role>/`로 rsync, (b) `train_flow.run_train`(= `model-lab train`과 같은 경로: doctor 점검, 서버에서 해시 확인 후 실행, 어댑터 회수)을 호출한다. 학습 설정은 역할별 `training.server_train_config`(configs/train*.yaml)이고 실행마다 바뀌는 값(데이터 폴더, 서버 출력 폴더 `<server_out_dir>/<run_id>`, 로컬 어댑터 폴더)만 덮어쓴다.
4. 결과(`TrainResult`)에서 `steps`는 선택이 되었다: 서버 스크립트가 단계 수를 알려 주지 않는다. `base_model_revision`은 요청한 값이다(서버가 해석한 커밋은 알지 못한다).

## 왜
- D78(학습 정본 = 서버 코드), 사용자 지시 "학습 형식 자체를 바꾸지 않고 서버가 이미 쓰는 형식에 맞춘다". 같은 데이터가 같은 어댑터를 내려면 같은 입력 형식이어야 한다.
- 경로에 `run_id`를 넣은 이유: 서버의 기존 기록·다른 실행과 겹치지 않고, 같은 입력(같은 run_id)의 재실행은 같은 폴더라 멱등이다. run_id는 설정·데이터셋·시드·라이브러리 해시에서 온다(`pipeline.py`).
- `upload_root`에 기본값을 두지 않은 이유: 서버에서 쓰기를 허락받은 폴더는 사람이 정한다. 값이 없으면 train 단계가 거부한다(`~/taehyeon` 등 타인 폴더에 쓰지 않는다).

## 검토한 대안과 버리는 이유
- 서버 안에서 파이프라인 전체를 실행: 서버에 의존(model-runtime 등) 설치가 필요하고 서버 소유 영역을 건드린다.
- 데이터를 올리지 않고 서버의 기존 데이터셋 폴더를 가리킴: 파이프라인이 받은 데이터셋 버전과 학습 데이터가 어긋날 수 있다(계보 위반).
- `hf` 학습기를 남기고 선택하게 함: 서버와 다른 어댑터가 정본처럼 게시될 수 있다.

## 결과
얻는 것: `run`이 끝까지 서버 정본으로 학습한다. 감수하는 것: (1) 가짜 ssh/rsync로만 시험했다(명령 모양, 서버 형식 키 구조, 업로드 위치). 진짜 업로드·학습은 확인하지 않았다 [구멍: GPU·서버·upload_root]. (2) `gpu-dev` 12GB 프로파일의 파이프라인 시험 설정들은 `server_train_config`가 없어 train 단계에서 거부된다(1.5B 임시 모델용 서버 학습 설정이 없다). (3) 서버 학습 중 단계 수·해석된 모델 커밋이 run 기록에 없다. (4) 서버 학습은 중간 재개를 지원하지 않는다(재실행은 처음부터).

## 되돌리는 조건
서버 스크립트가 다른 입력 형식으로 바뀌면(해시가 바뀌어 거부됨) transform의 변환과 이 ADR을 새 ADR로 대체한다.
