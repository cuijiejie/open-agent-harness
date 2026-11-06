#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""初始化 / 修复默认管理员账号（幂等，可重复执行）

做什么
------
1. 确保 RBAC 表结构就绪（含 pa_users.password_hash 列）
2. `admin` 用户不存在 → 建号，挂内置 `admin` 角色（工具 `*`）
3. `admin` 存在但**没有密码** → 设置为默认密码 `admin`
4. `admin` 已有密码 → **不动**（除非显式 --force-reset）

什么时候用
----------
· 全新部署后，第一次登录之前（系统不再提供免密登录，没有账号就进不去）
· 从「用户名即可登录」的旧版本升级后，admin 的密码还是空的
· 误删 admin 之后恢复

用法
----
    cd /opt/open-agent-harness
    PYTHONPATH=. .venv/bin/python deploy/seed_admin.py
    PYTHONPATH=. .venv/bin/python deploy/seed_admin.py --password 'N3wPass!'   # 换密码
    PYTHONPATH=. .venv/bin/python deploy/seed_admin.py --force-reset            # 已有密码也重置
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许直接 `python deploy/seed_admin.py` 运行（把项目根目录加进 sys.path）
_ROOT: Path = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.infra.RbacService import rbac  # noqa: E402
from src.infra.utils.LogUtil import log  # noqa: E402

ADMIN_USER: str = "admin"
ADMIN_ROLE: str = "admin"


def main() -> int:
    ap = argparse.ArgumentParser(description="初始化默认管理员账号")
    ap.add_argument("--username", default=ADMIN_USER, help="管理员账号（默认 admin）")
    ap.add_argument("--password", default="admin", help="密码（默认 admin）")
    ap.add_argument("--force-reset", action="store_true", help="已有密码也覆盖")
    args = ap.parse_args()

    user: str = args.username.strip()
    pwd: str = args.password or "admin"

    if not rbac.ensure_schema():
        print("✗ RBAC 建表失败，检查 MySQL 连接配置（config/configs.cfg）")
        return 1

    row: dict | None = rbac.user_row(user)
    created: bool = False
    pwd_set: bool = False
    if row is None:
        res: dict = rbac.create_user(user, "管理员", [ADMIN_ROLE], pwd)
        if not res.get("ok"):
            print(f"✗ 建号失败: {res.get('error')}")
            return 1
        created = True
        pwd_set = True
    else:
        # 角色兜底：确保一定挂在 admin 角色上（手工删过角色也能恢复）
        rbac.assign(user, [ADMIN_ROLE])
        if args.force_reset or not row["password_hash"]:
            res = rbac.set_password(user, pwd)
            if not res.get("ok"):
                print(f"✗ 设密码失败: {res.get('error')}")
                return 1
            pwd_set = True
        else:
            print(f"· {user} 已有密码，保持不变（要覆盖请加 --force-reset）")

    perm: dict = rbac.permissions(user)
    print(f"{'✓ 已创建' if created else '✓ 已就绪'}: {user}")
    print(f"  角色: {perm.get('roles')}  管理员: {perm.get('is_admin')}  工具: {perm.get('tools')}")
    if pwd_set:
        print(f"  密码: 已设置为「{pwd}」，请登录后立即修改")
    else:
        print("  密码: 保持不变（要覆盖请加 --force-reset）")
    log.warning(f"默认管理员就绪: user={user}, role={ADMIN_ROLE}, created={created}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
