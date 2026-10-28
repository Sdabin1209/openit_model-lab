# model-registry 서비스(I3 서버). 학습 잡(run-once)은 이미지가 아니라 requirements.lock 환경이다(D22).
# 계약 pin의 정본은 requirements-service.txt 한 곳이다. 빌드 명령이 거기서 뽑아 ARG로 넘기고, 이 파일에는 버전을 적지 않는다.
# 빌드(태그 의존, ssh 에이전트 필요):
#   SPEC=$(grep '^boundary-contracts' requirements-service.txt); docker build --ssh default -t model-lab-registry:dev \
#     --build-arg "BC_SPEC=$SPEC" --build-arg "BC_VERSION=${SPEC##*@}" --build-arg "REVISION=$(git rev-parse --short HEAD)" .
# 로컬 확인(ssh 없이 태그에서 만든 wheel): scripts/smoke_registry.sh
FROM scratch AS wheels
# 기본은 빈 컨텍스트. 로컬 확인에서는 --build-context wheels=vendor/wheels 로 바꾼다.

FROM python:3.12-slim AS build
ARG BC_SPEC
# 왜: 기본값을 두면 requirements-service.txt와 어긋난 pin이 조용히 설치된다. 비면 실패시킨다.
RUN test -n "${BC_SPEC}" || { echo "BC_SPEC build-arg required" >&2; exit 1; }
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/* \
 && mkdir -p -m 0700 /root/.ssh
# 왜: 키 스캔으로 처음 본 호스트 키를 신뢰하면 빌드 때 중간자가 키를 바꿔 끼울 수 있다(TOFU). GitHub가 공개한 키를 고정한다(ADR 0018).
COPY docker/github_known_hosts /root/.ssh/known_hosts
RUN python -m venv /opt/venv
RUN --mount=type=ssh --mount=type=bind,from=wheels,target=/wheels \
    /opt/venv/bin/pip install --no-cache-dir "${BC_SPEC}" "jsonschema==4.26.0" "waitress==3.0.2"

FROM python:3.12-slim
ARG REVISION
ARG BC_VERSION
# 왜: 라벨 값이 비거나 기본값으로 새면 추적이 거짓이 된다(D50). 빌드 인자가 없으면 실패시킨다.
RUN test -n "${REVISION}" && test -n "${BC_VERSION}" || { echo "REVISION and BC_VERSION build-args required" >&2; exit 1; }
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY model_lab/__init__.py model_lab/errors.py model_lab/registry.py /app/model_lab/
COPY model_lab/service /app/model_lab/service
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin registry && mkdir /data && chown registry /data
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MODEL_REGISTRY_ROOT=/data MODEL_REGISTRY_PORT=8080
VOLUME /data
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
LABEL io.openit.service="model-registry" \
      io.openit.revision="${REVISION}" \
      io.openit.contracts.boundary="${BC_VERSION}" \
      io.openit.contracts.survey-schema="n/a" \
      io.openit.turn-engine="n/a" \
      io.openit.model-runtime="n/a"
CMD ["python", "-m", "model_lab.service"]
