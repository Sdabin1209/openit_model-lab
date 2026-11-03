"""파이프라인 train 단계의 서버 정본 학습기 (G06, ADR 0031). 자체 학습기를 대신한다.

하는 일: (1) transform이 만든 서버 형식 데이터(<prefix>_{train,val,test}.jsonl)를 서버의 허락된 폴더 아래 <run_id>/<role>/에 올린다(rsync),
(2) `model-lab train`과 같은 경로(train_flow.run_train)로 서버 정본 스크립트를 해시 고정해 실행하고 어댑터를 가져온다.
학습 방법은 이 코드가 아니라 학습 설정(configs/train*.yaml)과 서버 스크립트가 정한다."""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from ..components import server_train_wrap as w
from ..config import load_train_config
from .errors import PipelineError
from .train_flow import run_train


class ServerTrainer:
    name = "server"

    def __init__(self, *, run_id: str, base_dir: str, dataset: dict, log=print):
        self.run_id, self.base_dir, self.dataset, self.log = run_id, Path(base_dir), dataset, log

    def train(self, req: dict) -> dict:
        from ..config import RESUME_MSG, has_resume
        if has_resume(req["training"]):      # 파이프라인 설정에서 온 재개 항목도 같은 이유로 거부한다
            raise PipelineError(f"{RESUME_MSG}: training/{has_resume(req['training'])}")
        ref = req["training"].get("server_train_config")
        if not ref:
            raise PipelineError(f"{req['role']}: training.server_train_config가 없다. trainer=server는 학습 설정(configs/train*.yaml) 경로가 필요하다")
        path = (self.base_dir / ref).resolve()
        base = load_train_config(path)
        if base["role"] != req["role"]:
            raise PipelineError(f"{path.name}의 role({base['role']})이 단계의 역할({req['role']})과 다르다")
        local_data = Path(req["train_path"]).parent
        if not w.remote(base):
            # 이 컴퓨터가 학습 서버다(gpu-dev: ssh 없음, 정본 스크립트는 fetch-train-script가 가져온 로컬 사본). 올릴 곳이 없으니 변환 폴더를 그대로 읽는다
            cfg = load_train_config(path, {"dataset": {"dir": str(local_data), "id": self.dataset["id"], "version": self.dataset["version"]},
                                           "output": {"adapter_dir": str(req["adapter_dir"]), "server_out_dir": str(Path(req["work_dir"]) / "server_out")}})
            rec = run_train(cfg, log=self.log)
            return self._result(rec, cfg)
        root, out0 = base["dataset"].get("upload_root"), base["output"].get("server_out_dir")
        if not root:
            raise PipelineError(
                f"{path.name}: dataset.upload_root가 비어 있다. 사용자가 정해야 할 것: 학습 서버({w.remote(base)})에서 이 파이프라인이 데이터를 써도 되는 폴더. "
                "그 경로를 dataset.upload_root에 적는다(예: ~/<본인 폴더>/openit-uploads). 데이터는 <upload_root>/<run_id>/<role>/ 아래에 올라가고 기존 기록은 건드리지 않는다. "
                "서버에 올리지 않고 이 컴퓨터에서 실행하려면 server.ssh를 지운 설정(gpu-dev)을 쓴다")
        if not out0:
            raise PipelineError(f"{path.name}: output.server_out_dir(서버 쪽 출력 폴더)가 필요하다")
        # 왜 run_id를 경로에 넣나: 서버의 기존 기록·다른 실행과 겹치지 않고, 같은 입력의 재실행은 같은 폴더라 멱등이다
        dest = f"{root.rstrip('/')}/{self.run_id}/{req['role']}"
        cfg = load_train_config(path, {"dataset": {"dir": dest, "id": self.dataset["id"], "version": self.dataset["version"]},
                                       "output": {"adapter_dir": str(req["adapter_dir"]), "server_out_dir": f"{out0.rstrip('/')}/{self.run_id}"}})
        w.check_sandbox(cfg)        # 업로드 전에: 서버에 쓰기 전에 ~/dabin 밖 경로를 거부한다
        self.upload(cfg, local_data, dest)
        rec = run_train(cfg, log=self.log)
        return self._result(rec, cfg)

    @staticmethod
    def _result(rec: dict, cfg: dict) -> dict:
        return {"resumed_from_step": 0, "base_model_revision": rec["base_model"]["requested_revision"], "library_versions": {},
                "metrics": {"train_wrap_record": str(w.record_path(cfg)), "script_sha256": rec["server_script_sha256"]}}

    @staticmethod
    def upload(cfg: dict, local: Path, dest: str) -> None:
        r = w.ssh_run(cfg, "mkdir -p " + w._rq(dest))
        if r.returncode != 0:
            raise PipelineError(f"서버에 {dest}를 만들지 못했다(종료 코드 {r.returncode}). dataset.upload_root가 쓰기 허락된 폴더인지 확인한다")
        r = subprocess.run(["rsync", "-a", "-e", shlex.join(["ssh", *w.SSH_OPTS]), str(local) + "/", f"{w.remote(cfg)}:{dest}/"], env=w.child_env(cfg))
        if r.returncode != 0:
            raise PipelineError(f"데이터를 서버로 올리지 못했다(rsync 종료 코드 {r.returncode}): {dest}")
