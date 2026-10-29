# 0026. 노출 선언(`io.openit.exposure`)은 model-registry의 모든 경로를 denied로 적는다

## 상태
채택 (2026-10-30, 헤드 브리프 U47, 계약 `Exposure` 스키마와 이미지 라벨 `io.openit.exposure`).

## 맥락
deploy의 `config/Caddyfile`이 영역 API의 경로·메서드·역할을 직접 알았다(구조 규칙 1 위반). 계약이 서비스가 자기 이미지에 노출 표를 선언하게 했다.

## 결정
- 정본은 리포의 `deploy-exposure.json` 하나다. `scripts/build-image.sh`가 계약 `Exposure`로 검증한 뒤 정렬한 압축 JSON으로 라벨 `io.openit.exposure`에 단다(`BuildImageResult.labels`에 나온다).
- model-registry의 경로(`/v1/packages`, `/v1/packages/*`, `/v1/eval-reports/*`, `/healthz`, `/readyz`)는 모두 `auth.kind=denied`다.

## 왜
`deploy/config/Caddyfile`에 이 서비스로 가는 블록이 없다(읽어서 확인: `reverse_proxy` 대상에 model-registry가 없고 `handle /v1/*`의 기본이 404). release-gate가 내부망에서 `GATE_I3_URL=http://model-registry:8080`으로 직접 읽는다(`deploy/config/release-gate.env`). 코드의 실제 라우트와는 어긋남이 없다(시험이 선언한 경로가 서버에 실제 라우트인지, 선언하지 않은 경로가 실제로 없는지 본다).

## 검토한 대안과 버리는 이유
- 읽기 경로를 `account`/`device-token`으로 열기: 현재 노출되지 않으므로 선언이 현실과 달라진다.
- 라벨 없이 두기: deploy가 이 서비스의 노출 여부를 계속 추측해야 한다.

## 결과
- 시험 `tests/test_exposure.py`(계약 통과, 선언 경로와 실제 라우트의 일치), `tests/test_build_image.py`(라벨 값이 파일과 같고 `Exposure`를 통과).
- 감수: 노출 표가 코드와 어긋나는 것은 시험이 잡지만, deploy Caddyfile과의 대조는 deploy 몫이다.

## 되돌리는 조건
이 서비스를 게이트웨이 밖으로 열기로 하면 경로별 인증을 적는다.
