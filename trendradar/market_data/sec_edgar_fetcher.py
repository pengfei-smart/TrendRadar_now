# 本文件通过美国SEC官方EDGAR submissions JSON接口采集指定美股公司的申报事件，不进行全市场无差别抓取。
"""SEC EDGAR submissions fetcher for configured US-listed companies.

The SEC requires a descriptive User-Agent identifying the application and a
contact address. Configure SEC_EDGAR_USER_AGENT with an organization/app name
and monitored contact email before enabling network collection.
"""

from __future__ import annotations

import os
import re
import time
from typing import Iterable, Mapping, Optional

import requests

from trendradar.market_data.models import MarketEvent


SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

# Filing types with potentially material corporate events. The fetcher remains
# scoped to explicitly configured tickers to bound requests and avoid noise.
DEFAULT_FORMS = frozenset({"8-K", "10-K", "10-Q", "6-K", "20-F", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"})


class SECEdgarError(RuntimeError):
    """Raised when SEC data cannot be fetched or parsed safely."""


class SECEdgarFetcher:
    """Fetch recent SEC filings for an explicit ticker-to-CIK watchlist."""

    def __init__(
        self,
        companies: Optional[Mapping[str, str]] = None,
        user_agent: Optional[str] = None,
        timeout: int = 15,
        session=None,
        min_interval: float = 0.12,
        forms: Iterable[str] = DEFAULT_FORMS,
    ):
        self.companies = {
            str(ticker).strip().upper(): self._normalize_cik(cik)
            for ticker, cik in (companies or {}).items()
        }
        self.user_agent = (user_agent or os.getenv("SEC_EDGAR_USER_AGENT", "")).strip()
        self.timeout = max(3, int(timeout))
        self.session = session or requests.Session()
        self.min_interval = max(0.1, float(min_interval))
        self.forms = frozenset(str(form).strip().upper() for form in forms)
        self._last_request_at = 0.0

    @staticmethod
    def _normalize_cik(value: str) -> str:
        digits = re.sub(r"\D", "", str(value))
        if not digits or len(digits) > 10:
            raise ValueError(f"Invalid SEC CIK: {value!r}")
        return digits.zfill(10)

    def _get_json(self, url: str) -> dict:
        if not self.user_agent or "@" not in self.user_agent:
            raise SECEdgarError(
                "请设置 SEC_EDGAR_USER_AGENT，包含应用名称及可联系邮箱；"
                "未配置前不会请求 SEC。"
            )
        wait = self.min_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                    "Host": "data.sec.gov",
                },
            )
            self._last_request_at = time.monotonic()
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise SECEdgarError(f"SEC 请求或JSON解析失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise SECEdgarError("SEC 返回内容不是JSON对象")
        return payload

    def fetch(self, limit_per_company: int = 40) -> list[MarketEvent]:
        """Fetch latest filings for configured tickers; one request per company."""
        events: list[MarketEvent] = []
        limit = max(1, min(int(limit_per_company), 100))
        for ticker, cik in self.companies.items():
            payload = self._get_json(SEC_SUBMISSIONS_URL.format(cik=cik))
            recent = payload.get("filings", {}).get("recent", {})
            arrays = {
                key: value for key, value in recent.items()
                if isinstance(value, list)
            }
            lengths = {len(value) for value in arrays.values()}
            if not arrays or len(lengths) != 1:
                raise SECEdgarError(f"{ticker} 的 SEC recent filing 字段结构不完整")
            count = next(iter(lengths), 0)
            for index in range(min(count, limit)):
                form = str(arrays.get("form", [""] * count)[index]).upper()
                if form not in self.forms:
                    continue
                accession = str(arrays.get("accessionNumber", [""] * count)[index])
                filing_date = str(arrays.get("filingDate", [""] * count)[index])
                primary_document = str(arrays.get("primaryDocument", [""] * count)[index])
                if not accession or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                    continue
                if not primary_document or "/" in primary_document or "\\" in primary_document:
                    continue
                accession_path = accession.replace("-", "")
                url = SEC_ARCHIVES_URL.format(
                    cik=str(int(cik)), accession=accession_path, document=primary_document
                )
                description = str(arrays.get("primaryDocDescription", [""] * count)[index] or "").strip()
                title = f"{ticker} {form} filing"
                if description:
                    title += f" — {description[:180]}"
                events.append(MarketEvent(
                    event_id=f"sec_edgar:{ticker}:{accession}",
                    source="sec_edgar",
                    title=title,
                    published_at=filing_date,
                    event_type="sec_filing",
                    direction="neutral",
                    region="US",
                    url=url,
                    summary=f"SEC EDGAR申报；ticker={ticker}；form={form}；accession={accession}",
                ))
        return events
