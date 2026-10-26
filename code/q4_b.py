"""Estimate the Q4(b) normalized linear-SDF model by identity GMM.

The 21 test assets and common monthly sample are identical to Q3(e).  The
script estimates the linear SDF slope, applies the same automatic-bandwidth
Newey-West procedure, and reports parameter-adjusted pricing-error inference.
"""

from __future__ import annotations

from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from q3_c import automatic_bandwidth, hac_matrix
from q3_e import (
    chi_square_survival_even,
    display_name,
    prepare_returns,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_FILE = RESULTS_DIR / "q4b_results.tex"

EXPECTED_ASSETS = 21
JOINT_TEST_DF = EXPECTED_ASSETS - 1
PSEUDOINVERSE_RCOND = 1e-10


def moment_components(
    wealth_return: np.ndarray, excess_returns: np.ndarray
) -> tuple[float, np.ndarray, np.ndarray]:
    """Return mean wealth, mean excess returns, and the GMM derivative."""
    mean_wealth_return = float(wealth_return.mean())
    wealth_deviation = wealth_return - mean_wealth_return
    mean_excess_return = excess_returns.mean(axis=0)
    derivative = (
        -(wealth_deviation[:, None] * excess_returns).mean(axis=0)
    )[:, None]
    return mean_wealth_return, mean_excess_return, derivative


def solve_identity_gmm(
    mean_excess_return: np.ndarray, derivative: np.ndarray
) -> float:
    """Return the analytical minimizer of gbar(b)'gbar(b)."""
    dtd = float((derivative.T @ derivative).item())
    if np.isclose(dtd, 0.0):
        raise ArithmeticError("The Q4(b) GMM derivative has zero rank")
    return float(-(derivative[:, 0] @ mean_excess_return) / dtd)


def moment_matrix(
    b_value: float,
    wealth_return: np.ndarray,
    mean_wealth_return: float,
    excess_returns: np.ndarray,
) -> np.ndarray:
    """Return the T by 21 matrix of observation-level linear-SDF moments."""
    sdf = 1.0 - b_value * (wealth_return - mean_wealth_return)
    return sdf[:, None] * excess_returns


def calculate_inference(
    moments: np.ndarray, derivative: np.ndarray, covariance: np.ndarray
) -> tuple[float, np.ndarray, np.ndarray, float, float]:
    """Calculate identity-GMM and parameter-adjusted pricing-error inference."""
    sample_size, number_of_moments = moments.shape
    if number_of_moments != EXPECTED_ASSETS:
        raise ValueError(
            f"Expected {EXPECTED_ASSETS} moments; found {number_of_moments}"
        )

    dtd = float((derivative.T @ derivative).item())
    if np.isclose(dtd, 0.0):
        raise ArithmeticError("The Q4(b) GMM derivative has zero rank")
    b_variance = float(
        (derivative.T @ covariance @ derivative).item()
        / (sample_size * dtd**2)
    )
    if b_variance < 0.0:
        raise ArithmeticError("The estimated variance of b is negative")
    b_standard_error = sqrt(b_variance)

    projection = np.eye(number_of_moments) - derivative @ derivative.T / dtd
    pricing_error_covariance = (
        projection @ covariance @ projection.T / sample_size
    )
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
        raise ArithmeticError("A Q4(b) pricing-error standard error is zero")

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
    """Write the requested Q4(b) summary and pricing errors as one table."""
    p_value_text = (
        f"{joint_p_value:.3e}" if joint_p_value < 1e-4 else f"{joint_p_value:.6f}"
    )
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\small",
        r"\caption{Identity-weighted GMM results for the normalized linear SDF.}",
        r"\label{tab:q4b-results}",
        r"\begin{tabular}{lrr}",
        r"\hline",
        r"Quantity or portfolio & Estimate & $t$-statistic \\",
        r"\hline",
        f"$\\widehat{{b}}$ & {b_hat:.6f} & \\\\",
        f"Newey-West standard error of $\\widehat{{b}}$ & {b_standard_error:.6f} & \\\\",
        f"Observations & {sample_size} & \\\\",
        f"Common bandwidth $L$ & {bandwidth} & \\\\",
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
    b_hat = solve_identity_gmm(mean_excess_return, derivative)
    moments = moment_matrix(
        b_hat, wealth_return, mean_wealth_return, excess_returns
    )
    sdf = 1.0 - b_hat * (wealth_return - mean_wealth_return)
    mean_sdf = float(sdf.mean())
    if not np.isclose(mean_sdf, 1.0, rtol=0.0, atol=1e-14):
        raise ArithmeticError(f"The Q4(b) SDF normalization failed: {mean_sdf}")

    (
        pilot_lag,
        s0_hat,
        s1_hat,
        const_hat,
        bandwidth_unrounded,
        bandwidth,
    ) = automatic_bandwidth(moments[:, 0])
    covariance = hac_matrix(moments, bandwidth)
    (
        b_standard_error,
        pricing_errors,
        t_statistics,
        joint_statistic,
        joint_p_value,
    ) = calculate_inference(moments, derivative, covariance)

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
    first_order_condition = float(derivative[:, 0] @ pricing_errors)
    print(
        f"Sample: {sample.index.min()} to {sample.index.max()} "
        f"({len(sample)} observations)"
    )
    print(f"Rw_bar: {mean_wealth_return:.12f}")
    print(f"mean(M): {mean_sdf:.12f}")
    print(f"b_hat: {b_hat:.12f}")
    print(f"Newey-West standard error: {b_standard_error:.12f}")
    print(f"identity-GMM first-order condition: {first_order_condition:.3e}")
    print(f"q: {pilot_lag}")
    print(f"s0_hat: {s0_hat:.12f}")
    print(f"s1_hat: {s1_hat:.12f}")
    print(f"const_hat: {const_hat:.12f}")
    print(f"bandwidth_unrounded: {bandwidth_unrounded:.12f}")
    print(f"L: {bandwidth}")
    print(f"joint chi-square statistic: {joint_statistic:.12f}")
    print(f"joint-test df: {JOINT_TEST_DF}")
    print(f"joint-test p-value: {joint_p_value:.12e}")
    print(diagnostics.to_string(index=False, float_format=lambda value: f"{value:.12g}"))
    print(f"Wrote {RESULTS_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
