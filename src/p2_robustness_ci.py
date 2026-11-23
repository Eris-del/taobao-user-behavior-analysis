from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "processed"
FIG = ROOT / "figures"
FIG.mkdir(exist_ok=True)
rng = np.random.default_rng(20261121)

def add_binomial_bootstrap_ci(df, success_col="buy_pairs", total_col="pv_pairs", reps=2000):
    df = df.copy()
    lows, highs = [], []
    for row in df.itertuples():
        n = int(getattr(row, total_col))
        s = int(getattr(row, success_col))
        if n <= 0:
            lows.append(np.nan)
            highs.append(np.nan)
            continue
        p = s / n
        draws = rng.binomial(n, p, size=reps) / n
        lows.append(float(np.quantile(draws, 0.025)))
        highs.append(float(np.quantile(draws, 0.975)))
    df["pv_to_buy_bootstrap_low"] = lows
    df["pv_to_buy_bootstrap_high"] = highs
    return df

for horizon in [24, 48]:
    for dimension in ["hour", "category"]:
        source = DATA / f"p0_{horizon}h_by_{dimension}.csv"
        output = DATA / f"p2_{horizon}h_by_{dimension}_ci.csv"
        df = pd.read_csv(source)
        df = add_binomial_bootstrap_ci(df)
        df.to_csv(output, index=False)

        # For categories, save a readable top-volume chart with error bars.
        if dimension == "category":
            top = df.nlargest(10, "pv_pairs").sort_values("pv_to_buy_rate")
            import matplotlib.pyplot as plt
            plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
            plt.rcParams["axes.unicode_minus"] = False
            fig, ax = plt.subplots(figsize=(10, 6))
            x = top["pv_to_buy_rate"] * 100
            low = (top["pv_to_buy_rate"] - top["pv_to_buy_bootstrap_low"]) * 100
            high = (top["pv_to_buy_bootstrap_high"] - top["pv_to_buy_rate"]) * 100
            ax.errorbar(x, top["category_id"].astype(str), xerr=[low, high], fmt="o", color="#4C78A8", capsize=3)
            ax.set_title(f"固定 {horizon} 小时窗口：浏览量前 10 品类购买率及 95% 区间")
            ax.set_xlabel("浏览→购买转化率（%）")
            ax.set_ylabel("品类 ID")
            ax.grid(axis="x", alpha=0.25)
            fig.tight_layout()
            fig.savefig(FIG / f"p2_{horizon}h_category_ci.png", dpi=180)
            plt.close(fig)

print("P2 bootstrap CI 结果已生成：")
for horizon in [24, 48]:
    for dimension in ["hour", "category"]:
        print(DATA / f"p2_{horizon}h_by_{dimension}_ci.csv")
