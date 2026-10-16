"""`manage_users.py` —— **登录账号的运维工具**（建号 / 改口令 / 停用 / 列出来）。
"""
import secrets
import sys

from access.login_users import add_user, list_users, set_active


def _gen_password() -> str:
    """随机口令。⚠️ `token_urlsafe(12)` ⇒ **16 个字符**，含大小写与数字。
    ⛔ 别改成"好记的"那种 —— 这十几个字符是**给机器之间传一次**用的，不是给人记的。"""
    return secrets.token_urlsafe(12)


#: 🔴 **建号默认到期天数**（作者 定：**15 天**）。
#:
#: **为什么默认【会过期】而不是"永不过期"**：账号是发给**外面的人**的
#: ⇒ 默认放开的话，一年后你会有一堆"不记得是谁、也不知道还用不用"的活账号
DEFAULT_EXPIRE_DAYS = 15


def _usage() -> str:
    return (
        "用法：python manage_users.py <命令> [参数]\n"
        "\n"
        f"  add <用户名> [--days N]     建号（已存在则改口令）；到期天数默认 {DEFAULT_EXPIRE_DAYS} 天\n"
        "  set-password <用户名>       只换口令\n"
        "  revoke <用户名>             停用（**不删行**）\n"
        "  enable <用户名>             重新启用\n"
        "  list                        列全部账号（⛔ 不含口令哈希）\n"
        "\n"
        "  --days N    到期天数（覆盖默认）\n"
        "  --forever   永不过期（⚠️ 明确想要才用）\n"
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(_usage())
        return 2
    cmd, rest = argv[1], argv[2:]

    if cmd == "list":
        rows = list_users()
        if not rows:
            print("（库里一个账号都没有 —— 现在能登录的只有环境变量里那个主账号）")
            return 0
        print("%-24s %-8s %-20s %s" % ("USER_NAME", "ACTIVE", "CREATED_AT", "EXPIRES_AT"))
        for r in rows:
            print("%-24s %-8s %-20s %s" % (
                r["user_name"],
                "yes" if r["is_active"] else "NO",
                r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else "-",
                r["expires_at"].strftime("%Y-%m-%d %H:%M:%S") if r["expires_at"] else "永不过期",
            ))
        return 0

    if cmd in ("add", "set-password"):
        if not rest:
            print(_usage())
            return 2
        user_name = rest[0]
        days = DEFAULT_EXPIRE_DAYS          # ⚠️ 默认【会过期】—— 见上面那段理由
        if "--forever" in rest:
            days = None
        if "--days" in rest:
            try:
                days = int(rest[rest.index("--days") + 1])
            except (IndexError, ValueError):
                print("⛔ --days 后面要给一个整数（天数）")
                return 2
        password = _gen_password()
        add_user(user_name, password, expire_days=days)
        print("")
        print("  账号已就绪（口令**只显示这一次**，请立刻复制走）：")
        print("")
        print("    用户名：%s" % user_name)
        print("    口  令：%s" % password)
        print("")
        print("  到期：%s" % ("%d 天后" % days if days else "永不过期"))
        print("  额度：**默认就是 free 档**（新用户名查不到角色 ⇒ 回退成 FREE，见 access/permission.py）")
        print("")
        return 0

    if cmd in ("revoke", "enable"):
        if not rest:
            print(_usage())
            return 2
        n = set_active(rest[0], cmd == "enable")
        if n == 0:
            print("⛔ 没有这个账号：%s" % rest[0])
            return 1
        print("%s ⇒ %s（改了 %d 行）" % (rest[0], "已停用" if cmd == "revoke" else "已启用", n))
        return 0

    print(_usage())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
