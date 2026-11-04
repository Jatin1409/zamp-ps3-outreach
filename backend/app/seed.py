"""Seed data: the fictional seller and the one-click sample scenarios."""
from __future__ import annotations

from sqlalchemy import select

from .db import session
from .models import SellerProfile

LEDGERLINE = {
    "company_name": "Ledgerline",
    "fictional_notice": "Ledgerline is a fictional company created for this case study.",
    "product": "Ledgerline is an accounts-payable automation platform. It captures supplier invoices from email "
               "and PDF, matches them to purchase orders and receipts, routes exceptions to the right approver, "
               "and schedules payments in the ERP.",
    "ideal_customer": "Finance and AP teams at companies with 200 to 5,000 employees that process more than 1,000 "
                      "supplier invoices a month, especially while growing quickly, opening new entities or "
                      "locations, or migrating ERP.",
    "problems_solved": [
        "Invoice volume growing faster than the AP team",
        "Manual three-way matching and chasing approvals over email",
        "Month-end close delayed by unprocessed invoices",
        "Duplicate or incorrect supplier payments",
        "Adding new entities, currencies or ERPs without adding AP headcount",
    ],
    "proof_points": [
        "A 900-person logistics company cut invoice processing time from 9 days to 2 days",
        "Customers typically automate 70-80% of invoice matching within 90 days",
    ],
    "tone": "Plain, direct and peer-to-peer. Short sentences. No hype, no flattery.",
    "sender_name": "Alex at Ledgerline",
}



async def ensure_seed():
    async with session() as s:
        row = (await s.execute(select(SellerProfile).limit(1))).scalars().first()
        if row is None:
            s.add(SellerProfile(id="default", name="Ledgerline", data=LEDGERLINE))
            await s.commit()


async def get_seller_profile() -> SellerProfile:
    await ensure_seed()
    async with session() as s:
        return (await s.execute(select(SellerProfile).limit(1))).scalars().first()
