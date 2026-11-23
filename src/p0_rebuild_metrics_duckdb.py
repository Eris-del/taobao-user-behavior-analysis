from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "processed" / "UserBehavior_clean.csv"
DB = ROOT / "data" / "processed" / "taobao_analysis.duckdb"
OUT = ROOT / "data" / "processed"

def sp(path: Path) -> str:
    return str(path).replace("'", "''")

con = duckdb.connect(str(DB))

# Rebuild the user-item first-event table.
con.execute(f"""
CREATE OR REPLACE TABLE user_item_first AS
SELECT
    user_id,
    item_id,
    MIN(category_id) AS category_id,
    MIN(timestamp) FILTER (WHERE behavior_type = 'pv') AS pv_time,
    MIN(timestamp) FILTER (WHERE behavior_type = 'fav') AS fav_time,
    MIN(timestamp) FILTER (WHERE behavior_type = 'cart') AS cart_time,
    MIN(timestamp) FILTER (WHERE behavior_type = 'buy') AS buy_time
FROM read_csv_auto('{sp(INPUT)}', header = true)
GROUP BY user_id, item_id
""")

# Correctly require fav/cart to occur after the first PV before calling it an interest step.
con.execute("""
CREATE OR REPLACE TABLE same_item_funnel_detail AS
WITH staged AS (
    SELECT
        *,
        CASE WHEN pv_time IS NOT NULL AND fav_time > pv_time THEN fav_time END AS fav_after_pv_time,
        CASE WHEN pv_time IS NOT NULL AND cart_time > pv_time THEN cart_time END AS cart_after_pv_time
    FROM user_item_first
),
interest AS (
    SELECT
        *,
        CASE
            WHEN fav_after_pv_time IS NULL THEN cart_after_pv_time
            WHEN cart_after_pv_time IS NULL THEN fav_after_pv_time
            WHEN fav_after_pv_time <= cart_after_pv_time THEN fav_after_pv_time
            ELSE cart_after_pv_time
        END AS interest_after_pv_time
    FROM staged
)
SELECT
    *,
    pv_time IS NOT NULL AS has_pv,
    fav_after_pv_time IS NOT NULL AS has_fav_after_pv,
    cart_after_pv_time IS NOT NULL AS has_cart_after_pv,
    interest_after_pv_time IS NOT NULL AS has_interest_after_pv,
    buy_time IS NOT NULL AND pv_time IS NOT NULL AND buy_time > pv_time AS has_buy_after_pv,
    buy_time IS NOT NULL AND fav_after_pv_time IS NOT NULL AND buy_time > fav_after_pv_time AS has_fav_to_buy,
    buy_time IS NOT NULL AND cart_after_pv_time IS NOT NULL AND buy_time > cart_after_pv_time AS has_cart_to_buy,
    buy_time IS NOT NULL AND interest_after_pv_time IS NOT NULL AND buy_time > interest_after_pv_time AS has_buy_after_interest,
    buy_time IS NOT NULL AND pv_time IS NOT NULL AND buy_time > pv_time
        AND NOT (
            (fav_after_pv_time IS NOT NULL AND fav_after_pv_time < buy_time)
            OR (cart_after_pv_time IS NOT NULL AND cart_after_pv_time < buy_time)
        ) AS has_direct_buy_after_pv
FROM interest
""")

summary = con.execute("""
SELECT
    COUNT(*) FILTER (WHERE has_pv) AS pv_pairs,
    COUNT(*) FILTER (WHERE has_interest_after_pv) AS interest_pairs,
    COUNT(*) FILTER (WHERE has_fav_after_pv) AS fav_after_pv_pairs,
    COUNT(*) FILTER (WHERE has_cart_after_pv) AS cart_after_pv_pairs,
    COUNT(*) FILTER (WHERE has_buy_after_pv) AS buy_after_pv_pairs,
    COUNT(*) FILTER (WHERE has_buy_after_interest) AS buy_after_interest_pairs,
    COUNT(*) FILTER (WHERE has_fav_to_buy) AS fav_to_buy_pairs,
    COUNT(*) FILTER (WHERE has_cart_to_buy) AS cart_to_buy_pairs,
    COUNT(*) FILTER (WHERE has_direct_buy_after_pv) AS direct_buy_pairs,
    ROUND(COUNT(*) FILTER (WHERE has_interest_after_pv)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_pv), 0), 6) AS pv_to_interest_rate,
    ROUND(COUNT(*) FILTER (WHERE has_fav_after_pv)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_pv), 0), 6) AS pv_to_fav_rate,
    ROUND(COUNT(*) FILTER (WHERE has_cart_after_pv)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_pv), 0), 6) AS pv_to_cart_rate,
    ROUND(COUNT(*) FILTER (WHERE has_buy_after_interest)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_interest_after_pv), 0), 6) AS interest_to_buy_rate,
    ROUND(COUNT(*) FILTER (WHERE has_fav_to_buy)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_fav_after_pv), 0), 6) AS fav_to_buy_rate,
    ROUND(COUNT(*) FILTER (WHERE has_cart_to_buy)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_cart_after_pv), 0), 6) AS cart_to_buy_rate,
    ROUND(COUNT(*) FILTER (WHERE has_buy_after_pv)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_pv), 0), 6) AS pv_to_buy_rate_including_direct,
    ROUND(COUNT(*) FILTER (WHERE has_direct_buy_after_pv)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_buy_after_pv), 0), 6) AS direct_buy_share
FROM same_item_funnel_detail
""").fetchdf()
summary.to_csv(OUT / "p0_same_item_funnel_summary.csv", index=False)

# User-level whole-window behavior coverage, explicitly not called conversion.
# The sequence is retained for comparison with the original user-level result.
con.execute(f"""
CREATE OR REPLACE TABLE user_first_p0 AS
SELECT
    user_id,
    MIN(timestamp) FILTER (WHERE behavior_type = 'pv') AS pv_time,
    MIN(timestamp) FILTER (WHERE behavior_type = 'fav') AS fav_time,
    MIN(timestamp) FILTER (WHERE behavior_type = 'cart') AS cart_time,
    MIN(timestamp) FILTER (WHERE behavior_type = 'buy') AS buy_time
FROM read_csv_auto('{sp(INPUT)}', header = true)
GROUP BY user_id
""")

coverage = con.execute("""
WITH s AS (
    SELECT *,
        CASE WHEN pv_time IS NOT NULL AND fav_time > pv_time THEN fav_time END AS fav_after_pv,
        CASE WHEN pv_time IS NOT NULL AND cart_time > pv_time THEN cart_time END AS cart_after_pv
    FROM user_first_p0
), i AS (
    SELECT *,
        CASE
            WHEN fav_after_pv IS NULL THEN cart_after_pv
            WHEN cart_after_pv IS NULL THEN fav_after_pv
            WHEN fav_after_pv <= cart_after_pv THEN fav_after_pv
            ELSE cart_after_pv
        END AS interest_after_pv
    FROM s
), f AS (
    SELECT *,
        pv_time IS NOT NULL AS has_pv,
        interest_after_pv IS NOT NULL AS has_interest_after_pv,
        buy_time IS NOT NULL AND interest_after_pv IS NOT NULL AND buy_time > interest_after_pv AS has_buy_after_interest
    FROM i
)
SELECT
    COUNT(*) FILTER (WHERE has_pv) AS pv_users,
    COUNT(*) FILTER (WHERE has_interest_after_pv) AS interest_after_pv_users,
    COUNT(*) FILTER (WHERE has_buy_after_interest) AS buy_after_interest_users,
    ROUND(COUNT(*) FILTER (WHERE has_interest_after_pv)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_pv), 0), 6) AS pv_to_interest_coverage,
    ROUND(COUNT(*) FILTER (WHERE has_buy_after_interest)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE has_pv), 0), 6) AS pv_to_buy_coverage
FROM f
""").fetchdf()
coverage.to_csv(OUT / "p0_user_behavior_coverage.csv", index=False)

# Fixed-horizon cohort outputs. The conservative cutoffs avoid incomplete follow-up.
def horizon_outputs(hours: int, cutoff: str):
    suffix = f"{hours}h"
    eligible = f"pv_date <= DATE '{cutoff}'"
    base = f"""
    WITH b AS (
        SELECT *,
            CAST(timezone('Asia/Shanghai', to_timestamp(pv_time)) AS DATE) AS pv_date,
            EXTRACT(HOUR FROM timezone('Asia/Shanghai', to_timestamp(pv_time)))::INTEGER AS pv_hour,
            pv_time + {hours} * 3600 AS horizon_end
        FROM same_item_funnel_detail
        WHERE has_pv AND {eligible}
    ), h AS (
        SELECT *,
            fav_time IS NOT NULL AND fav_time > pv_time AND fav_time <= horizon_end AS fav_h,
            cart_time IS NOT NULL AND cart_time > pv_time AND cart_time <= horizon_end AS cart_h,
            buy_time IS NOT NULL AND buy_time > pv_time AND buy_time <= horizon_end AS buy_h,
            fav_time IS NOT NULL AND fav_time > pv_time AND fav_time <= horizon_end
                AND (buy_time IS NULL OR fav_time < buy_time) AS fav_before_buy_h,
            cart_time IS NOT NULL AND cart_time > pv_time AND cart_time <= horizon_end
                AND (buy_time IS NULL OR cart_time < buy_time) AS cart_before_buy_h,
            CASE
                WHEN fav_time IS NOT NULL AND fav_time > pv_time AND fav_time <= horizon_end
                    AND (cart_time IS NULL OR cart_time <= pv_time OR cart_time > horizon_end OR fav_time <= cart_time) THEN fav_time
                WHEN cart_time IS NOT NULL AND cart_time > pv_time AND cart_time <= horizon_end THEN cart_time
            END AS interest_h_time
        FROM b
    ), f AS (
        SELECT *,
            interest_h_time IS NOT NULL AS interest_h,
            buy_time IS NOT NULL AND interest_h_time IS NOT NULL AND buy_time > interest_h_time AND buy_time <= horizon_end AS buy_after_interest_h,
            buy_time IS NOT NULL AND buy_time > pv_time AND buy_time <= horizon_end
                AND NOT (fav_before_buy_h OR cart_before_buy_h) AS direct_buy_h
        FROM h
    )
    """
    select_template = """
    SELECT {group_col},
        COUNT(*) AS pv_pairs,
        COUNT(*) FILTER (WHERE interest_h) AS interest_pairs,
        COUNT(*) FILTER (WHERE fav_h) AS fav_pairs,
        COUNT(*) FILTER (WHERE cart_h) AS cart_pairs,
        COUNT(*) FILTER (WHERE buy_after_interest_h) AS buy_pairs,
        COUNT(*) FILTER (WHERE direct_buy_h) AS direct_buy_pairs,
        ROUND(COUNT(*) FILTER (WHERE interest_h)::DOUBLE / NULLIF(COUNT(*), 0), 6) AS pv_to_interest_rate,
        ROUND(COUNT(*) FILTER (WHERE fav_h)::DOUBLE / NULLIF(COUNT(*), 0), 6) AS pv_to_fav_rate,
        ROUND(COUNT(*) FILTER (WHERE cart_h)::DOUBLE / NULLIF(COUNT(*), 0), 6) AS pv_to_cart_rate,
        ROUND(COUNT(*) FILTER (WHERE buy_after_interest_h)::DOUBLE / NULLIF(COUNT(*) FILTER (WHERE interest_h), 0), 6) AS interest_to_buy_rate,
        ROUND(COUNT(*) FILTER (WHERE buy_after_interest_h)::DOUBLE / NULLIF(COUNT(*), 0), 6) AS pv_to_buy_rate
    FROM f
    GROUP BY {group_col}
    ORDER BY {group_col}
    """
    for name, group_col in [("date", "pv_date"), ("hour", "pv_hour"), ("category", "category_id")]:
        q = base + select_template.format(group_col=group_col)
        con.execute(f"COPY ({q}) TO '{sp(OUT / f'p0_{suffix}_by_{name}.csv')}' (HEADER, DELIMITER ',')")

horizon_outputs(24, "2017-12-02")
horizon_outputs(48, "2017-12-01")

print("P0 核心商品级指标：")
print(summary.to_string(index=False))
print("\nP0 用户级行为覆盖率（不是转化率）：")
print(coverage.to_string(index=False))
print("\n已生成 p0_* 结果文件至：", OUT)

con.close()
