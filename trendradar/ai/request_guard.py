
class AIRequestNotAllowedError(RuntimeError):
    """AI 请求未获得当前调度时间窗口授权。"""


class AIRequestGuard:
    """
    AI 请求授权守卫。

    默认严格遵循 Scheduler 的 Timeline。
    只有显式启用 debug_bypass_enabled，才允许绕过时间窗口。
    """

    def __init__(
        self,
        scheduler,
        *,
        debug_bypass_enabled=False,
    ):
        self.scheduler = scheduler
        self.debug_bypass_enabled = bool(debug_bypass_enabled)

    def is_allowed(self):
        """检查当前是否允许发起 AI 请求。"""
        if self.debug_bypass_enabled:
            return True

        if self.scheduler is None:
            return False

        return self.scheduler.is_ai_allowed_now()

    def check(self):
        """检查授权；未授权时抛出明确异常。"""
        if not self.is_allowed():
            raise AIRequestNotAllowedError(
                "当前 Timeline 时间窗口不允许发起 AI 请求"
            )
