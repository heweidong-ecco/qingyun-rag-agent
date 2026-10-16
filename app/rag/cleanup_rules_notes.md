# 规则库的维护方式

新增规则： 只需编辑 cleanup_rules.json，添加一条新规则：

json
{"pattern": " Confidential ", "replacement": " ", "description": "去除机密水印标记"}
新增领域： 在 JSON 中添加一个新领域块：

json
"finance": [
    {"pattern": "账号：\\d+", "replacement": "[账号已脱敏]", "description": "银行账号脱敏"}
]

## default 领域

| 正则表达式 | 替换 | 说明 |
|------------|------|------|
| 第\s*\d+\s*页 | (空) | 去除页码 |
| Confidential | (空) | 去除机密水印 |

## finance 领域

| 正则表达式 | 替换 | 说明 |
|------------|------|------|
| 账号：\d+ | [账号已脱敏] | 银行账号脱敏 |