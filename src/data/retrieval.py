from ib_insync import Stock, util
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class MarketData:
    def __init__(self, ib_connection):
        self.ib = ib_connection.ib

    def get_contract(self, symbol, exchange='SMART', currency='USD'):
        """Creates an IBKR Stock contract."""
        contract = Stock(symbol, exchange, currency)
        self.ib.qualifyContracts(contract)
        return contract

    def fetch_historical_data(
        self,
        symbol,
        exchange='SMART',
        currency='USD',
        duration='1 Y',
        bar_size='1 day',
        use_rth=True,
        what_to_show='TRADES',
    ):
        """Fetches historical data for a given contract."""
        contract = self.get_contract(symbol, exchange, currency)
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1
        )
        if not bars:
            logger.warning(f"No historical data found for {symbol}")
            return pd.DataFrame()
            
        df = util.df(bars)
        df.set_index('date', inplace=True)
        return df

    def get_uk_stock(self, symbol):
        """Helper for UK stocks on LSE."""
        return self.fetch_historical_data(symbol, exchange='LSE', currency='GBP')

    def get_us_stock(self, symbol):
        """Helper for US stocks on SMART."""
        return self.fetch_historical_data(symbol, exchange='SMART', currency='USD')

