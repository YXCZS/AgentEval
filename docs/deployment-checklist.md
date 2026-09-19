# Agent Eval Workbench 上线部署清单

> 面向 Docker 单机部署。上线前逐项完成，避免把开发默认值带到生产。

## 一、多用户体系说明（本次新增）

平台现在支持**多用户 + 每人独立数据空间**：

- 每个用户有独立账号（邮箱 + 密码）和**独立 project 空间**，登录后只能看到自己的数据。
- 数据持久化在 PostgreSQL，关闭浏览器、下次登录后历史工作都在。
- **账号由管理员分配**（无开放注册）。管理员用「管理员账号登录 → 调用创建用户接口」创建成员账号。

### 鉴权方式（两种，边界清晰）

| 使用方 | 凭证 | 用途 |
|---|---|---|
| 人（浏览器） | 邮箱 + 密码 → JWT | 登录工作台，管理自己的空间 |
| 程序/CI（SDK） | Project API Key（`aek_...`） | 代码接入，读写对应 project |

## 二、上线前必做

### 1. 生成强随机密钥（安全红线）

用 `openssl rand` 生成，替换 `.env.production` 里的所有 `CHANGE_ME_*`：

```bash
openssl rand -hex 32   # 用于 API_KEY_SALT / WORKSPACE_SESSION_SECRET / JWT_SECRET
openssl rand -base64 32  # 用于 AGENT_EVAL_CREDENTIAL_ENCRYPTION_KEY
```

**绝不能**使用任何默认值：`local-compose-*`、`development-*`、`agent_eval:agent_eval`。

### 2. 配置首个管理员账号

在 `.env.production` 设置：

```
AGENT_EVAL_BOOTSTRAP_ADMIN_EMAIL=admin@your-domain.com
AGENT_EVAL_BOOTSTRAP_ADMIN_PASSWORD=<强密码>
```

首次启动会自动创建该管理员（之后不会再重复创建）。

### 3. 启动

```bash
cp .env.production.example .env.production
# 编辑 .env.production 填入真实值
docker compose -f infra/docker-compose.yml --env-file .env.production up -d --build
```

### 4. 数据库与 Redis 不暴露公网

`infra/docker-compose.yml` 中 `postgres` 和 `redis` 的宿主机端口映射**已默认注释掉**。生产环境保持注释状态，仅容器网络内可达。

### 5. HTTPS + 反向代理

参考 `infra/nginx.conf.example`：
- 只对外暴露 Nginx（443），前端 3000 / API 8000 由反代转发。
- 用 `certbot --nginx -d your-domain.com` 申请证书。
- 前端 `NEXT_PUBLIC_API_URL` 需指向公开 API 地址（如 `https://your-domain.com/api`）。

## 三、启动后验证

1. 浏览器打开 `https://your-domain.com`，用管理员账号登录。
2. 登录后能看到「我的空间」（一个独立的 project id）。
3. 创建数据（Agent Release / 数据集 / 实验），退出再登录，确认数据仍在。
4. 用管理员调用 `POST /auth/users` 创建成员账号，成员登录后应看到**空的独立空间**（看不到管理员的数据）。

## 四、成员账号管理（管理员操作）

### 可视化界面（推荐）

管理员登录后，左侧导航会出现「成员管理」入口，可以：

- **邀请成员**：填写邮箱、姓名、初始密码，创建账号（自动为其分配独立 project 空间）。
- **停用 / 启用**：停用后该账号无法登录，数据保留。
- **重置密码**：无需成员旧密码，直接设置新密码。
- **删除成员**：永久删除账号及其独立数据空间（不可恢复）。
- 管理员不能停用或删除自己。

### API 方式（等价）

管理员登录后，携带 JWT 调用：

```bash
# 列出所有成员
curl https://your-domain.com/api/auth/users \
  -H "Authorization: Bearer <管理员JWT>"

# 创建成员（会为其自动创建独立 project）
curl -X POST https://your-domain.com/api/auth/users \
  -H "Authorization: Bearer <管理员JWT>" \
  -H "Content-Type: application/json" \
  -d '{"email":"member@example.com","password":"<强密码>","display_name":"成员名","role":"member"}'

# 停用 / 启用成员
curl -X PATCH https://your-domain.com/api/auth/users/<user_id>/active \
  -H "Authorization: Bearer <管理员JWT>" \
  -H "Content-Type: application/json" \
  -d '{"active":false}'

# 重置成员密码
curl -X POST https://your-domain.com/api/auth/users/<user_id>/reset-password \
  -H "Authorization: Bearer <管理员JWT>" \
  -H "Content-Type: application/json" \
  -d '{"password":"<新密码>"}'

# 删除成员（级联删除其独立空间）
curl -X DELETE https://your-domain.com/api/auth/users/<user_id> \
  -H "Authorization: Bearer <管理员JWT>"
```

## 五、已知需后续处理（非阻塞）

- `sdk/python/dist/*.whl` 为旧构建，接入方应 `pip install sdk/python`（源码）而非旧 wheel。
