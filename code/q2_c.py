"""Construct the Q2(c) ex-post SDF for BM and momentum portfolios.

The script combines the ten book-to-market and ten momentum decile
portfolio excess returns over their common monthly sample, constructs the
normalized maximum-Sharpe SDF, and writes the requested SDF series and
coefficient plot. It also reports the monthly Sharpe ratios of the
maximum-Sharpe portfolios from Q2(a), Q2(b), and Q2(c).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from q2_a import (
    PORTFOLIOS as BM_PORTFOLIOS,
    START_MONTH,
    compute_sdf,
    load_bm_value_weighted_monthly,
    load_ff3_rf,
    require_complete_monthly_sample,
)
from q2_b import (
    PORTFOLIOS as MOM_PORTFOLIOS,
    load_momentum_value_weighted_monthly,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
BM_FILE = REPO_ROOT / "data" / "Portfolios_Formed_on_BE-ME.csv"
MOM_FILE = REPO_ROOT / "data" / "10_Portfolios_Prior_12_2.csv"
FF3_FILE = REPO_ROOT / "data" / "ff3.csv"
OUTPUT_DIR = REPO_ROOT / "output"
PLOT_FILE = OUTPUT_DIR / "q2c_b_coefficients.pdf"
SDF_FILE = OUTPUT_DIR / "q2c_M_star.csv"


def prepare_excess_returns(
    returns: pd.DataFrame, rf: pd.DataFrame, label: str
) -> pd.DataFrame:
    """Merge percentage returns with RF and return a complete decimal sample."""
    merged = returns.join(rf, how="inner").loc[START_MONTH:]
    if merged.empty:
        raise ValueError(f"The {label} sample beginning in July 1927 is empty")
    require_complete_monthly_sample(merged)

    decimal_returns = merged[returns.columns] / 100.0
    risk_free = merged["RF"] / 100.0
    return decimal_returns.sub(risk_free, axis=0)


def maximum_sharpe_results(
    excess_returns: pd.DataFrame,
) -> tuple[np.ndarray, float, np.ndarray, np.ndarray, np.ndarray]:
    """Return b, a, M, pricing errors, and maximum-Sharpe excess returns."""
    b_hat, a_hat, m_star, pricing_errors = compute_sdf(excess_returns)
    x_r_star = excess_returns.to_numpy(dtype=float) @ b_hat
    return b_hat, a_hat, m_star, pricing_errors, x_r_star


def sharpe_ratio(x_r_star: np.ndarray) -> float:
    """Calculate the monthly Sharpe ratio using divisor T (ddof=0)."""
    standard_deviation = float(np.std(x_r_star, ddof=0))
    if not np.isfinite(standard_deviation) or standard_deviation <= 0.0:
        raise ArithmeticError(
            f"Maximum-Sharpe portfolio has invalid volatility: {standard_deviation}"
        )
    return float(np.mean(x_r_star) / standard_deviation)


def save_coefficient_plot(
    bm_b_hat: np.ndarray, mom_b_hat: np.ndarray, path: Path
) -> None:
    """Save the combined coefficient scatterplot as a vector PDF."""
    deciles = np.arange(1, 11)
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.scatter(
        deciles,
        bm_b_hat,
        s=58,
        color="#1f77b4",
        edgecolor="white",
        linewidth=0.7,
        label="Book-to-market",
    )
    ax.scatter(
        deciles,
        mom_b_hat,
        s=58,
        color="#d62728",
        edgecolor="white",
        linewidth=0.7,
        label="Momentum",
    )
    ax.axhline(0.0, color="#555555", linewidth=0.8, linestyle="--")
    ax.set_xticks(deciles)
    ax.set_xlabel("Portfolio decile")
    ax.set_ylabel(r"Coefficient $\hat{b}_j$")
    ax.set_title("Maximum-Sharpe BM and Momentum Portfolio Coefficients")
    ax.grid(axis="y", color="#dddddd", linewidth=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, format="pdf", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    bm = load_bm_value_weighted_monthly(BM_FILE)
    momentum = load_momentum_value_weighted_monthly(MOM_FILE)
    rf = load_ff3_rf(FF3_FILE)

    bm_excess = prepare_excess_returns(bm, rf, "Q2(a) BM")
    mom_excess = prepare_excess_returns(momentum, rf, "Q2(b) momentum")

    combined_returns = bm.add_prefix("BM: ").join(
        momentum.add_prefix("MOM: "), how="inner"
    )
    combined_excess = prepare_excess_returns(
        combined_returns, rf, "Q2(c) combined BM-momentum"
    )
    if combined_excess.shape[1] != 20:
        raise ValueError(
            f"Expected 20 combined portfolios; found {combined_excess.shape[1]}"
        )

    _, _, _, _, q2a_x_r_star = maximum_sharpe_results(bm_excess)
    _, _, _, _, q2b_x_r_star = maximum_sharpe_results(mom_excess)
    (
        q2c_b_hat,
        q2c_a_hat,
        q2c_m_star,
        q2c_pricing_errors,
        q2c_x_r_star,
    ) = maximum_sharpe_results(combined_excess)

    q2a_sharpe = sharpe_ratio(q2a_x_r_star)
    q2b_sharpe = sharpe_ratio(q2b_x_r_star)
    q2c_sharpe = sharpe_ratio(q2c_x_r_star)

    mean_m = float(q2c_m_star.mean())
    max_abs_error = float(np.max(np.abs(q2c_pricing_errors)))
    if not np.isclose(mean_m, 1.0, atol=1e-12):
        raise ArithmeticError(f"SDF normalization failed: mean(M*) = {mean_m:.16g}")
    if max_abs_error > 1e-12:
        raise ArithmeticError(
            f"In-sample pricing check failed: max absolute error = {max_abs_error:.3e}"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "month": combined_excess.index.astype(str),
            "M_star": q2c_m_star,
        }
    ).to_csv(SDF_FILE, index=False, float_format="%.12f")
    save_coefficient_plot(q2c_b_hat[:10], q2c_b_hat[10:], PLOT_FILE)

    diagnostics = pd.DataFrame(
        {
            "portfolio": combined_excess.columns,
            "b_hat": q2c_b_hat,
            "mean_M_times_xR": q2c_pricing_errors,
        }
    )
    print(
        "Combined sample: "
        f"{combined_excess.index.min()} to {combined_excess.index.max()} "
        f"({len(combined_excess)} months)"
    )
    print(f"a_hat: {q2c_a_hat:.12f}")
    print(f"mean(M_star): {mean_m:.12f}")
    print(f"max abs mean(M_star * xR_j): {max_abs_error:.3e}")
    print("Monthly Sharpe ratios (standard deviation uses ddof=0):")
    print(f"  Q2(a), BM portfolios:             {q2a_sharpe:.12f}")
    print(f"  Q2(b), momentum portfolios:       {q2b_sharpe:.12f}")
    print(f"  Q2(c), BM and momentum combined:  {q2c_sharpe:.12f}")
    print(diagnostics.to_string(index=False, float_format=lambda value: f"{value:.12g}"))
    print(f"Wrote {SDF_FILE.relative_to(REPO_ROOT)}")
    print(f"Wrote {PLOT_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
