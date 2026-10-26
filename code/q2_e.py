"""Estimate the Q2(e) SDF in sample and evaluate it out of sample.

The script estimates the combined 20-portfolio maximum-Sharpe SDF using
July 1927 through December 1989, keeps the estimated coefficients fixed,
constructs the SDF from January 1990 onward, and reports the 20
out-of-sample pricing errors. All covariance calculations use divisor T.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from q2_a import load_bm_value_weighted_monthly, load_ff3_rf
from q2_b import load_momentum_value_weighted_monthly
from q2_c import prepare_excess_returns
from q2_d import pricing_errors


REPO_ROOT = Path(__file__).resolve().parents[1]
BM_FILE = REPO_ROOT / "data" / "Portfolios_Formed_on_BE-ME.csv"
MOM_FILE = REPO_ROOT / "data" / "10_Portfolios_Prior_12_2.csv"
FF3_FILE = REPO_ROOT / "data" / "ff3.csv"
OUTPUT_DIR = REPO_ROOT / "results"
CSV_FILE = OUTPUT_DIR / "q2e_pricing_errors.csv"
LATEX_FILE = OUTPUT_DIR / "q2e_pricing_errors.tex"

ESTIMATION_END = pd.Period("1989-12", freq="M")
OUT_OF_SAMPLE_START = pd.Period("1990-01", freq="M")


def estimate_sdf_coefficients(excess_returns: pd.DataFrame) -> tuple[float, np.ndarray]:
    """Estimate a and b using covariance divisor T over the supplied sample."""
    x_r = excess_returns.to_numpy(dtype=float)
    mu_hat = x_r.mean(axis=0)
    demeaned = x_r - mu_hat
    sigma_hat = demeaned.T @ demeaned / len(x_r)
    b_hat = np.linalg.solve(sigma_hat, mu_hat)
    a_hat = 1.0 + b_hat @ mu_hat
    return float(a_hat), b_hat


def save_latex_table(results: pd.DataFrame, path: Path) -> None:
    """Write a dependency-free LaTeX table of out-of-sample pricing errors."""
    def format_value(value: float) -> str:
        displayed_value = 0.0 if abs(value) < 0.5e-6 else value
        return f"{displayed_value:.6f}"

    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Out-of-sample pricing errors for the Q2(e) SDF.}",
        r"\label{tab:q2e-pricing-errors}",
        r"\begin{tabular}{lr}",
        r"\hline",
        r"Portfolio & $\widehat{\alpha}_{OS}$ \\",
        r"\hline",
    ]
    for row in results.itertuples(index=False):
        lines.append(
            f"{row.portfolio} & {format_value(row.alpha_hat_OS)} \\\\"
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
        combined_returns, rf, "Q2(e) combined BM-momentum"
    )
    if excess_returns.shape[1] != 20:
        raise ValueError(f"Expected 20 portfolios; found {excess_returns.shape[1]}")

    estimation_sample = excess_returns.loc[:ESTIMATION_END]
    out_of_sample = excess_returns.loc[OUT_OF_SAMPLE_START:]
    if estimation_sample.empty or out_of_sample.empty:
        raise ValueError("The estimation or out-of-sample period is empty")
    if estimation_sample.index.max() != ESTIMATION_END:
        raise ValueError(f"The estimation sample does not end in {ESTIMATION_END}")
    if out_of_sample.index.min() != OUT_OF_SAMPLE_START:
        raise ValueError(
            f"The out-of-sample period does not begin in {OUT_OF_SAMPLE_START}"
        )
    if len(estimation_sample) + len(out_of_sample) != len(excess_returns):
        raise ValueError("The sample split omits or duplicates one or more months")

    a_hat_is, b_hat_is = estimate_sdf_coefficients(estimation_sample)

    x_r_is = estimation_sample.to_numpy(dtype=float)
    m_star_is = a_hat_is - x_r_is @ b_hat_is
    is_pricing_errors = (m_star_is[:, None] * x_r_is).mean(axis=0)
    if not np.isclose(m_star_is.mean(), 1.0, atol=1e-12):
        raise ArithmeticError(
            f"In-sample SDF normalization failed: mean = {m_star_is.mean():.16g}"
        )
    if np.max(np.abs(is_pricing_errors)) > 1e-12:
        raise ArithmeticError(
            "The estimated SDF does not price the estimation-sample returns: "
            f"max error = {np.max(np.abs(is_pricing_errors)):.3e}"
        )

    x_r_os = out_of_sample.to_numpy(dtype=float)
    m_star_os = a_hat_is - x_r_os @ b_hat_is
    alpha_hat_os = pricing_errors(x_r_os, m_star_os)
    results = pd.DataFrame(
        {
            "portfolio": out_of_sample.columns,
            "alpha_hat_OS": alpha_hat_os,
        }
    )

    # The covariance-form alpha equals mean(M*xR) only when mean(M)=1.
    # The out-of-sample mean of a fixed in-sample-normalized SDF need not equal 1.
    negative_m_os = -m_star_os
    covariance_check = (
        (negative_m_os - negative_m_os.mean())[:, None]
        * (x_r_os - x_r_os.mean(axis=0))
    ).mean(axis=0)
    direct_alpha_check = x_r_os.mean(axis=0) - covariance_check
    if not np.allclose(alpha_hat_os, direct_alpha_check, rtol=0.0, atol=1e-15):
        raise ArithmeticError("Out-of-sample pricing-error calculation failed validation")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results.to_csv(CSV_FILE, index=False, float_format="%.12g")
    save_latex_table(results, LATEX_FILE)

    print(
        "Estimation sample: "
        f"{estimation_sample.index.min()} to {estimation_sample.index.max()} "
        f"({len(estimation_sample)} months)"
    )
    print(
        "Out-of-sample period: "
        f"{out_of_sample.index.min()} to {out_of_sample.index.max()} "
        f"({len(out_of_sample)} months)"
    )
    print(f"a_hat_IS: {a_hat_is:.12f}")
    print(f"mean(M_star_IS): {m_star_is.mean():.12f}")
    print(f"mean(M_star_OS): {m_star_os.mean():.12f}")
    print(
        "max abs in-sample mean(M_star_IS * xR): "
        f"{np.max(np.abs(is_pricing_errors)):.3e}"
    )
    print("Out-of-sample pricing errors are monthly decimal returns; covariance uses ddof=0.")
    print(results.to_string(index=False, float_format=lambda value: f"{value:.12g}"))
    print(f"max abs alpha_hat_OS: {np.max(np.abs(alpha_hat_os)):.12g}")
    print(f"Wrote {CSV_FILE.relative_to(REPO_ROOT)}")
    print(f"Wrote {LATEX_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
