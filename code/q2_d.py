"""Calculate Q2(d) pricing errors under the Q2(a)-Q2(c) SDFs.

The script reconstructs the 20 excess-return series from Q2(c), aligns
them with the three saved SDF series, and reports each portfolio's pricing
error under every SDF. Covariances use divisor T (ddof=0), as specified.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from q2_a import load_bm_value_weighted_monthly, load_ff3_rf
from q2_b import load_momentum_value_weighted_monthly
from q2_c import prepare_excess_returns


REPO_ROOT = Path(__file__).resolve().parents[1]
BM_FILE = REPO_ROOT / "data" / "Portfolios_Formed_on_BE-ME.csv"
MOM_FILE = REPO_ROOT / "data" / "10_Portfolios_Prior_12_2.csv"
FF3_FILE = REPO_ROOT / "data" / "ff3.csv"
Q2A_SDF_FILE = REPO_ROOT / "output" / "q2a_M_star.csv"
Q2B_SDF_FILE = REPO_ROOT / "output" / "q2b_M_star.csv"
Q2C_SDF_FILE = REPO_ROOT / "output" / "q2c_M_star.csv"
OUTPUT_DIR = REPO_ROOT / "output"
CSV_FILE = OUTPUT_DIR / "q2d_pricing_errors.csv"
LATEX_FILE = OUTPUT_DIR / "q2d_pricing_errors.tex"


def load_sdf(path: Path, column_name: str) -> pd.Series:
    """Load one saved SDF series and index it by monthly Period."""
    if not path.exists():
        raise FileNotFoundError(f"Missing SDF file: {path}")

    frame = pd.read_csv(path)
    required = {"month", "M_star"}
    if not required.issubset(frame.columns):
        raise ValueError(f"{path} must contain columns {sorted(required)}")
    frame["month"] = pd.PeriodIndex(frame["month"].astype(str), freq="M")
    if frame["month"].duplicated().any():
        raise ValueError(f"{path} contains duplicate months")

    values = pd.to_numeric(frame["M_star"], errors="raise")
    if not np.isfinite(values).all():
        raise ValueError(f"{path} contains non-finite SDF values")
    return pd.Series(
        values.to_numpy(dtype=float),
        index=frame["month"],
        name=column_name,
    ).sort_index()


def pricing_errors(excess_returns: np.ndarray, m_star: np.ndarray) -> np.ndarray:
    """Calculate mean(xR) - Cov(-M, xR) with covariance divisor T."""
    if excess_returns.ndim != 2:
        raise ValueError("excess_returns must be a two-dimensional array")
    if m_star.ndim != 1 or len(m_star) != len(excess_returns):
        raise ValueError("The SDF must be one-dimensional and match the sample length")

    mean_x_r = excess_returns.mean(axis=0)
    demeaned_x_r = excess_returns - mean_x_r
    negative_m = -m_star
    demeaned_negative_m = negative_m - negative_m.mean()
    covariance = (demeaned_negative_m[:, None] * demeaned_x_r).mean(axis=0)
    return mean_x_r - covariance


def save_latex_table(results: pd.DataFrame, path: Path) -> None:
    """Write a presentation-ready LaTeX table without optional dependencies."""
    def format_value(value: float) -> str:
        displayed_value = 0.0 if abs(value) < 0.5e-6 else value
        return f"{displayed_value:.6f}"

    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Pricing errors under the Q2(a), Q2(b), and Q2(c) SDFs.}",
        r"\label{tab:q2d-pricing-errors}",
        r"\begin{tabular}{lrrr}",
        r"\hline",
        (
            r"Portfolio & $\widehat{\alpha}^{Q2(a)}$ & "
            r"$\widehat{\alpha}^{Q2(b)}$ & $\widehat{\alpha}^{Q2(c)}$ \\"
        ),
        r"\hline",
    ]
    for row in results.itertuples(index=False):
        lines.append(
            f"{row.portfolio} & {format_value(row.alpha_hat_Q2a)} & "
            f"{format_value(row.alpha_hat_Q2b)} & "
            f"{format_value(row.alpha_hat_Q2c)} \\\\"
        )
    lines.extend(
        [
            r"\hline",
            r"\end{tabular}",
            r"\end{table}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    bm = load_bm_value_weighted_monthly(BM_FILE)
    momentum = load_momentum_value_weighted_monthly(MOM_FILE)
    rf = load_ff3_rf(FF3_FILE)
    combined_returns = bm.add_prefix("BM: ").join(
        momentum.add_prefix("MOM: "), how="inner"
    )
    excess_returns = prepare_excess_returns(
        combined_returns, rf, "Q2(d) combined BM-momentum"
    )
    if excess_returns.shape[1] != 20:
        raise ValueError(f"Expected 20 portfolios; found {excess_returns.shape[1]}")

    sdf_frame = pd.concat(
        [
            load_sdf(Q2A_SDF_FILE, "M_star_Q2a"),
            load_sdf(Q2B_SDF_FILE, "M_star_Q2b"),
            load_sdf(Q2C_SDF_FILE, "M_star_Q2c"),
        ],
        axis=1,
        join="inner",
    )
    if not sdf_frame.index.equals(excess_returns.index):
        missing_sdf = excess_returns.index.difference(sdf_frame.index)
        extra_sdf = sdf_frame.index.difference(excess_returns.index)
        raise ValueError(
            "The merged SDF sample does not exactly match the Q2(c) return sample. "
            f"Missing SDF months: {missing_sdf.tolist()}; "
            f"extra SDF months: {extra_sdf.tolist()}"
        )
    if sdf_frame.isna().any().any():
        raise ValueError("At least one SDF has a missing value in the common sample")

    x_r = excess_returns.to_numpy(dtype=float)
    alpha_columns: dict[str, np.ndarray] = {}
    pricing_moment_differences: dict[str, float] = {}
    for sdf_column, alpha_column in [
        ("M_star_Q2a", "alpha_hat_Q2a"),
        ("M_star_Q2b", "alpha_hat_Q2b"),
        ("M_star_Q2c", "alpha_hat_Q2c"),
    ]:
        m_star = sdf_frame[sdf_column].to_numpy(dtype=float)
        if not np.isclose(m_star.mean(), 1.0, atol=1e-10):
            raise ArithmeticError(
                f"{sdf_column} normalization failed: mean = {m_star.mean():.16g}"
            )
        alpha = pricing_errors(x_r, m_star)
        alpha_columns[alpha_column] = alpha

        # With E[M]=1, the specified alpha formula equals mean(M*xR).
        pricing_moment = (m_star[:, None] * x_r).mean(axis=0)
        pricing_moment_differences[sdf_column] = float(
            np.max(np.abs(alpha - pricing_moment))
        )

    results = pd.DataFrame(
        {
            "portfolio": excess_returns.columns,
            **alpha_columns,
        }
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results.to_csv(CSV_FILE, index=False, float_format="%.12g")
    save_latex_table(results, LATEX_FILE)

    print(
        f"Sample: {excess_returns.index.min()} to {excess_returns.index.max()} "
        f"({len(excess_returns)} months)"
    )
    print("Pricing errors are monthly decimal returns; covariance uses ddof=0.")
    print(results.to_string(index=False, float_format=lambda value: f"{value:.12g}"))
    print("Maximum absolute pricing error by SDF:")
    for column in ["alpha_hat_Q2a", "alpha_hat_Q2b", "alpha_hat_Q2c"]:
        print(f"  {column}: {np.max(np.abs(results[column])):.12g}")
    print("Maximum difference from direct mean(M_star * xR) check:")
    for sdf_column, difference in pricing_moment_differences.items():
        print(f"  {sdf_column}: {difference:.3e}")
    print(f"Wrote {CSV_FILE.relative_to(REPO_ROOT)}")
    print(f"Wrote {LATEX_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
