# LiteLLM 1.92.0 异步客户端退出清理验证记录



## 1. 验证目的



验证 LiteLLM 1.92.0 在应用退出清理阶段，能否正确关闭缓存中持有的异步 HTTP 客户端及其底层 aiohttp Session。



本次验证重点关注：



- HTTPX AsyncClient 是否能够正常关闭。

- aiohttp ClientSession 是否能够正常关闭。

- 清理函数是否能够在新建事件循环中执行。

- Session 创建时所属的原事件循环是否受到意外关闭。



## 2. 测试环境



| 项目 | 配置 |

|---|---|

| LiteLLM | 1.92.0 |

| Python | 3.10 |

| CI 环境 | GitHub Actions / Ubuntu |

| 测试脚本 | `scripts/diagnostics/test_litellm_aiohttp_session_cleanup.py` |

| 测试工作流 | `.github/workflows/litellm-aiohttp-cleanup-test.yml` |

| API Key | 不需要 |

| 真实 AI API 请求 | 不需要 |



## 3. 测试方法



1. 创建真实的 `aiohttp.ClientSession`。

2. 使用 `LiteLLMAiohttpTransport` 持有 Session，并设置 `owns_session=True`。

3. 创建使用该 Transport 的 `httpx.AsyncClient`。

4. 将客户端包装对象放入 LiteLLM 异步客户端缓存。

5. 使用一个全新的事件循环执行 `close_litellm_async_clients()`。

6. 检查清理前后的 Session 和 AsyncClient 状态。

7. 在测试结束时恢复原缓存，并执行兜底清理。



整个测试不发起外部 HTTP 请求，不依赖真实 AI 服务。



## 4. 测试结果



GitHub Actions Run：



https://github.com/pengfei-smart/TrendRadar_now/actions/runs/46016108130



实际输出：



```text

litellm_version=1.92.0

session_created=True

session_type=ClientSession

session_closed_before=False

async_client_closed_before=False

session_owner_loop_closed_before=False

session_owner_loop_running_before=False

cache_inserted=True

cleanup_loop_type=fresh_event_loop

session_closed_after=True

async_client_closed_after=True

session_owner_loop_closed_after_cleanup=False

RESULT=PASSED

final_session_closed=True

final_async_client_closed=True

```



**测试结论：PASSED。**



## 5. 源码验证结论



LiteLLM 1.92.0 的异步清理逻辑会遍历缓存中的客户端 Handler，并尝试关闭其底层 Transport 和异步客户端。



相关源码：



- https://github.com/BerriAI/litellm/blob/v1.92.0/litellm/llms/custom_httpx/async_client_cleanup.py

- https://github.com/BerriAI/litellm/blob/v1.92.0/litellm/llms/custom_httpx/aiohttp_transport.py



其中，aiohttp Transport 在拥有 Session 的情况下，通过 `ClientSession.close()` 关闭底层 Session。



本次实测结果与源码行为一致。



## 6. 验证范围与限制



本次验证确认：



- 真实 aiohttp ClientSession 可以成功创建。

- LiteLLM 异步清理函数可以关闭该 Session。

- HTTPX AsyncClient 可以成功关闭。

- 清理可以在新建事件循环中完成。

- 原 Session 所属事件循环在清理后没有被关闭。



本次尚未覆盖：



- 正在进行中的 HTTP 请求。

- 活跃连接池中的连接释放。

- 高并发下的客户端创建和退出竞争。

- 长时间运行任务中的异常退出场景。

- 所有 LiteLLM Provider 和 Handler 类型。



因此，本次结论只适用于实际覆盖的异步客户端清理路径，不代表所有网络资源退出场景都已验证。



## 7. 项目维护建议



1. 保留本次独立诊断脚本和 GitHub Actions 工作流。

2. LiteLLM 版本升级时重新运行该测试。

3. 如果升级后测试失败，先检查新版本的清理函数和 aiohttp Transport 实现。

4. 不要仅凭清理函数执行成功就判断资源已经关闭，必须继续断言 `session.closed` 和 `client.is_closed`。

5. 在没有新的证据之前，不需要为了本次测试修改 TrendRadar 生产代码。



## 8. 最终结论



LiteLLM 1.92.0 的异步客户端退出清理路径，已通过独立 GitHub Actions 测试。



本次测试覆盖的 HTTPX AsyncClient 和 aiohttp ClientSession 均成功关闭。



验证状态：**PASSED**。
