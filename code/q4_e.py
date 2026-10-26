"""Estimate the Q4(e) one-factor model using Fama-MacBeth regressions.

The script estimates full-sample time-series betas for the 21 Q4(b) assets,
runs a through-origin cross-sectional regression in every month, and applies
automatic-bandwidth Newey-West inference to the lambda and pricing-error
time series.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from q3_e import display_name, prepare_returns
from q4_d import estimate_mean_newey_west


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_FILE = RESULTS_DIR / "q4e_results.tex"

EXPECTED_ASSETS = 21


def estimate_full_sample_betas(
    factor: np.ndarray, excess_returns: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate intercepts and slopes from the 21 full-sample regressions."""
    sample_size, number_of_assets = excess_returns.shape
    if number_of_assets != EXPECTED_ASSETS:
        raise ValueError(
            f"Expected {EXPECTED_ASSETS} assets; found {number_of_assets}"
        )
    design = np.column_stack([np.ones(sample_size), factor])
    if np.linalg.matrix_rank(design) != design.shape[1]:
        raise ArithmeticError("The Q4(e) first-stage design matrix is singular")
    coefficients = np.linalg.solve(
        design.T @ design,
        design.T @ excess_returns,
    )
    return coefficients[0], coefficients[1]


def run_fama_macbeth(
    betas: np.ndarray, excess_returns: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Run each monthly cross section through the origin."""
    beta_sum_of_squares = float(betas @ betas)
    if beta_sum_of_squares <= 0.0:
        raise ArithmeticError("The Q4(e) first-stage betas have zero rank")
    lambda_t = excess_returns @ betas / beta_sum_of_squares
    pricing_errors_t = excess_returns - lambda_t[:, None] * betas[None, :]
    return lambda_t, pricing_errors_t


def calculate_mean_inference(
    lambda_t: np.ndarray,
    pricing_errors_t: np.ndarray,
) -> tuple[
    float,
    float,
    int,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    """Estimate the Fama-MacBeth means and Newey-West inference."""
    (
        lambda_hat,
        lambda_standard_error,
        lambda_bandwidth,
        _,
    ) = estimate_mean_newey_west(lambda_t)

    number_of_assets = pricing_errors_t.shape[1]
    alpha_hat = np.empty(number_of_assets)
    alpha_standard_errors = np.empty(number_of_assets)
    alpha_t_statistics = np.empty(number_of_assets)
    alpha_bandwidths = np.empty(number_of_assets, dtype=int)
    for asset_index in range(number_of_assets):
        (
            alpha_hat[asset_index],
            alpha_standard_errors[asset_index],
            alpha_bandwidths[asset_index],
            _,
        ) = estimate_mean_newey_west(pricing_errors_t[:, asset_index])
        alpha_t_statistics[asset_index] = (
            alpha_hat[asset_index] / alpha_standard_errors[asset_index]
        )
    return (
        lambda_hat,
        lambda_standard_error,
        lambda_bandwidth,
        alpha_hat,
        alpha_standard_errors,
        alpha_t_statistics,
        alpha_bandwidths,
    )


def write_results_table(
    path: Path,
    lambda_hat: float,
    lambda_standard_error: float,
    lambda_bandwidth: int,
    sample_size: int,
    asset_names: list[str],
    alpha_hat: np.ndarray,
    alpha_t_statistics: np.ndarray,
) -> None:
    """Write the requested Q4(e) Fama-MacBeth results."""
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\small",
        r"\caption{Fama--MacBeth results for the linear beta-pricing model.}",
        r"\label{tab:q4e-results}",
        r"\begin{tabular}{lrr}",
        r"\hline",
        r"Quantity or portfolio & Estimate & $t$-statistic \\",
        r"\hline",
        f"$\\widehat{{\\lambda}}$ & {lambda_hat:.6f} & \\\\",
        (
            "Newey-West standard error of $\\widehat{\\lambda}$ & "
            f"{lambda_standard_error:.6f} & \\\\"
        ),
        f"Lambda bandwidth $L$ & {lambda_bandwidth} & \\\\",
        f"Observations & {sample_size} & \\\\",
        r"\hline",
        r"\multicolumn{3}{l}{\textit{Average cross-sectional pricing errors}} \\",
        r"\hline",
    ]
    for name, alpha, t_statistic in zip(
        asset_names, alpha_hat, alpha_t_statistics, strict=True
    ):
        lines.append(
            f"{display_name(name)} & {alpha:.6f} & {t_statistic:.3f} \\\\"
        )
    lines.extend(
        [
            r"\hline",
            r"\end{tabular}",
            r"\end{table}",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    sample, excess_returns, asset_names = prepare_returns()
    wealth_return = np.exp(sample["re"].to_numpy(dtype=float))

    first_stage_alphas, betas = estimate_full_sample_betas(
        wealth_return, excess_returns
    )
    lambda_t, pricing_errors_t = run_fama_macbeth(betas, excess_returns)
    (
        lambda_hat,
        lambda_standard_error,
        lambda_bandwidth,
        alpha_hat,
        alpha_standard_errors,
        alpha_t_statistics,
        alpha_bandwidths,
    ) = calculate_mean_inference(lambda_t, pricing_errors_t)

    cross_sectional_normal_equation_error = float(
        np.max(np.abs(pricing_errors_t @ betas))
    )
    if cross_sectional_normal_equation_error > 1e-12:
        raise ArithmeticError(
            "A Q4(e) monthly cross-sectional normal equation failed"
        )

    write_results_table(
        RESULTS_FILE,
        lambda_hat,
        lambda_standard_error,
        lambda_bandwidth,
        len(sample),
        asset_names,
        alpha_hat,
        alpha_t_statistics,
    )

    diagnostics = pd.DataFrame(
        {
            "portfolio": asset_names,
            "first_stage_alpha": first_stage_alphas,
            "beta_hat": betas,
            "alpha_hat": alpha_hat,
            "alpha_standard_error": alpha_standard_errors,
            "alpha_t_statistic": alpha_t_statistics,
            "bandwidth": alpha_bandwidths,
        }
    )
    print(
        f"Sample: {sample.index.min()} to {sample.index.max()} "
        f"({len(sample)} observations)"
    )
    print(f"lambda_hat: {lambda_hat:.12f}")
    print(f"lambda Newey-West standard error: {lambda_standard_error:.12f}")
    print(f"lambda bandwidth: {lambda_bandwidth}")
    print(
        "maximum monthly cross-sectional normal-equation error: "
        f"{cross_sectional_normal_equation_error:.3e}"
    )
    print(diagnostics.to_string(index=False, float_format=lambda value: f"{value:.12g}"))
    print(f"Wrote {RESULTS_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
