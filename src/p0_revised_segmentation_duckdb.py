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
CREATE OR REPLACE TABLE p0_user_features AS
SELECT
    user_id,
    COUNT(*) AS total_events,
    COUNT(*) FILTER (WHERE behavior_type = 'pv') AS pv_count,
    COUNT(*) FILTER (WHERE behavior_type = 'fav') AS fav_count,
    COUNT(*) FILTER (WHERE behavior_type = 'cart') AS cart_count,
    COUNT(*) FILTER (WHERE behavior_type = 'buy') AS buy_count,
    COUNT(DISTINCT event_date) AS active_days,
    COUNT(DISTINCT event_date) FILTER (WHERE behavior_type = 'buy') AS buy_days,
    MAX(event_date) FILTER (WHERE behavior_type = 'buy') AS last_buy_date,
    MAX(event_date) AS last_active_date
FROM read_csv_auto('{sp(INPUT)}', header = true)
GROUP BY user_id
""")

con.execute("""
CREATE OR REPLACE TABLE p0_user_segments AS
WITH base AS (
    SELECT
        *,
        CASE
            WHEN last_buy_date IS NULL THEN NULL
            ELSE date_diff('day', CAST(last_buy_date AS DATE), DATE '2017-12-04')
        END AS recency_days
    FROM p0_user_features
), thresholds AS (
    SELECT quantile_cont(pv_count, 0.75) AS pv_q75 FROM base
), segmented AS (
    SELECT
        base.*,
        thresholds.pv_q75,
        CASE
            WHEN buy_count = 0 AND pv_count >= thresholds.pv_q75
                THEN '高浏览未购买'
            WHEN buy_count = 0
                THEN '低浏览未购买'
            WHEN recency_days <= 2 AND buy_count >= 3
                THEN '近期高频购买'
            WHEN recency_days <= 2 AND buy_count < 3
                THEN '近期低频购买'
            WHEN recency_days > 5 AND buy_count >= 3
                THEN '窗口早期高频购买'
            WHEN recency_days > 5 AND buy_count < 3
                THEN '近5日未购低频'
            ELSE '中期购买'
        END AS user_segment
    FROM base
    CROSS JOIN thresholds
)
SELECT * FROM segmented
""")

con.execute(f"""
COPY p0_user_segments TO '{sp(OUT / 'p0_user_segments_revised.csv')}' (HEADER, DELIMITER ',')
""")

summary = con.execute("""
SELECT
    user_segment,
    COUNT(*) AS users,
    ROUND(COUNT(*)::DOUBLE / SUM(COUNT(*)) OVER (), 6) AS user_share,
    ROUND(AVG(pv_count), 2) AS avg_pv_count,
    ROUND(AVG(cart_count), 2) AS avg_cart_count,
    ROUND(AVG(buy_count), 2) AS avg_buy_count,
    ROUND(AVG(active_days), 2) AS avg_active_days,
    ROUND(AVG(recency_days), 2) AS avg_recency_days
FROM p0_user_segments
GROUP BY user_segment
ORDER BY users DESC
""").fetchdf()
summary.to_csv(OUT / "p0_user_segment_summary_revised.csv", index=False)

thresholds = con.execute("""
SELECT
    MAX(pv_q75) AS pv_count_q75,
    2 AS recent_recency_max_days,
    5 AS early_window_min_recency_days,
    3 AS high_frequency_min_buy_events,
    '2017-12-04' AS observation_end_date
FROM p0_user_segments
""").fetchdf()
thresholds.to_csv(OUT / "p0_user_segment_thresholds.csv", index=False)

rules = pd.DataFrame([
    ["高浏览未购买", "buy_count = 0 且 pv_count >= pv_count_q75", "相对高浏览但观察期内未购买，优先做详情页和兴趣引导"],
    ["低浏览未购买", "buy_count = 0 且 pv_count < pv_count_q75", "未购买且浏览相对较少，不作为主要投放对象"],
    ["近期高频购买", "recency_days <= 2 且 buy_count >= 3", "近期购买频繁，适合会员维护和个性化推荐"],
    ["近期低频购买", "recency_days <= 2 且 buy_count < 3", "近期购买但频次较低，适合培养复购"],
    ["窗口早期高频购买", "recency_days > 5 且 buy_count >= 3", "窗口早期购买频繁，当前窗口后段未见购买，适合谨慎召回"],
    ["近5日未购低频", "recency_days > 5 且 buy_count < 3", "较早购买且频次较低，采用低成本触达"],
    ["中期购买", "3 <= recency_days <= 5", "介于近期和窗口早期之间，作为常规运营人群"],
], columns=["user_segment", "rule", "business_meaning"])
rules.to_csv(OUT / "p0_user_segment_rules.csv", index=False)

print("修订后的规则分层汇总：")
print(summary.to_string(index=False))
print("\n阈值：")
print(thresholds.to_string(index=False))
print(f"\n用户分层明细：{OUT / 'p0_user_segments_revised.csv'}")
print(f"分层汇总：{OUT / 'p0_user_segment_summary_revised.csv'}")
print(f"阈值表：{OUT / 'p0_user_segment_thresholds.csv'}")
print(f"规则表：{OUT / 'p0_user_segment_rules.csv'}")

con.close()
