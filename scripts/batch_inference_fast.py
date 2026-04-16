import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from concurrent.futures import ProcessPoolExecutor, as_completed

# 상위 폴더 모듈 임포트
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from services.production_agent import ProductionManagementAgent

def process_store_batch(store_id, target_date, current_time, inv_df, prod_df, sales_df_all, campaign_df, store_prod_df):
    """단일 매장의 모든 품목을 처리하는 워커 함수"""
    # 에이전트는 대문자 컬럼을 기대할 수 있으므로, 에이전트용 DF는 다시 대문자로 임시 변환하여 전달
    def to_upper_df(df):
        return df.rename(columns={c: c.upper() for c in df.columns})

    store_inv = inv_df[inv_df['masked_stor_cd'] == store_id]
    store_prod = prod_df[prod_df['masked_stor_cd'] == store_id]
    store_sales = sales_df_all[sales_df_all['masked_stor_cd'] == store_id]
    store_prod_list = store_prod_df[store_prod_df['masked_stor_cd'] == store_id]

    agent = ProductionManagementAgent(
        inventory_df=to_upper_df(store_inv),
        production_df=to_upper_df(store_prod),
        sales_df=to_upper_df(store_sales),
        campaign_df=campaign_df, # 캠페인은 이미 소문자
        production_list_df=to_upper_df(store_prod_list)
    )
    
    results_dash = []
    results_stock = []
    results_pred = []
    
    for _, row in store_prod_list.iterrows():
        item_cd = str(row['item_cd'])
        item_nm = str(row['item_nm'])
        
        # 활동성 체크 (소문자 기준)
        has_activity = not store_sales[(store_sales['item_cd'] == item_cd) & (store_sales['sale_dt'] == target_date)].empty or \
                       not store_prod[(store_prod['item_cd'] == item_cd) & (store_prod['prod_dt'] == target_date)].empty or \
                       not store_inv[(store_inv['item_cd'] == item_cd) & (store_inv['stock_dt'] == target_date)].empty

        if not has_activity:
            results_dash.append({
                "store_cd": store_id, "item_cd": item_cd, "item_nm": item_nm, "target_date": target_date, "target_time": "14:00",
                "status": "안전", "current_qty": 0, "predict_1h_qty": 0, "chance_loss_reduction_pct": 0, "sales_velocity": 1.0,
                "alert_message": "판매 활동이 없습니다.", "can_produce": True, "tags_json": "[]", "prod_1st_json": "{}", "prod_2nd_json": "{}",
                "updated_at": datetime.now().isoformat()
            })
            continue

        try:
            status_data = agent.get_sku_status(store_id, item_cd, item_nm, current_time)
            results_dash.append({
                "store_cd": store_id, "item_cd": item_cd, "item_nm": item_nm, "target_date": target_date, "target_time": "14:00",
                "status": status_data.get("status"), "current_qty": status_data.get("current_qty"),
                "predict_1h_qty": status_data.get("predict_1h_qty"), "chance_loss_reduction_pct": status_data.get("chance_loss_reduction_pct"),
                "sales_velocity": status_data.get("sales_velocity"), "alert_message": status_data.get("alert_message"),
                "can_produce": status_data.get("can_produce"),
                "tags_json": json.dumps(status_data.get("tags", []), ensure_ascii=False),
                "prod_1st_json": json.dumps(status_data.get("avg_4w_prod_1st") or {}, ensure_ascii=False),
                "prod_2nd_json": json.dumps(status_data.get("avg_4w_prod_2nd") or {}, ensure_ascii=False),
                "updated_at": datetime.now().isoformat()
            })
            
            stock_flow = agent.engine.get_estimated_stock(store_id, item_cd, target_date)
            for hour in range(8, 23):
                t = datetime.strptime(target_date, "%Y%m%d").replace(hour=hour)
                if t in stock_flow.index:
                    results_stock.append({
                        "store_cd": store_id, "item_cd": item_cd, "target_date": target_date, "tmzon_div": f"{hour:02d}",
                        "estimated_stock": round(float(stock_flow.at[t, 'estimated_stock']), 1)
                    })
            
            for hour in range(14, 23):
                t = datetime.strptime(target_date, "%Y%m%d").replace(hour=hour)
                pred_qty = agent.predictor.predict_next_hour_sales(store_id, item_cd, t - timedelta(hours=1), to_upper_df(store_sales), campaign_df)
                results_pred.append({
                    "store_cd": store_id, "item_cd": item_cd, "target_date": target_date, "tmzon_div": f"{hour:02d}",
                    "predict_qty": round(float(pred_qty), 1)
                })
        except:
            pass
            
    return results_dash, results_stock, results_pred

def run_fast_batch_inference():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    engine = create_engine(db_url)
    
    target_date = "20260310"
    current_time = datetime.strptime(f"{target_date} 14:00:00", "%Y%m%d %H:%M:%S")
    
    print("🚀 [Fast Batch] 병렬 처리 기반 고속 분석 시작...")
    
    with engine.connect() as conn:
        inv_df = pd.read_sql(text('SELECT * FROM raw_inventory_extract'), conn)
        prod_df = pd.read_sql(text('SELECT * FROM raw_production_extract'), conn)
        sales_df_all = pd.read_sql(text('SELECT * FROM raw_daily_store_item_tmzon'), conn)
        store_prod_df = pd.read_sql(text('SELECT * FROM raw_stor_prod_item'), conn)
        
        # 모든 DF 컬럼명 소문자 통일
        for df in [inv_df, prod_df, sales_df_all, store_prod_df]:
            df.columns = [c.lower() for c in df.columns]
            for col in ['masked_stor_cd', 'item_cd', 'sale_dt', 'stock_dt', 'prod_dt']:
                if col in df.columns: df[col] = df[col].astype(str)

        campaign_query = text('SELECT m.cmp_cd, m.start_dt, m.fnsh_dt, i.item_cd, i.dc_rate_amt FROM raw_campaign_master m JOIN raw_campaign_item i ON m.cmp_cd = i.cmp_cd WHERE m.use_yn = \'Y\'')
        campaign_df = pd.read_sql(campaign_query, conn)
        campaign_df.columns = [c.lower() for c in campaign_df.columns]
        campaign_df['start_dt'] = pd.to_datetime(campaign_df['start_dt']).dt.strftime('%Y%m%d')
        campaign_df['fnsh_dt'] = pd.to_datetime(campaign_df['fnsh_dt']).dt.strftime('%Y%m%d')
        campaign_df['item_cd'] = campaign_df['item_cd'].astype(str)

    stores = store_prod_df['masked_stor_cd'].unique()
    # [시연 우선순위] 상위 10개 매장만 먼저 분석 (타임아웃 방지)
    selected_stores = stores[:10]
    print(f"📌 전체 {len(stores)}개 중 우선 분석 대상 {len(selected_stores)}개 매장 진행 중...")
    
    all_dash, all_stock, all_pred = [], [], []
    
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures = {executor.submit(process_store_batch, sid, target_date, current_time, inv_df, prod_df, sales_df_all, campaign_df, store_prod_df): sid for sid in selected_stores}
        
        for future in as_completed(futures):
            sid = futures[future]
            try:
                d, s, p = future.result()
                all_dash.extend(d)
                all_stock.extend(s)
                all_pred.extend(p)
                print(f"  ✅ 매장 {sid} 완료 ({len(d)}개 품목)")
            except Exception as e:
                print(f"  ❌ 매장 {sid} 실패: {e}")

    print("\n⏳ DB 최종 적재 중...")
    with engine.begin() as conn:
        pd.DataFrame(all_dash).to_sql('ai_dashboard_status', conn, if_exists='replace', index=False)
        pd.DataFrame(all_stock).to_sql('ai_estimated_stock', conn, if_exists='replace', index=False)
        pd.DataFrame(all_pred).to_sql('ai_hourly_prediction', conn, if_exists='replace', index=False)

    print(f"🎉 분석 완료! (총 {len(all_dash)}개 품목 상태 적재됨)")

if __name__ == "__main__":
    run_fast_batch_inference()
