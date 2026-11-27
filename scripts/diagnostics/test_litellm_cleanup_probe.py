import asyncio
import contextlib
import importlib
import io

import scripts.diagnostics.litellm_exit_probe as probe

cleanup_module = importlib.import_module(
    "litellm.llms.custom_httpx.async_client_cleanup"
)

real_cleanup = cleanup_module.close_litellm_async_clients
should_fail = False


async def fake_cleanup():
    if should_fail:
        raise RuntimeError("simulated cleanup failure")


try:
    cleanup_module.close_litellm_async_clients = fake_cleanup

    output = io.StringIO()

    with contextlib.redirect_stderr(output):
        probe.install_cleanup_probe()

        assert probe._cleanup_probe_installed

        asyncio.run(
            cleanup_module.close_litellm_async_clients()
        )

        should_fail = True

        try:
            asyncio.run(
                cleanup_module.close_litellm_async_clients()
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("异常没有正确抛出")

    logs = output.getvalue()

    required = [
        "cleanup_probe_installed",
        "cleanup_started",
        "cleanup_finished",
        "cleanup_exception=RuntimeError",
        "cleanup_ended_with_error",
    ]

    for item in required:
        assert item in logs, f"缺少预期日志：{item}"
    expected_counts = {
        "cleanup_started": 2,
        "cleanup_finished": 1,
        "cleanup_ended_with_error": 1,
        "cleanup_exception=RuntimeError": 1,
        "[LiteLLM退出诊断] version=": 1,
    }

    for item, expected in expected_counts.items():
        actual = logs.count(item)
        assert actual == expected, (
            f"{item} 出现次数错误："
            f"expected={expected}, actual={actual}"
        )

    print("模拟测试通过")
    print("清理成功路径：通过")
    print("清理异常路径：通过")
    print("异常重新抛出：通过")
    print("日志状态区分：通过")

finally:
    cleanup_module.close_litellm_async_clients = real_cleanup
