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


# 在 LiteLLM 的退出清理回调之后注册，
# 利用 atexit 的后进先出顺序优先记录状态。
atexit.register(diagnose)


def main():
    api_key = os.environ.get("AI_API_KEY", "").strip()
    model = os.environ.get("AI_MODEL", "").strip()
    api_base = os.environ.get("AI_API_BASE", "").strip()

    if not api_key:
        raise RuntimeError("AI_API_KEY is missing")

    if not model:
        raise RuntimeError("AI_MODEL is missing")

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
