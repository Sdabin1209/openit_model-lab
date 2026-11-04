import sys

from .app import main

# 서비스 기동이 기본이고, 잡(deploy-jobs.json)은 부명령으로 부른다: backup, restore [--snapshot ID]
_JOBS = {"backup", "restore"}
if sys.argv[1:2] and sys.argv[1] in _JOBS:
    from ..components import link_backup
    from ..wiring.backup_verify import verify_data
    raise SystemExit(link_backup.main(sys.argv[2:]) if sys.argv[1] == "backup" else link_backup.restore_main(sys.argv[2:], verify=verify_data))
if sys.argv[1:]:
    print("사용: python -m model_lab.service [backup | restore [--snapshot <ID|latest>]]", file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(main())
