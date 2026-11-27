import atexit
import importlib.metadata
import os
import sys

import litellm
from litellm import completion


def diagnose():
    prefix = "[LiteLLM退出诊断]"

    def log(message):
        print(f"{prefix} {message}", file=sys.stderr, flush=True)

    try:
        try:
            version = importlib.metadata.version("litellm")
        except Exception:
            version = "unknown"

        log(f"version={version}")

        cache = getattr(litellm, "in_memory_llm_clients_cache", None)
        if cache is None:
            log("client_cache=not_found")
            return

        cache_dict = getattr(cache, "cache_dict", {})
        handlers = list(cache_dict.values())

        log(f"cached_handler_count={len(handlers)}")

        for index, handler in enumerate(handlers):
            log(f"handler[{index}]={type(handler).__name__}")

            client = getattr(handler, "client", None)
            if client is None:
                client = getattr(handler, "_client", None)

            if client is None:
                log(f"handler[{index}].client=not_found")
                continue

            log(f"handler[{index}].client_type={type(client).__name__}")
            log(
                f"handler[{index}].client_closed="
                f"{getattr(client, 'is_closed', 'unknown')}"
            )

            transport = getattr(client, "_transport", None)
            if transport is None:
                log(f"handler[{index}].transport=not_found")
                continue

            log(f"handler[{index}].transport_type={type(transport).__name__}")

            session = getattr(transport, "client", None)

            log(
                f"handler[{index}].transport_client_type="
                f"{type(session).__name__}"
            )
            log(
                f"handler[{index}].transport_client_is_factory="
                f"{callable(session)}"
            )

            if session is None or callable(session):
                log(f"handler[{index}].aiohttp_session=not_created")
                continue

            log(
                f"handler[{index}].aiohttp_session_closed="
                f"{getattr(session, 'closed', 'unknown')}"
            )

            session_loop = getattr(session, "_loop", None)
            if session_loop is None:
                log(f"handler[{index}].session_loop=none")
            else:
                log(
                    f"handler[{index}].session_loop="
                    f"id:{id(session_loop)},"
                    f"closed:{session_loop.is_closed()},"
                    f"running:{session_loop.is_running()}"
                )

    except Exception as exc:
        log(f"diagnostic_error={type(exc).__name__}")


# LiteLLM 清理回调由 ensure_cleanup_registration() 显式注册。
# 最终诊断由 observed_cleanup() 在原始清理完成后执行。

_cleanup_probe_installed = False
_diagnose_executed = False


def run_diagnose_once():
    """确保最终诊断最多执行一次。"""
    global _diagnose_executed

    if _diagnose_executed:
        return

    _diagnose_executed = True
    diagnose()


def fallback_diagnose():
    """监控安装失败时执行备用诊断。"""
    run_diagnose_once()

def ensure_cleanup_registration():
    """显式触发 LiteLLM 的异步客户端清理回调注册。"""
    if getattr(litellm, "_async_client_cleanup_registered", False):
        return

    try:
        getattr(litellm, "__trendradar_probe_trigger__")
    except AttributeError:
        pass

    if not getattr(litellm, "_async_client_cleanup_registered", False):
        raise RuntimeError("LiteLLM async cleanup registration failed")

def install_cleanup_probe():
    """监控 LiteLLM 异步客户端清理前后的状态。"""
    global _cleanup_probe_installed

    prefix = "[LiteLLM清理监控]"

    def log(message):
        print(f"{prefix} {message}", file=sys.stderr, flush=True)

    if _cleanup_probe_installed:
        log("cleanup_probe_already_installed")
        return True

    try:
        import litellm.llms.custom_httpx.async_client_cleanup as cleanup_module

        original_cleanup = cleanup_module.close_litellm_async_clients

        async def observed_cleanup():
            log("cleanup_started")

            # 清理前快照
            snapshot("before")

            cleanup_succeeded = False
            try:
                await original_cleanup()
                cleanup_succeeded = True
            except BaseException as exc:
                log(f"cleanup_exception={type(exc).__name__}")
                raise
            finally:
                # 清理后快照
                snapshot("after")

                if cleanup_succeeded:
                    log("cleanup_finished")
                else:
                    log("cleanup_ended_with_error")

                # 原始清理结束后，再执行最终诊断。
                run_diagnose_once()

        def snapshot(stage):
            try:
                cache = getattr(
                    litellm,
                    "in_memory_llm_clients_cache",
                    None,
                )
                cache_dict = getattr(cache, "cache_dict", {}) if cache else {}
                handlers = list(cache_dict.values())

                log(f"{stage}.handler_count={len(handlers)}")

                for index, handler in enumerate(handlers):
                    client = getattr(handler, "client", None)

                    if client is None:
                        client = getattr(handler, "_client", None)

                    if client is None:
                        continue

                    log(
                        f"{stage}.handler[{index}].client_closed="
                        f"{getattr(client, 'is_closed', 'unknown')}"
                    )

                    transport = getattr(client, "_transport", None)

                    if transport is None:
                        continue

                    log(
                        f"{stage}.handler[{index}].transport="
                        f"{type(transport).__name__}"
                    )

                    session = getattr(transport, "client", None)

                    if session is not None and not callable(session):
                        log(
                            f"{stage}.handler[{index}].session_closed="
                            f"{getattr(session, 'closed', 'unknown')}"
                        )

            except Exception as exc:
                log(f"{stage}.snapshot_error={type(exc).__name__}")

        cleanup_module.close_litellm_async_clients = observed_cleanup
        _cleanup_probe_installed = True

        log("cleanup_probe_installed")
        return True

    except Exception as exc:
        log(f"cleanup_probe_install_error={type(exc).__name__}")
        return False

def main():
    api_key = os.environ.get("AI_API_KEY", "").strip()
    model = os.environ.get("AI_MODEL", "").strip()
    api_base = os.environ.get("AI_API_BASE", "").strip()

    if not api_key:
        raise RuntimeError("AI_API_KEY is missing")

    if not model:
        raise RuntimeError("AI_MODEL is missing")

    # 先注册备用诊断。
    # 后续 LiteLLM 注册的清理回调会先于备用诊断执行。
    atexit.register(fallback_diagnose)

    # 确保 LiteLLM 已注册退出清理回调。
    ensure_cleanup_registration()

    # 在首次 AI 请求之前安装清理监控。
    probe_installed = install_cleanup_probe()

    if not probe_installed:
        print(
            "[诊断] 清理监控安装失败，将使用备用退出诊断",
            file=sys.stderr,
            flush=True,
        )

    print(f"[诊断] model={model}", flush=True)
    print("[诊断] 开始执行一次真实 AI 请求", flush=True)

    params = {
        "model": model,
        "api_key": api_key,
        "messages": [
            {
                "role": "user",
                "content": "Reply with OK only."
            }
        ],
        "max_tokens": 16,
        "temperature": 0,
        "timeout": 45,
        "num_retries": 0,
        "max_retries": 0,
    }

    if api_base:
        params["api_base"] = api_base

    response = completion(**params)

    if not response.choices:
        raise RuntimeError("AI returned no choices")

    print("[诊断] AI 请求成功完成", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"[诊断] AI 请求失败：{type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
