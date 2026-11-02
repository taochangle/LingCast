# API 文档（Swagger 网关）

通用 HTTP 微服务的 OpenAPI/Swagger 文档，由独立的 `docs` 网关
（nginx）聚合，经管理端 nginx 的 `/doc/` 前缀暴露：

| 前缀 | 服务 | 文档入口（本地） |
| --- | --- | --- |
| `/api-admin` | 管理端 API（gin-swagger） | [http://localhost:8080/doc/api-admin/swagger/index.html](http://localhost:8080/doc/api-admin/swagger/index.html) |
| `/api-live` | 观众端 / 直播聊天 API | [http://localhost:8080/doc/api-live/swagger/index.html](http://localhost:8080/doc/api-live/swagger/index.html) |
| `/api-scheduler` | Worker Webhook | [http://localhost:8080/doc/api-scheduler/swagger/index.html](http://localhost:8080/doc/api-scheduler/swagger/index.html) |
| `/service-rag` | 知识库服务（FastAPI /docs） | [http://localhost:8080/doc/service-rag/docs](http://localhost:8080/doc/service-rag/docs) |
| `/service-tts` | 旧版辅助语音服务（不等于当前常驻配音接口） | [http://localhost:8080/doc/service-tts/docs](http://localhost:8080/doc/service-tts/docs) |

> 网关落地页：<http://localhost:8080/doc/>。生产环境将 `localhost:8080` 替换为
> 实际入口域名即可；网关内部通过 `docs` 微服务的 nginx 按前缀反代到各服务。

## 鉴权说明

- 管理端主持、换装、项目速读、采集密钥等受保护操作需要管理员会话，实际路由见`backend/internal/router/admin.go`。
- `/api/collector/session`、`/api/collector/events`使用独立Bearer，不接受管理员Cookie替代认证。
- 观众、登录及Worker回调等接口分别按路由实现处理，不把整个`/api/live`或所有Webhook统称为管理员保护。
- Swagger网关是通用API参考，不代表全部流式模型接口都出现在其中。常驻模型`/readyz`、`/render`仅内部访问，见[流式手册](../cosyvoice-musetalk-live.md)。
