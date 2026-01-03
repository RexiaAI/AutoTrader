import logging
from datetime import datetime, timedelta, timezone


logger = logging.getLogger(__name__)


class IBKRNewsFetcher:
    """
    Fetches real news headlines via Interactive Brokers.
    This uses IBKR's news endpoints (no mocked headlines).
    """

    def __init__(self, ib_connection):
        self.ib = ib_connection.ib
        self._provider_codes = None

    def _get_provider_codes(self) -> str:
        if self._provider_codes is not None:
            return self._provider_codes

        providers = self.ib.reqNewsProviders()
        codes = [p.code for p in providers if getattr(p, "code", None)]
        if not codes:
            raise RuntimeError("No IBKR news providers available for this account/API session.")

        self._provider_codes = ",".join(codes)
        return self._provider_codes

    def fetch_headlines(self, contract, lookback_days: int = 7, limit: int = 10) -> list[str]:
        """
        Returns a list of headlines for a given qualified contract.

        Raises if no providers/headlines are available (so we do not fabricate data).
        """
        provider_codes = self._get_provider_codes()

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(lookback_days))

        # IBKR expects `YYYYMMDD HH:MM:SS` (in UTC).
        start_str = start.strftime("%Y%m%d %H:%M:%S")
        end_str = end.strftime("%Y%m%d %H:%M:%S")

        news_items = self.ib.reqHistoricalNews(
            contract.conId,
            provider_codes,
            start_str,
            end_str,
            int(limit),
            [],
        )

        headlines = [n.headline for n in news_items if getattr(n, "headline", None)]
        if not headlines:
            raise RuntimeError("IBKR returned no headlines for this contract in the requested window.")

        return headlines




