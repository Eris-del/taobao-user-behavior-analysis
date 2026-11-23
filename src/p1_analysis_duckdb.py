from pathlib import Path
import duckdb
import pandas as pd
import math

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "processed" / "UserBehavior_clean.csv"
DB = ROOT / "data" / "processed" / "taobao_analysis.duckdb"
OUT = ROOT / "data" / "processed"

def sp(path: Path) -> str:
    return str(path).replace("'", "''")

con = duckdb.connect(str(DB))

# P1-6: feature period 11/25-11/30, outcome period 12/01-12/03.
con.execute(f"""
CREATE OR REPLACE TABLE p1_feature_users AS
SELECT
    user_id,
    COUNT(*) AS feature_events,
    COUNT(*) FILTER (WHERE behavior_type = 'pv') AS feature_pv,
    COUNT(*) FILTER (WHERE behavior_type = 'cart') AS feature_cart,
    COUNT(*) FILTER (WHERE behavior_type = 'fav') AS feature_fav,
    COUNT(*) FILTER (WHERE behavior_type = 'buy') AS feature_buy,
    COUNT(DISTINCT event_date) AS feature_active_days,
    MAX(event_date) FILTER (WHERE behavior_type = 'buy') AS feature_last_buy_date
FROM read_csv_auto('{sp(INPUT)}', header = true)
WHERE event_date <= '2017-11-30'
GROUP BY user_id
""")

con.execute(f"""
CREATE OR REPLACE TABLE p1_outcome_users AS
SELECT
    user_id,
    COUNT(*) AS outcome_events,
    MAX(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END) AS outcome_buy,
    COUNT(DISTINCT event_date) AS outcome_active_days
FROM read_csv_auto('{sp(INPUT)}', header = true)
WHERE event_date >= '2017-12-01'
GROUP BY user_id
""")

feature_q75 = con.execute("SELECT quantile_cont(feature_pv, 0.75) FROM p1_feature_users").fetchone()[0]

con.execute(f"""
CREATE OR REPLACE TABLE p1_segment_validation_detail AS
WITH base AS (
    SELECT
        f.*,
        date_diff('day', CAST(feature_last_buy_date AS DATE), DATE '2017-12-01') AS recency_days,
        COALESCE(o.outcome_events, 0) AS outcome_events,
        COALESCE(o.outcome_buy, 0) AS outcome_buy,
        COALESCE(o.outcome_active_days, 0) AS outcome_active_days
    FROM p1_feature_users f
    LEFT JOIN p1_outcome_users o USING (user_id)
), segmented AS (
    SELECT *,
        CASE
            WHEN feature_buy = 0 AND feature_pv >= {feature_q75} THEN '高浏览未购买'
            WHEN feature_buy = 0 THEN '低浏览未购买'
            WHEN recency_days <= 2 AND feature_buy >= 3 THEN '近期高频购买'
            WHEN recency_days <= 2 AND feature_buy < 3 THEN '近期低频购买'
            WHEN recency_days > 5 AND feature_buy >= 3 THEN '窗口早期高频购买'
            WHEN recency_days > 5 AND feature_buy < 3 THEN '近5日未购低频'
            ELSE '中期购买'
        END AS user_segment
    FROM base
)
SELECT * FROM segmented
""")

validation = con.execute("""
SELECT
    user_segment,
    COUNT(*) AS users,
    SUM(outcome_buy) AS outcome_buyers,
    SUM(CASE WHEN outcome_events > 0 THEN 1 ELSE 0 END) AS outcome_active_users,
    ROUND(AVG(feature_pv), 2) AS avg_feature_pv,
    ROUND(AVG(feature_cart), 2) AS avg_feature_cart,
    ROUND(AVG(feature_buy), 2) AS avg_feature_buy,
    ROUND(SUM(outcome_buy)::DOUBLE / NULLIF(COUNT(*), 0), 6) AS outcome_3d_purchase_rate,
    ROUND(SUM(CASE WHEN outcome_events > 0 THEN 1 ELSE 0 END)::DOUBLE / NULLIF(COUNT(*), 0), 6) AS outcome_3d_active_rate,
    ROUND(SUM(outcome_buy)::DOUBLE / NULLIF(SUM(CASE WHEN feature_buy > 0 THEN 1 ELSE 0 END), 0), 6) AS repeat_purchase_rate
FROM p1_segment_validation_detail
GROUP BY user_segment
ORDER BY outcome_3d_purchase_rate DESC
""").fetchdf()

def wilson(success, total, z=1.96):
    if total == 0:
        return (None, None)
    p = success / total
    denom = 1 + z*z/total
    centre = p + z*z/(2*total)
    margin = z * math.sqrt((p*(1-p) + z*z/(4*total))/total)
    return ((centre-margin)/denom, (centre+margin)/denom)

cis = [wilson(int(row.outcome_buyers), int(row.users)) for row in validation.itertuples()]
validation["purchase_rate_ci95_low"] = [x[0] for x in cis]
validation["purchase_rate_ci95_high"] = [x[1] for x in cis]
validation["priority_note"] = validation["user_segment"].map({
    "高浏览未购买": "中优先：先验证详情页和兴趣引导",
    "低浏览未购买": "低优先/不建议大规模投放",
    "近期高频购买": "高优先：维护和复购",
    "近期低频购买": "中优先：培养复购",
    "窗口早期高频购买": "中优先：谨慎召回",
    "近5日未购低频": "低到中优先：低成本触达",
    "中期购买": "常规运营"
})
validation.to_csv(OUT / "p1_segment_validation_summary.csv", index=False)
con.execute(f"COPY p1_segment_validation_detail TO '{sp(OUT / 'p1_segment_validation_detail.csv')}' (HEADER, DELIMITER ',')")

# P1-7: sensitivity table. Units are explicitly user-item pairs or users, not orders.
item = pd.read_csv(OUT / "p0_same_item_funnel_summary.csv").iloc[0]
segment_full = pd.read_csv(OUT / "p0_user_segment_summary_revised.csv")
full_counts = dict(zip(segment_full.user_segment, segment_full.users))
rows = []
def add_sensitivity(target, unit, eligible, baseline, confidence, cost):
    for lift in [0.01, 0.03, 0.05]:
        rows.append({
            "target": target,
            "unit": unit,
            "eligible_units": int(eligible),
            "baseline_rate": baseline,
            "assumed_lift_pp": lift * 100,
            "estimated_incremental_units": round(eligible * lift),
            "confidence": confidence,
            "execution_cost": cost,
            "note": "增量单位不是订单；需实验验证"
        })

add_sensitivity(
    "加购后未购买", "user-item", int(item.cart_after_pv_pairs - item.cart_to_buy_pairs),
    float(item.cart_to_buy_rate), "中", "中"
)
add_sensitivity(
    "高浏览未购买", "user", full_counts.get("高浏览未购买", 0),
    0.0, "低到中", "中"
)
add_sensitivity(
    "浏览→兴趣整体优化", "user-item", int(item.pv_pairs),
    float(item.pv_to_interest_rate), "低", "高"
)
add_sensitivity(
    "窗口早期高频购买", "user", full_counts.get("窗口早期高频购买", 0),
    None, "低到中", "中"
)
pd.DataFrame(rows).to_csv(OUT / "p1_operational_sensitivity.csv", index=False)

# P1-9: minimal window-first-purchase D1/D3 cohort. Do not call these new users.
retention = con.execute(f"""
WITH events AS (
    SELECT user_id, event_date, behavior_type
    FROM read_csv_auto('{sp(INPUT)}', header = true)
), day_flags AS (
    SELECT
        user_id,
        event_date,
        MAX(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END) AS has_buy
    FROM events
    GROUP BY user_id, event_date
), first_buy AS (
    SELECT user_id, MIN(event_date) AS cohort_date
    FROM events
    WHERE behavior_type = 'buy'
    GROUP BY user_id
), cohort_users AS (
    SELECT cohort_date, user_id
    FROM first_buy
), metrics AS (
    SELECT
        c.cohort_date,
        COUNT(*) AS cohort_users,
        COUNT(*) FILTER (WHERE e1.user_id IS NOT NULL) AS d1_active_users,
        COUNT(*) FILTER (WHERE e1.user_id IS NOT NULL AND e1.has_buy = 1) AS d1_buy_users,
        COUNT(*) FILTER (WHERE e3.user_id IS NOT NULL) AS d3_active_users,
        COUNT(*) FILTER (WHERE e3.user_id IS NOT NULL AND e3.has_buy = 1) AS d3_buy_users
    FROM cohort_users c
    LEFT JOIN day_flags e1 ON e1.user_id = c.user_id AND e1.event_date = CAST(c.cohort_date AS DATE) + INTERVAL 1 DAY
    LEFT JOIN day_flags e3 ON e3.user_id = c.user_id AND e3.event_date = CAST(c.cohort_date AS DATE) + INTERVAL 3 DAY
    GROUP BY c.cohort_date
)
SELECT
    cohort_date,
    cohort_users,
    d1_active_users,
    d1_buy_users,
    d3_active_users,
    d3_buy_users,
    cohort_date <= '2017-12-02' AS d1_complete,
    cohort_date <= '2017-11-30' AS d3_complete,
    CASE WHEN cohort_date <= '2017-12-02' THEN ROUND(d1_active_users::DOUBLE / NULLIF(cohort_users, 0), 6) END AS d1_active_rate,
    CASE WHEN cohort_date <= '2017-12-02' THEN ROUND(d1_buy_users::DOUBLE / NULLIF(cohort_users, 0), 6) END AS d1_buy_rate,
    CASE WHEN cohort_date <= '2017-11-30' THEN ROUND(d3_active_users::DOUBLE / NULLIF(cohort_users, 0), 6) END AS d3_active_rate,
    CASE WHEN cohort_date <= '2017-11-30' THEN ROUND(d3_buy_users::DOUBLE / NULLIF(cohort_users, 0), 6) END AS d3_buy_rate
FROM metrics
WHERE cohort_date <= '2017-12-02'
ORDER BY cohort_date
""").fetchdf()
retention.to_csv(OUT / "p1_window_first_purchase_cohort.csv", index=False)

# P1-10: data quality table.
quality = con.execute(f"""
WITH events AS (
    SELECT * FROM read_csv_auto('{sp(INPUT)}', header = true)
), counts AS (
    SELECT
        COUNT(*) AS rows,
        COUNT(DISTINCT user_id) AS users,
        COUNT(DISTINCT item_id) AS items,
        COUNT(DISTINCT category_id) AS categories,
        MIN(event_date) AS min_date,
        MAX(event_date) AS max_date,
        COUNT(*) FILTER (WHERE behavior_type = 'pv') AS pv_events,
        COUNT(*) FILTER (WHERE behavior_type = 'fav') AS fav_events,
        COUNT(*) FILTER (WHERE behavior_type = 'cart') AS cart_events,
        COUNT(*) FILTER (WHERE behavior_type = 'buy') AS buy_events
    FROM events
), user_dist AS (
    SELECT
        quantile_cont(n, 0.50) AS p50_events_per_user,
        quantile_cont(n, 0.90) AS p90_events_per_user,
        quantile_cont(n, 0.99) AS p99_events_per_user,
        MAX(n) AS max_events_per_user
    FROM (SELECT user_id, COUNT(*) n FROM events GROUP BY user_id)
), duplicates AS (
    SELECT COUNT(*) - COUNT(DISTINCT (user_id, item_id, category_id, behavior_type, timestamp)) AS exact_duplicate_rows
    FROM events
), item_quality AS (
    SELECT
        COUNT(*) FILTER (WHERE buy_time IS NOT NULL) AS buy_user_item_pairs,
        COUNT(*) FILTER (WHERE has_buy_after_pv) AS buy_after_pv_pairs,
        COUNT(*) FILTER (WHERE has_direct_buy_after_pv) AS buy_after_pv_without_interest,
        COUNT(*) FILTER (WHERE buy_time IS NOT NULL AND pv_time IS NULL) AS buy_without_pv,
        ROUND(
            COUNT(*) FILTER (WHERE buy_time IS NOT NULL AND pv_time IS NULL)::DOUBLE
            / NULLIF(COUNT(*) FILTER (WHERE buy_time IS NOT NULL), 0), 6
        ) AS buy_without_pv_rate
    FROM same_item_funnel_detail
)
SELECT * FROM counts CROSS JOIN user_dist CROSS JOIN duplicates CROSS JOIN item_quality
""").fetchdf()
quality.to_csv(OUT / "p1_data_quality_summary.csv", index=False)

print("P1 分层验证：")
print(validation.to_string(index=False))
print("\nP1 cohort：")
print(retention.to_string(index=False))
print("\nP1 数据质量：")
print(quality.to_string(index=False))
print("\nP1 输出已写入：", OUT)

con.close()
