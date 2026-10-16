# `locust/` —— 压测（**只有一个家**）

> 📇 🔴 **两代压测件都在这里，⛔ 别读混** —— 它们**契约不同**。

## 📇 本目录索引
| 文件 | 代 | 契约 |
|---|---|---|
| `locustfile.py` · `locustfile_v2.py` · `locustfile_hybrid.py` | **上一代** | 🔴 **被 `app/tests/test_locust_payload.py` 守卫着**（那三个名字就写在该测试里） |
| **`locustfile_bench.py`** | **这一代**（`T5-8`） | 🔴 作者 2026-10-07 裁「**不进 pytest**」—— 它跑**代码里的参数**，给来仓库看的人看 |
| `README.md` | —— | `T5-8` 那份的口径与跑法 |

## 🔴 本层特有的规矩
- 🔴 **⛔ 不写进 `app/requirements.txt`** —— 那份清单会进 **本服务镜像**
- 🔴 **`locustfile_bench.py` 的 docstring 里那句「⛔ 不进 pytest」是【命令】，⛔ 不是建议**
- ⚠️ **`locustfile*.py` 两代【名字撞过】** —— `bench/` 那份并进来时改名为 `locustfile_bench.py`；
  ⇒ 改名字前先看 `app/tests/test_locust_payload.py` 的 `LOCUSTFILES`
- ⚠️ **CI 不编译本目录**（`ci.yml` 只 `compileall app/`）⇒ 语法错**CI 照样绿**，
  改完自己 `python -m py_compile locust/*.py`

## 🔴 打的是 `app/` 的端点 ⇒ 形状的权威在 `app/`（兄弟目录，⛔ 不自动加载）
> - 🔴 **上一代那 3 个**被 **`app/tests/test_locust_payload.py`** 守卫着（那 3 个名字就写在该测试里）
>   ⇒ 改脚本的请求形状时，**同批看它**
> - ⚠️ 跑法与参数口径 ⇒ `locust/README.md`

## 📍 往上读
- 仓库根 `CLAUDE.md` · **`app/tests/test_locust_payload.py`（形状的权威）**
