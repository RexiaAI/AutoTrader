#!/usr/bin/env python3
"""
Script to fix current position issues:
1. Close URG short position (buy to cover)
2. Cancel stuck CGC duplicate SELL orders
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.broker.connection import IBConnection
from ib_insync import Stock, MarketOrder
import time


def main():
    print("=" * 60)
    print("Position Fix Script")
    print("=" * 60)
    
    # Connect to IBKR
    print("\n[1] Connecting to IBKR...")
    conn = IBConnection()
    if not conn.connect():
        print("ERROR: Failed to connect to IBKR")
        return
    
    ib = conn.ib
    print("Connected successfully")
    
    # Step 1: Cancel stuck CGC orders
    print("\n[2] Cancelling stuck CGC orders...")
    open_orders = ib.reqOpenOrders()
    cgc_orders_cancelled = 0
    
    for order in open_orders:
        if hasattr(order, 'contract') and order.contract.symbol == 'CGC':
            print(f"    Cancelling order {order.orderId}: {order.action} {order.totalQuantity} {order.contract.symbol}")
            ib.cancelOrder(order)
            cgc_orders_cancelled += 1
    
    # Also check trades
    open_trades = ib.openTrades()
    for trade in open_trades:
        if trade.contract.symbol == 'CGC':
            print(f"    Cancelling trade order {trade.order.orderId}: {trade.order.action} {trade.order.totalQuantity}")
            ib.cancelOrder(trade.order)
            cgc_orders_cancelled += 1
    
    print(f"    Cancelled {cgc_orders_cancelled} CGC orders")
    ib.sleep(1)
    
    # Step 2: Check for URG short position
    print("\n[3] Checking URG position...")
    positions = ib.positions()
    urg_position = None
    
    for pos in positions:
        if pos.contract.symbol == 'URG':
            urg_position = pos
            print(f"    Found URG: {pos.position} shares @ avg cost ${pos.avgCost:.4f}")
            break
    
    if urg_position is None:
        print("    No URG position found")
    elif urg_position.position >= 0:
        print("    URG is not a short position, no action needed")
    else:
        # Short position - buy to cover
        short_qty = abs(int(urg_position.position))
        print(f"\n[4] Closing URG short position...")
        print(f"    Buying to cover: {short_qty} shares")
        
        # Create contract
        contract = Stock('URG', 'AMEX', 'USD')
        ib.qualifyContracts(contract)
        
        # Place buy order
        buy_order = MarketOrder('BUY', short_qty)
        buy_order.transmit = True
        trade = ib.placeOrder(contract, buy_order)
        
        print(f"    Order placed: {trade.order.orderId}")
        
        # Wait for fill
        print("    Waiting for fill...")
        for _ in range(30):
            ib.sleep(1)
            if trade.isDone():
                break
        
        if trade.orderStatus.status == 'Filled':
            print(f"    FILLED: {trade.orderStatus.filled} shares @ ${trade.orderStatus.avgFillPrice:.4f}")
        else:
            print(f"    Status: {trade.orderStatus.status} (may need manual check in TWS)")
    
    # Final status
    print("\n[5] Current positions after fix:")
    positions = ib.positions()
    for pos in positions:
        print(f"    {pos.contract.symbol}: {pos.position} shares")
    
    print("\n[6] Remaining open orders:")
    open_trades = ib.openTrades()
    if not open_trades:
        print("    None")
    else:
        for trade in open_trades:
            print(f"    {trade.order.orderId}: {trade.order.action} {trade.order.totalQuantity} {trade.contract.symbol} - {trade.orderStatus.status}")
    
    # Disconnect
    conn.disconnect()
    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == "__main__":
    main()

