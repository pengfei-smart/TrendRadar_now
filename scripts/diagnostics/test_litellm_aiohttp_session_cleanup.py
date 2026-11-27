
"""
独立验证 LiteLLM 1.92.0 的 aiohttp ClientSession 退出清理。

测试目标：
1. 创建真实 aiohttp.ClientSession。
2. 将持有该 Session 的 HTTPX AsyncClient 放入 LiteLLM 缓存。
3. 模拟 LiteLLM atexit 使用新事件循环执行异步清理。
4. 验证 aiohttp Session 和 HTTPX AsyncClient 是否均已关闭。
5. 记录 Session 所属事件循环及清理前后的状态。

不调用真实 AI API，不需要 API Key。
"""

import asyncio
import importlib.metadata
from types import SimpleNamespace


def log(message):
    print(f"[aiohttp清理测试] {message}", flush=True)


def get_litellm_version():
    return importlib.metadata.version("litellm")


async def create_real_session():
    import aiohttp
    import httpx

    from litellm.llms.custom_httpx.aiohttp_transport import (
        LiteLLMAiohttpTransport,
    )

    transport = LiteLLMAiohttpTransport(
        client=lambda: aiohttp.ClientSession(),
        owns_session=True,
    )

    # 主动创建真实 aiohttp ClientSession。
    session = transport._get_valid_client_session()

    if not isinstance(session, aiohttp.ClientSession):
        raise AssertionError(
            "没有创建真实 aiohttp.ClientSession"
        )

    if session.closed:
        raise AssertionError(
            "Session 创建后已经关闭"
        )

    # 创建使用上述 transport 的 HTTPX AsyncClient。
    client = httpx.AsyncClient(
        transport=transport
    )

    handler = SimpleNamespace(
        client=client
    )

    return handler, client, session


def run_in_loop(loop, awaitable):
    """在指定事件循环中运行异步操作。"""
    if loop.is_closed():
        raise RuntimeError("目标事件循环已经关闭")

    if loop.is_running():
        raise RuntimeError("目标事件循环仍在运行")

    asyncio.set_event_loop(loop)

    try:
        return loop.run_until_complete(awaitable)
    finally:
        asyncio.set_event_loop(None)


def run_cleanup_in_fresh_event_loop(cleanup_function):
    """
    模拟 LiteLLM atexit 清理：
    使用独立的新事件循环执行异步清理。
    """
    loop = asyncio.new_event_loop()

    try:
        run_in_loop(
            loop,
            cleanup_function()
        )
    finally:
        asyncio.set_event_loop(None)

        if not loop.is_closed():
            loop.close()


def main():
    import litellm

    from litellm.llms.custom_httpx.async_client_cleanup import (
        close_litellm_async_clients,
    )

    version = get_litellm_version()

    log(f"litellm_version={version}")

    if version != "1.92.0":
        raise RuntimeError(
            f"版本不匹配：要求 LiteLLM 1.92.0，当前为 {version}"
        )

    cache = litellm.in_memory_llm_clients_cache
    cache_dict = cache.cache_dict

    if not isinstance(cache_dict, dict):
        raise RuntimeError(
            "LiteLLM cache_dict 不是预期的 dict"
        )

    # 保存原始缓存内容。
    original_cache = dict(cache_dict)

    original_loop = None
    handler = None
    client = None
    session = None

    try:
        # 隔离测试缓存，避免清理其他真实客户端。
        cache_dict.clear()

        # 创建并保留 Session 所属的原始事件循环。
        original_loop = asyncio.new_event_loop()

        handler, client, session = run_in_loop(
            original_loop,
            create_real_session()
        )

        log("session_created=True")
        log(f"session_type={type(session).__name__}")
        log(f"session_closed_before={session.closed}")
        log(f"async_client_closed_before={client.is_closed}")
        log(
            "session_owner_loop_closed_before="
            f"{session._loop.is_closed()}"
        )
        log(
            "session_owner_loop_running_before="
            f"{session._loop.is_running()}"
        )

        if session.closed:
            raise AssertionError(
                "Session 在清理前已经关闭"
            )

        if client.is_closed:
            raise AssertionError(
                "AsyncClient 在清理前已经关闭"
            )

        if session._loop is not original_loop:
            raise AssertionError(
                "Session 所属事件循环与创建事件循环不一致"
            )

        # 将测试 handler 放入 LiteLLM 缓存。
        cache_dict["trendradar_aiohttp_cleanup_test"] = handler

        log("cache_inserted=True")

        # 原始事件循环保持打开，但不再运行。
        # LiteLLM 清理将在另一个全新的事件循环执行。
        log("cleanup_loop_type=fresh_event_loop")

        run_cleanup_in_fresh_event_loop(
            close_litellm_async_clients
        )

        log(f"session_closed_after={session.closed}")
        log(f"async_client_closed_after={client.is_closed}")
        log(
            "session_owner_loop_closed_after_cleanup="
            f"{original_loop.is_closed()}"
        )

        if not session.closed:
            raise AssertionError(
                "失败：aiohttp ClientSession 没有关闭"
            )

        if not client.is_closed:
            raise AssertionError(
                "失败：HTTPX AsyncClient 没有关闭"
            )

        log("RESULT=PASSED")

    finally:
        # 先在 Session 所属的原始事件循环中做兜底清理。
        # 这样即使跨事件循环清理失败，也尽力释放测试资源。
        if original_loop is not None and not original_loop.is_closed():

            if client is not None and not client.is_closed:
                try:
                    run_in_loop(
                        original_loop,
                        client.aclose()
                    )
                    log("fallback_client_cleanup=completed")
                except Exception as exc:
                    log(
                        "fallback_client_cleanup_error="
                        f"{type(exc).__name__}: {exc}"
                    )

            if session is not None and not session.closed:
                try:
                    run_in_loop(
                        original_loop,
                        session.close()
                    )
                    log("fallback_session_cleanup=completed")
                except Exception as exc:
                    log(
                        "fallback_session_cleanup_error="
                        f"{type(exc).__name__}: {exc}"
                    )

        # 恢复原始 LiteLLM 缓存。
        cache_dict.clear()
        cache_dict.update(original_cache)

        # 最后关闭原始事件循环。
        if original_loop is not None and not original_loop.is_closed():
            original_loop.close()

        # 报告最终资源状态。
        if session is not None:
            log(f"final_session_closed={session.closed}")

        if client is not None:
            log(f"final_async_client_closed={client.is_closed}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(
            f"RESULT=FAILED: {type(exc).__name__}: {exc}"
        )
        raise
