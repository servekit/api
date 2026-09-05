# servekit/api

servekit 的**契约仓库**：全部微服务的 proto 定义与多语言生成产物。服务仓库
（`user-service`、`testkit-service`…）不再持有 proto 与生成代码，只消费这里的产物。

## 目录结构

```
api/
├── buf.yaml                    整仓一个 buf module（STANDARD lint + FILE breaking）
├── buf.gen.go.yaml             Go 生成配置（gen/go 嵌套模块）
├── <domain>/v1/                每个域一个包（package <domain>.v1）
│   ├── service.proto           只放 service 定义（RPC 声明）
│   ├── enums.proto             域内枚举
│   ├── message.proto           领域消息（实体 / 值对象）
│   └── request_response.proto  RPC 请求/响应
├── common/v1/                  跨域公共类型（准入：被 ≥2 个域引用）
├── gen/go/                     生成 Go 代码（committed，module github.com/servekit/api/gen/go）
├── gen/openapi/                swagger 产物（仅 testkit —— 唯一带 http 注解的契约）
└── tools/                      一次性迁移工具（如 split_proto.py）
```

域清单：`gid` / `user` / `license` / `storage` / `messaging` / `telemetry` / `testkit`
/ `common`。注意 **message 域的包名是 `messaging.v1`**（`message` 与 proto 关键字
冲突，跨包引用无法解析；2026-09 迁移时改名）。

## 规则

1. **三分文件**：service / enums / message / request_response 各一个文件。不搞
   每消息一文件的极致 1-1-1（域内 RPC 数十级时导航成本超过收益）。
2. **common 准入标准**：枚举或消息被 **≥2 个域**引用才进 `common/v1`；域内类型
   一律留在自己包里。当前 common 只有 `Pong`（全部服务 Ping 的共享响应）。
3. **跨域引用直接 import**：testkit 需要 `UserStatus` 就
   `import "user/v1/enums.proto"` 用 `user.v1.UserStatus`——**禁止镜像复制**
   （镜像 + 同名同号约定没有编译期保障，值序漂移是静默数据错义）。
4. **生成集中、按语言一份**：Go 只在 `gen/go` 生成一次，全生态共享同一 Go 包
   （同 proto 两处生成 = 类型身份分裂 + proto 注册表 panic）。消费者（服务仓库）
   **不装 buf、不跑生成**，只 `require github.com/servekit/api/gen/go`（本地
   `replace ../api/gen/go`）。
5. **wire 兼容由 `make breaking` 守护**（对上一个 tag，FILE 级别）。包名/文件
   归属变更都是 breaking，需要显式决策。
6. **HTTP 注解只在网关型契约上**：目前仅 testkit（grpc-gateway + swagger 派生
   供前端）。后端服务的 proto 一律不带 `google.api.http`。

## 日常流程

```bash
make gen        # 改 proto 后重新生成（gen/go + gen/openapi）并构建校验
make lint       # buf lint
make breaking   # 对上一 tag 的破坏性检查
# PR 完整性：make gen && git diff --exit-code 必须为空（产物与 proto 同步）
```

服务仓库接入方式：

```go
import userv1 "github.com/servekit/api/gen/go/user/v1"
```

```go
// go.mod
require github.com/servekit/api/gen/go v0.0.0-00010101000000-000000000000
replace github.com/servekit/api/gen/go => ../api/gen/go
```

## 版本发布

Go 嵌套模块的 tag 带路径前缀：`git tag gen/go/v0.1.0`。消费方在脱离本地
replace 时按此 pin 版本。Rust（prost/tonic + pbjson）与 TS（protobuf-es）的
生成分区 `gen/rust`、`gen/ts` 为预留位，出现第一个对应语言消费者时启用。
