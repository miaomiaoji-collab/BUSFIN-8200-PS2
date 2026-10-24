"""Construct the Q2(a) ex-post SDF for book-to-market portfolios.

The script reads the monthly value-weighted book-to-market decile returns
and the Fama-French risk-free rate, uses the common sample beginning in
July 1927, and writes the requested SDF series and coefficient plot.
"""

from __future__ import annotations

import csv
import re
from io import StringIO
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
BM_FILE = REPO_ROOT / "data" / "Portfolios_Formed_on_BE-ME.csv"
FF3_FILE = REPO_ROOT / "data" / "ff3.csv"
OUTPUT_DIR = REPO_ROOT / "output"
PLOT_FILE = OUTPUT_DIR / "q2a_b_coefficients.pdf"
SDF_FILE = OUTPUT_DIR / "q2a_M_star.csv"

PORTFOLIOS = [
    "Lo 10",
    "2-Dec",
    "3-Dec",
    "4-Dec",
    "5-Dec",
    "6-Dec",
    "7-Dec",
    "8-Dec",
    "9-Dec",
    "Hi 10",
]
START_MONTH = pd.Period("1927-07", freq="M")
MISSING_SENTINELS = {-99.99, -999.0}


def _parse_yyyymm(value: str) -> pd.Period:
    """Convert a six-digit YYYYMM string to a monthly Period."""
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError(f"Invalid YYYYMM value: {value!r}")
    return pd.Period(f"{value[:4]}-{value[4:]}", freq="M")


def load_bm_value_weighted_monthly(path: Path) -> pd.DataFrame:
    """Read the ten deciles from the monthly value-weighted BM section."""
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
        raise ValueError(f"Missing monthly BM header in {path}") from exc

    header = [value.strip() for value in raw_header]
    missing_columns = [name for name in PORTFOLIOS if name not in header]
    if missing_columns:
        raise ValueError(f"Missing BM portfolio columns: {missing_columns}")

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
        raise ValueError(f"No monthly value-weighted BM observations found in {path}")

    frame = pd.DataFrame.from_records(records).set_index("month")
    if frame.index.has_duplicates:
        raise ValueError("The BM monthly data contain duplicate months")
    return frame.sort_index()


def load_ff3_rf(path: Path) -> pd.DataFrame:
    """Read the monthly risk-free rate from the Fama-French factors file."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    try:
        header_index = next(i for i, line in enumerate(lines) if "RF" in line)
    except StopIteration as exc:
        raise ValueError(f"Could not find an RF column in {path}") from exc

    rows = [lines[header_index]]
    for line in lines[header_index + 1 :]:
        first_field = line.split(",", maxsplit=1)[0].strip()
        if not re.fullmatch(r"\d{6}", first_field):
            break
        rows.append(line)

    frame = pd.read_csv(StringIO("\n".join(rows)), skipinitialspace=True)
    date_column = frame.columns[0]
    frame = frame.rename(columns={date_column: "yyyymm"})
    frame["month"] = frame["yyyymm"].astype(str).str.strip().map(_parse_yyyymm)
    frame["RF"] = pd.to_numeric(frame["RF"], errors="raise")
    frame = frame.set_index("month")[["RF"]].sort_index()
    if frame.index.has_duplicates:
        raise ValueError("The FF3 monthly data contain duplicate months")
    return frame


def require_complete_monthly_sample(frame: pd.DataFrame) -> None:
    """Fail on missing values or gaps instead of choosing a treatment silently."""
    if frame.isna().any().any():
        locations = np.argwhere(frame.isna().to_numpy())
        preview = [
            (str(frame.index[row]), frame.columns[col]) for row, col in locations[:10]
        ]
        raise ValueError(f"Missing observations in the analysis sample: {preview}")

    expected = pd.period_range(frame.index.min(), frame.index.max(), freq="M")
    missing_months = expected.difference(frame.index)
    if len(missing_months):
        raise ValueError(f"Months missing from the common sample: {missing_months.tolist()}")


def compute_sdf(excess_returns: pd.DataFrame) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    """Return b, a, M, and sample pricing errors using covariance divisor T."""
    x_r = excess_returns.to_numpy(dtype=float)
    mu_hat = x_r.mean(axis=0)
    demeaned = x_r - mu_hat
    sigma_hat = demeaned.T @ demeaned / x_r.shape[0]

    b_hat = np.linalg.solve(sigma_hat, mu_hat)
    x_r_star = x_r @ b_hat
    a_hat = 1.0 + b_hat @ mu_hat
    m_star = a_hat - x_r_star
    pricing_errors = (m_star[:, None] * x_r).mean(axis=0)
    return b_hat, float(a_hat), m_star, pricing_errors


def save_coefficient_plot(b_hat: np.ndarray, path: Path) -> None:
    """Save the requested scatterplot as a vector PDF."""
    deciles = np.arange(1, len(PORTFOLIOS) + 1)
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.scatter(deciles, b_hat, s=54, color="#1f5a94", edgecolor="white", linewidth=0.7)
    ax.axhline(0.0, color="#555555", linewidth=0.8, linestyle="--")
    ax.set_xticks(deciles)
    ax.set_xlabel("Book-to-market portfolio decile")
    ax.set_ylabel(r"Coefficient $\hat{b}_j$")
    ax.set_title("Maximum-Sharpe Portfolio Coefficients")
    ax.grid(axis="y", color="#dddddd", linewidth=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, format="pdf", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    bm = load_bm_value_weighted_monthly(BM_FILE)
    rf = load_ff3_rf(FF3_FILE)
    merged = bm.join(rf, how="inner").loc[START_MONTH:]
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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sdf_output = pd.DataFrame(
        {
            "month": merged.index.astype(str),
            "M_star": m_star,
        }
    )
    sdf_output.to_csv(SDF_FILE, index=False, float_format="%.12f")
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
    print(diagnostics.to_string(index=False, float_format=lambda value: f"{value:.12g}"))
    print(f"Wrote {SDF_FILE.relative_to(REPO_ROOT)}")
    print(f"Wrote {PLOT_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
