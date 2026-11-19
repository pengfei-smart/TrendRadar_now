
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trendradar.ai.client import AIClient
from trendradar.ai.usage_ledger import AIUsageLedger
from trendradar.ai.usage_ledger_factory import clear_ai_usage_ledger_cache
from trendradar.market_data.ai_model_provider import (
    ChatModelRequest,
    LiteLLMChatProvider,
)


class UnifiedUsageLedgerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "shared_usage.db"

        clear_ai_usage_ledger_cache()
        self.env_patch = patch.dict(
            os.environ,
            {"AI_USAGE_DB_PATH": str(self.db_path)},
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        clear_ai_usage_ledger_cache()
        self.temp_dir.cleanup()

    def test_ai_client_and_event_review_share_same_ledger(self):
        ai_response = SimpleNamespace(
            model="deepseek/deepseek-v4-flash",
            id="req-normal-ai",
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            ),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="normal result")
                )
            ],
        )

        client = AIClient(
            {
                "MODEL": "deepseek/deepseek-v4-pro",
                "API_KEY": "test-key",
                "MAX_TOKENS": 100,
            },
            feature="translation",
        )

        with patch(
            "trendradar.ai.client.completion",
            return_value=ai_response,
        ):
            result = client.chat(
                [{"role": "user", "content": "hello"}],
                usage_operation="translate_batch",
            )

        self.assertEqual("normal result", result)

        review_response = SimpleNamespace(
            id="req-event-review",
            model="deepseek/deepseek-v4-pro",
            usage=SimpleNamespace(
                prompt_tokens=200,
                completion_tokens=50,
                total_tokens=250,
            ),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"approved": true}'),
                    finish_reason="stop",
                )
            ],
        )

        provider = LiteLLMChatProvider(
            completion_callable=lambda **kwargs: review_response,
        )

        review_result = provider.complete(
            ChatModelRequest(
                model="deepseek/deepseek-v4-pro",
                system_prompt="system",
                user_prompt="user",
                usage_operation="chief_reviewer",
            )
        )

        self.assertEqual('{"approved": true}', review_result.content)

        ledger = AIUsageLedger(self.db_path)
        rows = ledger.list_records()


        self.assertEqual(2, len(rows))

        records = {
            row["request_id"]: row
            for row in rows
        }

        normal_record = records["req-normal-ai"]
        self.assertEqual("translation", normal_record["feature"])
        self.assertEqual("translate_batch", normal_record["operation"])

        review_record = records["req-event-review"]
        self.assertEqual("event_review", review_record["feature"])
        self.assertEqual("chief_reviewer", review_record["operation"])


if __name__ == "__main__":
    unittest.main()
