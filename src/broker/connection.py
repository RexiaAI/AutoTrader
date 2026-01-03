import logging
from ib_insync import IB, util
import yaml
import os

logger = logging.getLogger(__name__)

# Connection timeout in seconds
IBKR_CONNECT_TIMEOUT = 30
IBKR_REQUEST_TIMEOUT = 60


class IBConnection:
    def __init__(self, config: dict | None = None, config_path: str = "config/config.yaml"):
        # Prefer a shared, validated config loader (single source of truth).
        if config is None:
            try:
                from src.utils.config_loader import load_config

                self.config = load_config(config_path=config_path)
            except Exception:
                # Fall back to reading directly for backwards compatibility.
                with open(config_path, "r", encoding="utf-8") as f:
                    self.config = yaml.safe_load(f)
        else:
            self.config = config
        
        self.ib = IB()
        # Set default timeout for all IB requests
        self.ib.RequestTimeout = IBKR_REQUEST_TIMEOUT
        broker = self.config.get("broker", {}) or {}
        self.host = str(broker.get("host", "127.0.0.1"))
        self.port = int(broker.get("port", 7497))
        self.client_id = int(broker.get("client_id", 10))

    def connect(self, timeout: int = IBKR_CONNECT_TIMEOUT) -> bool:
        """
        Connects to the Interactive Brokers TWS or Gateway.
        
        Args:
            timeout: Maximum seconds to wait for connection (default 30s)
        
        Returns:
            True if connected successfully, False otherwise
        """
        try:
            logger.info(f"Connecting to IBKR at {self.host}:{self.port} (Client ID: {self.client_id}, timeout: {timeout}s)")
            self.ib.connect(
                self.host, 
                self.port, 
                clientId=self.client_id,
                timeout=timeout,
                readonly=False,
            )
            
            # Enable delayed market data (Type 3)
            # This ensures we get data even if the account doesn't have real-time subscriptions.
            self.ib.reqMarketDataType(3)
            logger.info("Successfully connected to IBKR. Market data type set to DELAYED (3).")
            
            return True
        except TimeoutError:
            logger.error(f"IBKR connection timed out after {timeout}s")
            return False
        except ConnectionRefusedError:
            logger.error(f"IBKR connection refused at {self.host}:{self.port} - is TWS/Gateway running?")
            return False
        except Exception as e:
            logger.error(f"Failed to connect to IBKR: {type(e).__name__}: {e}")
            return False
    
    def ensure_connected(self, max_retries: int = 3) -> bool:
        """
        Ensures the connection is active, reconnecting if necessary.
        
        Returns:
            True if connected, False if all reconnection attempts failed
        """
        if self.ib.isConnected():
            return True
        
        logger.warning("IBKR connection lost, attempting to reconnect...")
        for attempt in range(1, max_retries + 1):
            logger.info(f"Reconnection attempt {attempt}/{max_retries}")
            if self.connect():
                return True
            import time
            time.sleep(2 ** attempt)  # Exponential backoff
        
        logger.error(f"Failed to reconnect to IBKR after {max_retries} attempts")
        return False

    def disconnect(self):
        """Disconnects from Interactive Brokers."""
        if self.ib.isConnected():
            self.ib.disconnect()
            logger.info("Disconnected from IBKR.")

    def is_paper_trading(self):
        """Checks if the current connection is a paper trading account."""
        # Typically paper trading ports are 7497 (TWS) or 4002 (Gateway)
        # But we can also check the account name/number if connected
        return self.port in [7497, 4002]

if __name__ == "__main__":
    # Basic testing block
    logging.basicConfig(level=logging.INFO)
    conn = IBConnection()
    if conn.connect():
        print(f"Connected. Paper trading: {conn.is_paper_trading()}")
        conn.disconnect()

