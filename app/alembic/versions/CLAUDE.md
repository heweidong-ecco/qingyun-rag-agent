# `app/alembic/versions/` —— 迁移脚本

## 📇 本目录索引
| `828721f77ef2_add_is_active_to_api_keys.py` |
| `d171778a3bff_add_usage_logs_table.py` |
| `f12a761ae626_init.py` |

## 🔴 规矩
- ⚠️ **文件名由 alembic 自动生成**（`<hash>_<slug>.py`）⇒ ⛔ 别手工改名
- ⚠️ **每个迁移要能【前滚也要能回滚】**（`upgrade()` / `downgrade()` 都写）
- ⚠️ **改了迁移 ⇒ `app/schema.sql` 要重生成**（`bash scripts/gen_schema_sql.sh`）

## 📍 往上读
- `../CLAUDE.md` · 仓库根 `CLAUDE.md`
