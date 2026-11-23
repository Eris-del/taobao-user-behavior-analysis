from pathlib import Path
import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "processed" / "UserBehavior_clean.csv"
DB = ROOT / "data" / "processed" / "taobao_analysis.duckdb"
OUT = ROOT / "data" / "processed"

def sp(path: Path) -> str:
    return str(path).replace("'", "''")

con = duckdb.connect(str(DB))

con.execute(f"""
CREATE OR REPLACE TABLE p1_events AS
SELECT user_id, item_id, category_id, behavior_type, timestamp
FROM read_csv_auto('{sp(INPUT)}', header = true)
""")

def build_sessions(gap_seconds: int, table_name: str):
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(f"""
    CREATE TABLE {table_name} AS
    WITH ordered AS (
        SELECT
            *,
            LAG(timestamp) OVER (
                PARTITION BY user_id
                ORDER BY timestamp, behavior_type, item_id
            ) AS previous_timestamp
        FROM p1_events
    ), marked AS (
        SELECT *,
            CASE
                WHEN previous_timestamp IS NULL
                    OR timestamp - previous_timestamp > {gap_seconds}
                THEN 1 ELSE 0
            END AS new_session
        FROM ordered
    ), numbered AS (
        SELECT *,
            SUM(new_session) OVER (
                PARTITION BY user_id
                ORDER BY timestamp, behavior_type, item_id
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS session_number
        FROM marked
    )
    SELECT
        *,
        CAST(user_id AS VARCHAR) || '_' || CAST(session_number AS VARCHAR) AS session_id
    FROM numbered
    """)

def session_summary(table_name: str):
    return con.execute(f"""
    WITH sessions AS (
        SELECT
            session_id,
            user_id,
            COUNT(*) AS event_count,
            MIN(timestamp) AS session_start,
            MAX(timestamp) AS session_end,
            MIN(timestamp) FILTER (WHERE behavior_type = 'pv') AS pv_time,
            MIN(timestamp) FILTER (WHERE behavior_type = 'fav') AS fav_time,
            MIN(timestamp) FILTER (WHERE behavior_type = 'cart') AS cart_time,
            MIN(timestamp) FILTER (WHERE behavior_type = 'buy') AS buy_time
        FROM {table_name}
        GROUP BY session_id, user_id
    ), flags AS (
        SELECT *,
            pv_time IS NOT NULL AS has_pv,
            fav_time IS NOT NULL AND pv_time IS NOT NULL AND fav_time > pv_time AS fav_after_pv,
            cart_time IS NOT NULL AND pv_time IS NOT NULL AND cart_time > pv_time AS cart_after_pv,
            buy_time IS NOT NULL AND pv_time IS NOT NULL AND buy_time > pv_time AS buy_after_pv,
            buy_time IS NOT NULL AND cart_time IS NOT NULL AND buy_time > cart_time AS cart_to_buy,
            buy_time IS NOT NULL AND fav_time IS NOT NULL AND buy_time > fav_time AS fav_to_buy
        FROM sessions
    )
    SELECT
        COUNT(*) AS sessions,
        COUNT(*) FILTER (WHERE has_pv) AS pv_sessions,
        COUNT(*) FILTER (WHERE fav_after_pv OR cart_after_pv) AS interest_sessions,
        COUNT(*) FILTER (WHERE fav_after_pv) AS fav_sessions,
        COUNT(*) FILTER (WHERE cart_after_pv) AS cart_sessions,
        COUNT(*) FILTER (WHERE buy_after_pv) AS buy_sessions,
        COUNT(*) FILTER (WHERE fav_to_buy OR cart_to_buy) AS interest_to_buy_sessions,
        COUNT(*) FILTER (WHERE fav_to_buy) AS fav_to_buy_sessions,
        COUNT(*) FILTER (WHERE cart_to_buy) AS cart_to_buy_sessions,
        ROUND(COUNT(*) FILTER (WHERE fav_after_pv OR cart_after_pv)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_pv), 0), 6) AS pv_to_interest_rate,
        ROUND(COUNT(*) FILTER (WHERE fav_to_buy OR cart_to_buy)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE fav_after_pv OR cart_after_pv), 0), 6) AS interest_to_buy_rate,
        ROUND(COUNT(*) FILTER (WHERE buy_after_pv)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_pv), 0), 6) AS pv_to_buy_rate
    FROM flags
    """).fetchdf()

rows = []
for minutes in [15, 30, 60]:
    table = f"p1_sessions_{minutes}m"
    build_sessions(minutes * 60, table)
    result = session_summary(table).iloc[0].to_dict()
    result["gap_minutes"] = minutes
    rows.append(result)

    if minutes == 30:
        con.execute(f"""
        CREATE OR REPLACE TABLE p1_session_summary_30m AS
        SELECT
            session_id,
            user_id,
            COUNT(*) AS event_count,
            MIN(timestamp) AS session_start,
            MAX(timestamp) AS session_end,
            MIN(timestamp) FILTER (WHERE behavior_type = 'pv') AS pv_time,
            MIN(timestamp) FILTER (WHERE behavior_type = 'fav') AS fav_time,
            MIN(timestamp) FILTER (WHERE behavior_type = 'cart') AS cart_time,
            MIN(timestamp) FILTER (WHERE behavior_type = 'buy') AS buy_time
        FROM {table}
        GROUP BY session_id, user_id
        """)

        con.execute("""
        CREATE OR REPLACE TABLE p1_session_path_matrix AS
        WITH f AS (
            SELECT *,
                pv_time IS NOT NULL AS has_pv,
                fav_time IS NOT NULL AND pv_time IS NOT NULL AND fav_time > pv_time AS fav_after_pv,
                cart_time IS NOT NULL AND pv_time IS NOT NULL AND cart_time > pv_time AS cart_after_pv,
                buy_time IS NOT NULL AND pv_time IS NOT NULL AND buy_time > pv_time AS buy_after_pv,
                buy_time IS NOT NULL AND cart_time IS NOT NULL AND buy_time > cart_time AS cart_to_buy,
                buy_time IS NOT NULL AND fav_time IS NOT NULL AND buy_time > fav_time AS fav_to_buy
            FROM p1_session_summary_30m
        )
        SELECT
            CASE
                WHEN has_pv AND cart_after_pv AND cart_to_buy THEN 'pv→cart→buy'
                WHEN has_pv AND fav_after_pv AND fav_to_buy THEN 'pv→fav→buy'
                WHEN has_pv AND buy_after_pv AND NOT fav_after_pv AND NOT cart_after_pv THEN 'pv→buy直购'
                WHEN has_pv AND cart_after_pv THEN 'pv→cart未购'
                WHEN has_pv AND fav_after_pv THEN 'pv→fav未购'
                WHEN has_pv AND buy_after_pv THEN 'pv→buy其他路径'
                WHEN has_pv THEN '仅浏览或未形成后续行为'
                ELSE '无浏览行为会话'
            END AS path,
            COUNT(*) AS sessions,
            ROUND(COUNT(*)::DOUBLE / SUM(COUNT(*)) OVER (), 6) AS session_share
        FROM f
        GROUP BY path
        ORDER BY sessions DESC
        """)
        con.execute(f"COPY p1_session_path_matrix TO '{sp(OUT / 'p1_session_path_matrix.csv')}' (HEADER, DELIMITER ',')")
        con.execute(f"COPY p1_session_summary_30m TO '{sp(OUT / 'p1_session_summary_30m.csv')}' (HEADER, DELIMITER ',')")

pd.DataFrame(rows).sort_values("gap_minutes").to_csv(OUT / "p1_session_sensitivity.csv", index=False)
print("会话阈值敏感性：")
print(pd.DataFrame(rows).sort_values("gap_minutes").to_string(index=False))
print("\n30 分钟路径矩阵：")
print(con.execute("SELECT * FROM p1_session_path_matrix").fetchdf().to_string(index=False))
print(f"\n敏感性结果：{OUT / 'p1_session_sensitivity.csv'}")
print(f"路径矩阵：{OUT / 'p1_session_path_matrix.csv'}")

con.close()
