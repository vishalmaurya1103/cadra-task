"""Read-only data preparation and lookup index for the Larkfield data room."""
from __future__ import annotations

import collections
import csv
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config

MONEY_PREFIX = re.compile(r"(?i)\busd\b|\$|,")
_INDEX: Optional[Dict[str, Any]] = None


def find_data_dir() -> Path:
    """Locate the data-room folder without hardcoding its UUID directory name."""
    candidates: List[Path] = []
    if config.DATA_DIR:
        candidates.append(Path(config.DATA_DIR))
    candidates.extend([config.REPO_ROOT, Path.cwd()])

    for base in candidates:
        if (base / "data" / "customers.csv").exists():
            return base
        for hit in base.glob("*/data/customers.csv"):
            return hit.parent.parent
    raise FileNotFoundError("Could not find <data-room>/data/customers.csv")


def parse_money(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = MONEY_PREFIX.sub("", text).strip()
    return round(float(text), 2)


def parse_date(value: Any):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def norm_email(value: Any) -> str:
    return str(value or "").strip().lower()


def norm_order_id(value: Any) -> str:
    text = str(value or "").strip().upper()
    digits = re.sub(r"\D", "", text)
    return f"LF-{digits}" if len(digits) == 6 else text


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _money_format(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("$"):
        return "$x"
    if text.upper().startswith("USD"):
        return "USD x"
    return "plain"


def _date_format(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "YYYY-MM-DD"
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
        return "DD/MM/YYYY"
    return "OTHER"


def prepare_index(data_dir: Optional[Path] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Read exported CSVs and build a normalized, read-only index."""
    data_dir = data_dir or find_data_dir()
    data_path = data_dir / "data"

    customer_rows = _read_csv(data_path / "customers.csv")
    order_rows = _read_csv(data_path / "orders.csv")
    item_rows = _read_csv(data_path / "order_items.csv")
    shipment_rows = _read_csv(data_path / "shipments.csv")
    refund_rows = _read_csv(data_path / "refunds.csv")

    customers_by_email: Dict[str, Dict[str, Any]] = {}
    customers_by_id: Dict[str, Dict[str, Any]] = {}
    duplicate_emails: List[str] = []
    for row in customer_rows:
        customer = {
            "customer_id": row["customer_id"].strip(),
            "name": row["name"].strip(),
            "email": row["email"].strip(),
            "country": row["country"].strip(),
            "created_at": row["created_at"].strip(),
            "loyalty_tier": row["loyalty_tier"].strip(),
        }
        customers_by_id[customer["customer_id"]] = customer
        key = norm_email(customer["email"])
        if key in customers_by_email:
            duplicate_emails.append(key)
        else:
            customers_by_email[key] = customer

    orders: Dict[str, Dict[str, Any]] = {}
    duplicate_order_ids: List[str] = []
    order_date_formats: collections.Counter[str] = collections.Counter()
    order_total_formats: collections.Counter[str] = collections.Counter()
    for row in order_rows:
        oid = norm_order_id(row["order_id"])
        order_date_formats[_date_format(row["order_date"])] += 1
        order_total_formats[_money_format(row["Order Total"])] += 1
        if oid in orders:
            duplicate_order_ids.append(oid)
            continue
        orders[oid] = {
            "order_id": oid,
            "customer_id": row["customer_id"].strip(),
            "order_date_raw": row["order_date"].strip(),
            "order_date": (parse_date(row["order_date"]) or "").isoformat()
            if parse_date(row["order_date"])
            else None,
            "status": row["status"].strip(),
            "order_total": parse_money(row["Order Total"]),
            "shipping_fee": parse_money(row["shipping_fee"]),
            "currency": row["currency"].strip(),
            "payment_method": row["payment_method"].strip(),
        }

    items: Dict[str, List[Dict[str, Any]]] = {}
    for row in item_rows:
        oid = norm_order_id(row["order_id"])
        qty = int(row["qty"])
        unit_price = parse_money(row["unit_price"]) or 0.0
        items.setdefault(oid, []).append(
            {
                "order_id": oid,
                "line_no": str(row["line_no"]).strip(),
                "sku": row["sku"].strip(),
                "product_name": row["product_name"].strip(),
                "qty": qty,
                "unit_price": unit_price,
                "line_value": round(qty * unit_price, 2),
            }
        )

    shipments: Dict[str, Dict[str, Any]] = {}
    duplicate_shipment_orders: List[str] = []
    for row in shipment_rows:
        oid = norm_order_id(row["order_id"])
        if oid in shipments:
            duplicate_shipment_orders.append(oid)
            continue
        shipments[oid] = {
            "shipment_id": row["shipment_id"].strip(),
            "order_id": oid,
            "carrier": row["carrier"].strip(),
            "tracking_no": row["tracking_no"].strip(),
            "status": row["status"].strip(),
            "shipped_at": row["shipped_at"].strip() or None,
            "delivered_at": row["delivered_at"].strip() or None,
            "last_event_at": row["last_event_at"].strip() or None,
        }

    refunds: Dict[str, List[Dict[str, Any]]] = {}
    refunded_by_line: Dict[str, Dict[str, float]] = {}
    for row in refund_rows:
        oid = norm_order_id(row["order_id"])
        line_no = str(row["line_no"]).strip()
        amount = parse_money(row["amount"]) or 0.0
        record = {
            "refund_id": row["refund_id"].strip(),
            "order_id": oid,
            "line_no": line_no,
            "amount": amount,
            "status": row["status"].strip(),
            "requested_at": row["requested_at"].strip(),
            "approved_by": row["approved_by"].strip(),
        }
        refunds.setdefault(oid, []).append(record)
        if record["status"] == "ISSUED":
            per_line = refunded_by_line.setdefault(oid, {})
            per_line[line_no] = round(per_line.get(line_no, 0.0) + amount, 2)

    customer_names = collections.Counter(c["name"] for c in customers_by_id.values())
    missing_shipments = sorted(set(orders) - set(shipments))
    missing_items = sorted(set(orders) - set(items))
    total_mismatches = []
    for oid, order in orders.items():
        line_sum = round(sum(x["line_value"] for x in items.get(oid, [])), 2)
        if items.get(oid) and abs((order["order_total"] or 0.0) - line_sum) > 0.001:
            total_mismatches.append(
                {"order_id": oid, "order_total": order["order_total"], "item_sum": line_sum}
            )

    index = {
        "data_dir": str(data_dir),
        "customers_by_email": customers_by_email,
        "customers_by_id": customers_by_id,
        "orders": orders,
        "items": items,
        "shipments": shipments,
        "refunds": refunds,
        "refunded_by_line": refunded_by_line,
    }
    report = {
        "data_dir": str(data_dir),
        "row_counts": {
            "customers": len(customer_rows),
            "orders": len(order_rows),
            "order_items": len(item_rows),
            "shipments": len(shipment_rows),
            "refunds": len(refund_rows),
        },
        "unique_counts": {
            "customers": len(customers_by_id),
            "orders": len(orders),
            "order_items_orders": len(items),
            "shipments_orders": len(shipments),
        },
        "duplicate_order_ids": sorted(set(duplicate_order_ids)),
        "duplicate_customer_emails": sorted(set(duplicate_emails)),
        "duplicate_shipment_orders": sorted(set(duplicate_shipment_orders)),
        "duplicate_customer_names": sum(1 for n in customer_names.values() if n > 1),
        "orders_missing_shipment": missing_shipments,
        "orders_missing_items": missing_items,
        "order_total_mismatches": total_mismatches,
        "order_date_formats": dict(order_date_formats),
        "order_total_formats": dict(order_total_formats),
        "customer_email_uppercase": sum(
            1 for c in customers_by_id.values() if c["email"] != c["email"].lower()
        ),
        "customer_email_whitespace": sum(
            1 for c in customers_by_id.values() if c["email"] != c["email"].strip()
        ),
        "shipment_nulls": {
            "delivered_at": sum(1 for s in shipments.values() if not s["delivered_at"]),
            "last_event_at": sum(1 for s in shipments.values() if not s["last_event_at"]),
        },
        "shipment_statuses": dict(
            collections.Counter(s["status"] for s in shipments.values())
        ),
        "qty_gt_one_lines": [
            {"order_id": oid, "line_no": line["line_no"], "qty": line["qty"]}
            for oid, lines in items.items()
            for line in lines
            if line["qty"] > 1
        ],
        "issued_refunds": sum(
            1 for entries in refunds.values() for r in entries if r["status"] == "ISSUED"
        ),
        "anomalies": {
            "last_event_before_shipped": [
                oid
                for oid, s in shipments.items()
                if s["last_event_at"]
                and s["shipped_at"]
                and (parse_datetime(s["last_event_at"]) or dt.datetime.max)
                < (parse_datetime(s["shipped_at"]) or dt.datetime.min)
            ],
            "order_date_after_shipped": [
                oid
                for oid, order in orders.items()
                if order["order_date"]
                and shipments.get(oid)
                and shipments[oid]["shipped_at"]
                and order["order_date"]
                > (parse_datetime(shipments[oid]["shipped_at"]).date().isoformat())
            ],
        },
    }
    return index, report


def write_index(
    index: Dict[str, Any],
    report: Dict[str, Any],
    out_dir: Optional[Path] = None,
) -> Tuple[Path, Path]:
    out_dir = out_dir or config.BUILD_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "data_index.json"
    report_path = out_dir / "data_report.json"
    with index_path.open("w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, default=str)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    return index_path, report_path


def load_index(rebuild: bool = False) -> Dict[str, Any]:
    global _INDEX
    if _INDEX is not None and not rebuild:
        return _INDEX
    if rebuild:
        index, report = prepare_index()
        write_index(index, report)
    else:
        index_path = config.BUILD_DIR / "data_index.json"
        if index_path.exists():
            with index_path.open("r", encoding="utf-8") as handle:
                index = json.load(handle)
        else:
            index, report = prepare_index()
            write_index(index, report)
    _INDEX = index
    return index
