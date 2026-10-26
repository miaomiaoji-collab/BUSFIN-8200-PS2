"""Estimate the Q4(c) normalized linear-SDF model by efficient GMM.

The script exactly reproduces the Q4(b) identity-GMM first step and its
Newey-West covariance matrix, uses the inverse covariance as the efficient
weighting matrix, and reports parameter-adjusted pricing-error inference.
"""

from __future__ import annotations

from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from q3_c import automatic_bandwidth, hac_matrix
from q3_e import chi_square_survival_even, display_name, prepare_returns
from q4_b import moment_components, moment_matrix, solve_identity_gmm


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_FILE = RESULTS_DIR / "q4c_results.tex"

EXPECTED_ASSETS = 21
JOINT_TEST_DF = EXPECTED_ASSETS - 1
PSEUDOINVERSE_RCOND = 1e-10


def solve_efficient_gmm(
    mean_excess_return: np.ndarray,
    derivative: np.ndarray,
    weighting_matrix: np.ndarray,
) -> float:
    """Return the analytical efficient-GMM estimator of b."""
    c_hat = -derivative[:, 0]
    denominator = float(c_hat @ weighting_matrix @ c_hat)
    if denominator <= 0.0:
        raise ArithmeticError("The Q4(c) efficient-GMM denominator is not positive")
    numerator = float(c_hat @ weighting_matrix @ mean_excess_return)
    return numerator / denominator


def calculate_efficient_inference(
    moments: np.ndarray,
    derivative: np.ndarray,
    first_step_covariance: np.ndarray,
    weighting_matrix: np.ndarray,
) -> tuple[float, np.ndarray, np.ndarray, float, float]:
    """Calculate efficient-GMM and parameter-adjusted pricing-error inference."""
    sample_size, number_of_moments = moments.shape
    if number_of_moments != EXPECTED_ASSETS:
        raise ValueError(
            f"Expected {EXPECTED_ASSETS} moments; found {number_of_moments}"
        )

    information = float(
        (derivative.T @ weighting_matrix @ derivative).item()
    )
    if information <= 0.0:
        raise ArithmeticError("The Q4(c) efficient-GMM information is not positive")
    b_standard_error = sqrt(1.0 / (sample_size * information))

    pricing_error_covariance = (
        first_step_covariance - derivative @ derivative.T / information
    ) / sample_size
    pricing_error_covariance = (
        pricing_error_covariance + pricing_error_covariance.T
    ) / 2.0
    eigenvalues = np.linalg.eigvalsh(pricing_error_covariance)
    if eigenvalues.min() < -1e-12:
        raise ArithmeticError("The pricing-error covariance is not positive semidefinite")

    pricing_error_standard_errors = np.sqrt(
        np.maximum(np.diag(pricing_error_covariance), 0.0)
    )
    if np.any(np.isclose(pricing_error_standard_errors, 0.0)):
        raise ArithmeticError("A Q4(c) pricing-error standard error is zero")

    pricing_errors = moments.mean(axis=0)
    t_statistics = pricing_errors / pricing_error_standard_errors
    joint_statistic = float(
        pricing_errors
        @ np.linalg.pinv(
            pricing_error_covariance,
            rcond=PSEUDOINVERSE_RCOND,
            hermitian=True,
        )
        @ pricing_errors
    )
    joint_p_value = chi_square_survival_even(joint_statistic, JOINT_TEST_DF)
    return (
        b_standard_error,
        pricing_errors,
        t_statistics,
        joint_statistic,
        joint_p_value,
    )


def write_results_table(
    path: Path,
    b_hat: float,
    b_standard_error: float,
    sample_size: int,
    bandwidth: int,
    joint_statistic: float,
    joint_p_value: float,
    asset_names: list[str],
    pricing_errors: np.ndarray,
    t_statistics: np.ndarray,
) -> None:
    """Write the requested Q4(c) summary and pricing errors as one table."""
    p_value_text = (
        f"{joint_p_value:.3e}" if joint_p_value < 1e-4 else f"{joint_p_value:.6f}"
    )
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\small",
        r"\caption{Efficient GMM results for the normalized linear SDF.}",
        r"\label{tab:q4c-results}",
        r"\begin{tabular}{lrr}",
        r"\hline",
        r"Quantity or portfolio & Estimate & $t$-statistic \\",
        r"\hline",
        f"$\\widehat{{b}}_{{eff}}$ & {b_hat:.6f} & \\\\",
        f"Newey-West standard error of $\\widehat{{b}}_{{eff}}$ & {b_standard_error:.6f} & \\\\",
        f"Observations & {sample_size} & \\\\",
        f"Q4(b) common bandwidth $L$ & {bandwidth} & \\\\",
        f"Joint $\\chi^2({JOINT_TEST_DF})$ & {joint_statistic:.6f} & \\\\",
        f"Joint-test $p$-value & {p_value_text} & \\\\",
        r"\hline",
        r"\multicolumn{3}{l}{\textit{Pricing errors}} \\",
        r"\hline",
    ]
    for name, error, t_statistic in zip(
        asset_names, pricing_errors, t_statistics, strict=True
    ):
        lines.append(
            f"{display_name(name)} & {error:.6f} & {t_statistic:.3f} \\\\"
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
    mean_wealth_return, mean_excess_return, derivative = moment_components(
        wealth_return, excess_returns
    )

    first_step_b = solve_identity_gmm(mean_excess_return, derivative)
    first_step_moments = moment_matrix(
        first_step_b, wealth_return, mean_wealth_return, excess_returns
    )
    (
        pilot_lag,
        s0_hat,
        s1_hat,
        const_hat,
        bandwidth_unrounded,
        bandwidth,
    ) = automatic_bandwidth(first_step_moments[:, 0])
    first_step_covariance = hac_matrix(first_step_moments, bandwidth)
    weighting_matrix = np.linalg.inv(first_step_covariance)

    b_hat = solve_efficient_gmm(
        mean_excess_return, derivative, weighting_matrix
    )
    moments = moment_matrix(
        b_hat, wealth_return, mean_wealth_return, excess_returns
    )
    sdf = 1.0 - b_hat * (wealth_return - mean_wealth_return)
    mean_sdf = float(sdf.mean())
    if not np.isclose(mean_sdf, 1.0, rtol=0.0, atol=1e-14):
        raise ArithmeticError(f"The Q4(c) SDF normalization failed: {mean_sdf}")

    (
        b_standard_error,
        pricing_errors,
        t_statistics,
        joint_statistic,
        joint_p_value,
    ) = calculate_efficient_inference(
        moments,
        derivative,
        first_step_covariance,
        weighting_matrix,
    )

    write_results_table(
        RESULTS_FILE,
        b_hat,
        b_standard_error,
        len(sample),
        bandwidth,
        joint_statistic,
        joint_p_value,
        asset_names,
        pricing_errors,
        t_statistics,
    )

    diagnostics = pd.DataFrame(
        {
            "portfolio": asset_names,
            "pricing_error": pricing_errors,
            "t_statistic": t_statistics,
        }
    )
    first_order_condition = float(
        derivative[:, 0] @ weighting_matrix @ pricing_errors
    )
    print(
        f"Sample: {sample.index.min()} to {sample.index.max()} "
        f"({len(sample)} observations)"
    )
    print(f"Rw_bar: {mean_wealth_return:.12f}")
    print(f"mean(M): {mean_sdf:.12f}")
    print(f"first-step b_hat: {first_step_b:.12f}")
    print(f"b_hat_eff: {b_hat:.12f}")
    print(f"Newey-West standard error: {b_standard_error:.12f}")
    print(f"efficient-GMM first-order condition: {first_order_condition:.3e}")
    print(f"q: {pilot_lag}")
    print(f"s0_hat: {s0_hat:.12f}")
    print(f"s1_hat: {s1_hat:.12f}")
    print(f"const_hat: {const_hat:.12f}")
    print(f"bandwidth_unrounded: {bandwidth_unrounded:.12f}")
    print(f"L: {bandwidth}")
    print(f"cond(S_hat_Q4b): {np.linalg.cond(first_step_covariance):.12f}")
    print(f"joint chi-square statistic: {joint_statistic:.12f}")
    print(f"joint-test df: {JOINT_TEST_DF}")
    print(f"joint-test p-value: {joint_p_value:.12e}")
    print(diagnostics.to_string(index=False, float_format=lambda value: f"{value:.12g}"))
    print(f"Wrote {RESULTS_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
