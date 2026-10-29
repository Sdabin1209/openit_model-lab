#!/usr/bin/env bash
# 왜: 영역 환경 준비의 정본은 이 파일이다. 새 머신으로 옮길 때 README마다 설치법이 달라(이웃 폴더 editable 설치 등) 막혔으므로, 영역마다 같은 모양의 한 명령으로 모았다.
# 버전·태그는 requirements.lock / requirements-dev.txt가 정본이라 여기에 적지 않는다. 비밀 값은 다루지 않는다.
set -euo pipefail

# 계약 SetupEnvReport(boundary-contracts 2.6.0, U43, ADR 0025): 표준 출력의 마지막 줄은 결과 JSON 한 줄이다. 진행 기록은 모두 표준 오류로 보낸다.
# 왜 fd 3: 아래 모든 명령(pip 포함)의 표준 출력이 보고 줄과 섞이지 않게 원래 표준 출력을 3번에 보관한다.
exec 3>&1 1>&2
START=$SECONDS
report() {
  local rc=$? status=ok
  [ "$rc" -eq 0 ] || status=fail
  # 보고는 성공이든 실패든 낸다. 값은 고정 문자열과 숫자뿐이라 JSON 이스케이프가 필요 없다. 비밀 값은 다루지 않는다.
  printf '{"area":"model-lab","status":"%s","exit_code":%d,"seconds":%d,"environment":{"kind":"python-venv","path":".venv","test_command":[".venv/bin/python","-m","pytest","-q"]}}\n' \
    "$status" "$rc" "$((SECONDS - START))" >&3
}
trap report EXIT

# 어느 폴더에서 실행해도 이 리포 루트 기준으로 동작한다.
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# 왜: 멱등. .venv가 있으면 재사용하고 아래 설치로 핀과 같게 맞춘다.
if [ ! -x .venv/bin/python ]; then
  echo "[setup-env] .venv 생성"
  python3 -m venv .venv || { echo "[setup-env] 실패: python3 -m venv (python3-venv 패키지 필요할 수 있음)" >&2; exit 1; }
else
  echo "[setup-env] 기존 .venv 재사용"
fi

# 왜: lock을 먼저 설치하면 오래된 설치가 있어도 == 핀으로 맞춰진다. 이어서 dev(시험 도구)를 얹는다.
install() {
  .venv/bin/python -m pip install -r requirements.lock -r requirements-dev.txt
}

# 왜: 태그 설치는 GitHub 접속에 의존하므로 일시적 네트워크 실패에 한 번만 재시도한다.
if ! install; then
  echo "[setup-env] 설치 실패, 10초 뒤 한 번 재시도" >&2
  sleep 10
  if ! install; then
    echo "[setup-env] 실패: 의존 설치가 두 번 실패했다. 네트워크 또는 GitHub ssh 접근(태그 설치)을 확인하라." >&2
    exit 1
  fi
fi

.venv/bin/python -m pip check || { echo "[setup-env] 실패: pip check에서 의존 충돌" >&2; exit 1; }
echo "[setup-env] 완료. 시험: .venv/bin/python -m pytest -q"
