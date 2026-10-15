[결정 2026-10-15: I2는 같은 호스트의 파일 저장소이고 synthetic-data의 `FileDatasetStore`를 쓴다. 진입점은 그쪽 runbook. 설정은 `inputs.dataset_store.factory`에 그 팩토리를 적으면 된다.]

# CR-L004 데이터셋 읽기 SDK(I2)의 설치 이름과 생성 방법 (수신: 헤드)

- 현황: I2의 `DatasetStore` Protocol은 있으나 synthetic-data가 배포하는 실제 구현의 패키지 이름·생성자·인증(읽기 전용 토큰, CONVENTIONS 6절)을 모른다. model-lab은 `inputs.dataset_store.factory: "pkg.module:callable"` + `args`로 생성 함수를 설정에서 받는다. 지금은 `tests/doubles`의 대역(`tests.doubles.dataset_store:from_dir`)을 가리킨다.
- 요청: 실제 SDK가 나오면 팩토리 경로와 args(주소, 토큰 환경 변수 이름)를 알려 달라. 토큰은 환경 변수로만 받는다는 CONVENTIONS 7절에 따라 팩토리 호출자(진입점)가 읽도록 하겠다.
- 같은 질문: `list_versions`에 "새 버전이 생겼는지" 외에 변경 알림이 없다. run-once는 `list_versions()[-1]`을 읽어 이전에 끝낸 실행과 같은 입력이면 건너뛴다(스케줄러가 주기적으로 부른다고 가정, D39).
