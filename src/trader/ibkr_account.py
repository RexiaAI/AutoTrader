from __future__ import annotations


def get_account_value(account_values, tag: str, currency: str | None = None) -> float | None:
    """
    Extract a specific tag value from IBKR account values.

    If a currency-specific tag is missing, falls back to the BASE currency value for that tag.
    """
    # 1) Collect matches for the tag.
    matches = [v for v in account_values if getattr(v, "tag", None) == tag]
    if not matches:
        return None

    # 2) If a currency was requested, try exact match first.
    if currency is not None:
        for v in matches:
            if getattr(v, "currency", None) == currency:
                try:
                    return float(v.value)
                except (ValueError, TypeError):
                    continue

    # 3) Prefer BASE for tag-level values when no currency is specified (stable over time).
    if currency is None:
        for v in matches:
            if getattr(v, "currency", None) == "BASE":
                try:
                    return float(v.value)
                except (ValueError, TypeError):
                    continue

        # Fallback preference order for common setups.
        for pref in ("USD", "GBP"):
            for v in matches:
                if getattr(v, "currency", None) == pref:
                    try:
                        return float(v.value)
                    except (ValueError, TypeError):
                        continue

        # Last resort: first parseable value.
        for v in matches:
            try:
                return float(v.value)
            except (ValueError, TypeError):
                continue
        return None

    # 4) If not found and a currency was requested, try "BASE" or the account's primary line.
    for v in matches:
        if getattr(v, "currency", None) in ["BASE", "", None]:
            try:
                # We return the base value. IBKR handles conversion at trade time.
                return float(v.value)
            except (ValueError, TypeError):
                continue

    # 3) Last resort for cash: look for CashBalance if TotalCashValue is missing for a currency.
    if tag == "TotalCashValue" and currency is not None:
        for v in account_values:
            if getattr(v, "tag", None) == "CashBalance" and getattr(v, "currency", None) == currency:
                try:
                    return float(v.value)
                except (ValueError, TypeError):
                    continue

    return None


