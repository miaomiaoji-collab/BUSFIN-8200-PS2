"""Construct the Q2(b) ex-post SDF for momentum portfolios.

The script reads the monthly value-weighted momentum decile returns and
the Fama-French risk-free rate, uses the common sample beginning in July
1927, and writes the requested SDF series and coefficient plot. It also
reports the Pearson correlation with the Q2(a) SDF over common months.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from q2_a import (
    MISSING_SENTINELS,
    START_MONTH,
    compute_sdf,
    load_ff3_rf,
    require_complete_monthly_sample,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MOM_FILE = REPO_ROOT / "data" / "10_Portfolios_Prior_12_2.csv"
FF3_FILE = REPO_ROOT / "data" / "ff3.csv"
Q2A_SDF_FILE = REPO_ROOT / "output" / "q2a_M_star.csv"
OUTPUT_DIR = REPO_ROOT / "output"
PLOT_FILE = OUTPUT_DIR / "q2b_b_coefficients.pdf"
SDF_FILE = OUTPUT_DIR / "q2b_M_star.csv"

PORTFOLIOS = [
    "Lo PRIOR",
    "PRIOR 2",
    "PRIOR 3",
    "PRIOR 4",
    "PRIOR 5",
    "PRIOR 6",
    "PRIOR 7",
    "PRIOR 8",
    "PRIOR 9",
    "Hi PRIOR",
]


def _parse_yyyymm(value: str) -> pd.Period:
    """Convert a six-digit YYYYMM string to a monthly Period."""
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError(f"Invalid YYYYMM value: {value!r}")
    return pd.Period(f"{value[:4]}-{value[4:]}", freq="M")


def load_momentum_value_weighted_monthly(path: Path) -> pd.DataFrame:
    """Read the ten deciles from the monthly value-weighted momentum section."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    marker = "Value Weight Returns -- Monthly"
    try:
        marker_index = next(i for i, line in enumerate(lines) if marker in line)
    except StopIteration as exc:
        raise ValueError(f"Could not find {marker!r} in {path}") from exc

    reader = csv.reader(lines[marker_index + 1 :])
    try:
        raw_header = next(reader)
    except StopIteration as exc:
        raise ValueError(f"Missing monthly momentum header in {path}") from exc

    header = [value.strip() for value in raw_header]
    missing_columns = [name for name in PORTFOLIOS if name not in header]
    if missing_columns:
        raise ValueError(f"Missing momentum portfolio columns: {missing_columns}")

    column_positions = {name: header.index(name) for name in PORTFOLIOS}
    records: list[dict[str, object]] = []
    for row in reader:
        if not row or not re.fullmatch(r"\d{6}", row[0].strip()):
            break
        record: dict[str, object] = {"month": _parse_yyyymm(row[0].strip())}
        for name, position in column_positions.items():
            raw_value = float(row[position].strip())
            record[name] = np.nan if raw_value in MISSING_SENTINELS else raw_value
        records.append(record)

    if not records:
        raise ValueError(f"No monthly value-weighted momentum observations found in {path}")

    frame = pd.DataFrame.from_records(records).set_index("month")
    if frame.index.has_duplicates:
        raise ValueError("The momentum monthly data contain duplicate months")
    return frame.sort_index()


def load_q2a_sdf(path: Path) -> pd.Series:
    """Read the saved Q2(a) SDF and index it by month."""
    if not path.exists():
        raise FileNotFoundError(f"Run Q2(a) first; missing {path}")
    frame = pd.read_csv(path)
    required = {"month", "M_star"}
    if not required.issubset(frame.columns):
        raise ValueError(f"{path} must contain columns {sorted(required)}")
    frame["month"] = pd.PeriodIndex(frame["month"].astype(str), freq="M")
    if frame["month"].duplicated().any():
        raise ValueError("The saved Q2(a) SDF contains duplicate months")
    return frame.set_index("month")["M_star"].sort_index().rename("M_star_q2a")


def save_coefficient_plot(b_hat: np.ndarray, path: Path) -> None:
    """Save the requested scatterplot as a vector PDF."""
    deciles = np.arange(1, len(PORTFOLIOS) + 1)
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.scatter(deciles, b_hat, s=54, color="#8f3b2f", edgecolor="white", linewidth=0.7)
    ax.axhline(0.0, color="#555555", linewidth=0.8, linestyle="--")
    ax.set_xticks(deciles)
    ax.set_xlabel("Momentum portfolio decile")
    ax.set_ylabel(r"Coefficient $\hat{b}_j$")
    ax.set_title("Maximum-Sharpe Momentum Portfolio Coefficients")
    ax.grid(axis="y", color="#dddddd", linewidth=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, format="pdf", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    momentum = load_momentum_value_weighted_monthly(MOM_FILE)
    rf = load_ff3_rf(FF3_FILE)
    merged = momentum.join(rf, how="inner").loc[START_MONTH:]
    if merged.empty:
        raise ValueError("The common sample beginning in July 1927 is empty")
    require_complete_monthly_sample(merged)

    # Source files report percentage returns; convert to decimals before subtraction.
    returns = merged[PORTFOLIOS] / 100.0
    risk_free = merged["RF"] / 100.0
    excess_returns = returns.sub(risk_free, axis=0)

    b_hat, a_hat, m_star, pricing_errors = compute_sdf(excess_returns)
    mean_m = float(m_star.mean())
    max_abs_error = float(np.max(np.abs(pricing_errors)))
    if not np.isclose(mean_m, 1.0, atol=1e-12):
        raise ArithmeticError(f"SDF normalization failed: mean(M*) = {mean_m:.16g}")
    if max_abs_error > 1e-12:
        raise ArithmeticError(
            f"In-sample pricing check failed: max absolute error = {max_abs_error:.3e}"
        )

    q2b_sdf = pd.Series(m_star, index=merged.index, name="M_star_q2b")
    q2a_sdf = load_q2a_sdf(Q2A_SDF_FILE)
    correlation_sample = pd.concat([q2a_sdf, q2b_sdf], axis=1, join="inner").dropna()
    if correlation_sample.empty:
        raise ValueError("Q2(a) and Q2(b) SDF series have no common months")
    sdf_correlation = float(correlation_sample.corr().iloc[0, 1])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "month": merged.index.astype(str),
            "M_star": m_star,
        }
    ).to_csv(SDF_FILE, index=False, float_format="%.12f")
    save_coefficient_plot(b_hat, PLOT_FILE)

    diagnostics = pd.DataFrame(
        {
            "portfolio": PORTFOLIOS,
            "b_hat": b_hat,
            "mean_M_times_xR": pricing_errors,
        }
    )
    print(f"Sample: {merged.index.min()} to {merged.index.max()} ({len(merged)} months)")
    print(f"a_hat: {a_hat:.12f}")
    print(f"mean(M_star): {mean_m:.12f}")
    print(f"max abs mean(M_star * xR_j): {max_abs_error:.3e}")
    print(
        "Correlation with Q2(a) M_star "
        f"({correlation_sample.index.min()} to {correlation_sample.index.max()}, "
        f"{len(correlation_sample)} months): {sdf_correlation:.12f}"
    )
    print(diagnostics.to_string(index=False, float_format=lambda value: f"{value:.12g}"))
    print(f"Wrote {SDF_FILE.relative_to(REPO_ROOT)}")
    print(f"Wrote {PLOT_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
