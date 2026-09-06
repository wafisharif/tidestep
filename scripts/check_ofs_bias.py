"""Compare NYOFS guidance to observed water levels over the last 48 h.

Run before trusting a forecast: a steady offset between OFS and observations
means a datum mismatch and would shift every flood depth by that amount.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd  # noqa: E402
from tidestep import coops  # noqa: E402

start = datetime.now(timezone.utc) - timedelta(hours=48)
obs = coops.fetch_observed(start, 48)
ofs = coops.fetch_ofs_forecast(start, 48)
pred = coops.fetch_predictions(start, 48)
df = pd.concat({"obs": obs, "ofs": ofs, "pred": pred}, axis=1).dropna()
df["ofs-obs"] = df.ofs - df.obs
df["pred-obs"] = df.pred - df.obs
print(df.round(3).to_string())
print("\nmean ofs-obs: %.3f m   mean pred-obs: %.3f m" % (df["ofs-obs"].mean(), df["pred-obs"].mean()))
