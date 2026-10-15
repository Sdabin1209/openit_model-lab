# model-registry 서비스(I3 서버). 학습 잡(run-once)은 이미지가 아니라 requirements.lock 환경이다(D22).
# 빌드(태그 의존, ssh 에이전트 필요): docker build --ssh default -t model-lab-registry:dev --build-arg REVISION=$(git rev-parse --short HEAD) .
# 로컬 확인(ssh 없이 태그에서 만든 wheel): scripts/smoke_registry.sh
FROM scratch AS wheels
# 기본은 빈 컨텍스트. 로컬 확인에서는 --build-context wheels=vendor/wheels 로 바꾼다.

FROM python:3.12-slim AS build
ARG BC_SPEC="boundary-contracts @ git+ssh://git@github.com/Sdabin1209/openit_boundary-contracts.git@v1.3.0"
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/* \
 && mkdir -p -m 0700 /root/.ssh && (ssh-keyscan github.com >> /root/.ssh/known_hosts 2>/dev/null || true)
RUN python -m venv /opt/venv
RUN --mount=type=ssh --mount=type=bind,from=wheels,target=/wheels \
    /opt/venv/bin/pip install --no-cache-dir "${BC_SPEC}" "jsonschema==4.26.0"

FROM python:3.12-slim
ARG REVISION=unknown
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
      io.openit.contracts.boundary="v1.3.0" \
      io.openit.contracts.survey-schema="n/a" \
      io.openit.turn-engine="n/a" \
      io.openit.model-runtime="n/a"
CMD ["python", "-m", "model_lab.service"]
