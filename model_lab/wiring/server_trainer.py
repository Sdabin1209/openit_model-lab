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
        ref = req["training"].get("server_train_config")
        if not ref:
            raise PipelineError(f"{req['role']}: training.server_train_config가 없다. trainer=server는 학습 설정(configs/train*.yaml) 경로가 필요하다")
        path = (self.base_dir / ref).resolve()
        base = load_train_config(path)
        if base["role"] != req["role"]:
            raise PipelineError(f"{path.name}의 role({base['role']})이 단계의 역할({req['role']})과 다르다")
        if not w.remote(base):
            raise PipelineError("파이프라인의 서버 학습은 server.ssh가 필요하다(이 컴퓨터로 데이터를 옮겨 올리는 길이 없다)")
        root, out0 = base["dataset"].get("upload_root"), base["output"].get("server_out_dir")
        if not root or not out0:
            raise PipelineError("dataset.upload_root(서버에 데이터를 올릴 허락된 폴더)와 output.server_out_dir가 학습 설정에 필요하다")
        # 왜 run_id를 경로에 넣나: 서버의 기존 기록·다른 실행과 겹치지 않고, 같은 입력의 재실행은 같은 폴더라 멱등이다
        dest = f"{root.rstrip('/')}/{self.run_id}/{req['role']}"
        cfg = load_train_config(path, {"dataset": {"dir": dest, "id": self.dataset["id"], "version": self.dataset["version"]},
                                       "output": {"adapter_dir": str(req["adapter_dir"]), "server_out_dir": f"{out0.rstrip('/')}/{self.run_id}"}})
        self.upload(cfg, Path(req["train_path"]).parent, dest)
        rec = run_train(cfg, log=self.log)
        return {"resumed_from_step": 0, "base_model_revision": rec["base_model"]["requested_revision"], "library_versions": {},
                "metrics": {"train_wrap_record": str(w.record_path(cfg)), "script_sha256": rec["server_script_sha256"]}}

    @staticmethod
    def upload(cfg: dict, local: Path, dest: str) -> None:
        r = w.ssh_run(cfg, "mkdir -p " + w._rq(dest))
        if r.returncode != 0:
            raise PipelineError(f"서버에 {dest}를 만들지 못했다(종료 코드 {r.returncode}). dataset.upload_root가 쓰기 허락된 폴더인지 확인한다")
        r = subprocess.run(["rsync", "-a", "-e", shlex.join(["ssh", *w.SSH_OPTS]), str(local) + "/", f"{w.remote(cfg)}:{dest}/"])
        if r.returncode != 0:
            raise PipelineError(f"데이터를 서버로 올리지 못했다(rsync 종료 코드 {r.returncode}): {dest}")
