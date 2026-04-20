from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import create_engine, text

from services.chance_loss_engine import ChanceLossEngine


class ChanceLossService:
    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url or os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc",
        )

    def estimate_from_db(
        self,
        store_id: str,
        item_id: str,
        target_date: str,
        unit_price: float = 1500.0,
    ) -> dict:
        """DB에서 판매 데이터를 조회하고 찬스로스를 추정"""
        engine = create_engine(self._db_url)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT * FROM raw_daily_store_item_tmzon
                    WHERE masked_stor_cd = :store_id
                      AND item_cd = :item_id
                      AND sale_dt = :target_date
                    """
                ),
                {"store_id": store_id, "item_id": item_id, "target_date": target_date},
            ).mappings().all()
        sales_df = pd.DataFrame([dict(r) for r in rows])
        if not sales_df.empty:
            sales_df.columns = [c.upper() for c in sales_df.columns]

        cle = ChanceLossEngine()
        return cle.estimate_chance_loss(
            sales_df=sales_df,
            production_df=pd.DataFrame(),
            store_id=store_id,
            item_id=item_id,
            target_date=target_date,
            unit_price=unit_price,
        )