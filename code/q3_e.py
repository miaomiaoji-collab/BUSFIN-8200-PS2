"""Estimate the 21-moment identity-weighted GMM model for Q3(e).

The test assets are the wealth return and the 20 book-to-market and momentum
decile excess returns constructed exactly as in Q2.  The script estimates the
nonlinear SDF parameter, applies a common automatic Newey-West bandwidth, and
reports parameter-adjusted pricing-error inference and the joint test.
"""

from __future__ import annotations

from math import exp, factorial, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from q2_a import (
    PORTFOLIOS as BM_PORTFOLIOS,
    load_bm_value_weighted_monthly,
    load_ff3_rf,
    require_complete_monthly_sample,
)
from q2_b import (
    PORTFOLIOS as MOM_PORTFOLIOS,
    load_momentum_value_weighted_monthly,
)
from q2_c import prepare_excess_returns
from q3_c import automatic_bandwidth, hac_matrix, load_data


REPO_ROOT = Path(__file__).resolve().parents[1]
EQ_FILE = REPO_ROOT / "data" / "EQ Dataset.csv"
BM_FILE = REPO_ROOT / "data" / "Portfolios_Formed_on_BE-ME.csv"
MOM_FILE = REPO_ROOT / "data" / "10_Portfolios_Prior_12_2.csv"
FF3_FILE = REPO_ROOT / "data" / "ff3.csv"
RESULTS_DIR = REPO_ROOT / "results"
SUMMARY_FILE = RESULTS_DIR / "q3e_gmm_summary.tex"
PRICING_ERRORS_FILE = RESULTS_DIR / "q3e_pricing_errors.tex"

EXPECTED_ASSETS = 21
JOINT_TEST_DF = EXPECTED_ASSETS - 1
PSEUDOINVERSE_RCOND = 1e-10


def prepare_returns() -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    """Construct and align xRw and the 20 Q2 portfolio excess returns."""
    equity = load_data(EQ_FILE)
    equity["month"] = pd.PeriodIndex(equity["date"], freq="M")
    equity = equity.set_index("month")[["re", "x_rw"]]

    bm = load_bm_value_weighted_monthly(BM_FILE)
    momentum = load_momentum_value_weighted_monthly(MOM_FILE)
    rf = load_ff3_rf(FF3_FILE)
    combined_returns = bm.add_prefix("BM: ").join(
        momentum.add_prefix("MOM: "), how="inner"
    )
    portfolio_excess = prepare_excess_returns(
        combined_returns, rf, "Q3(e) combined BM-momentum"
    )
    if portfolio_excess.shape[1] != 20:
        raise ValueError(
            f"Expected 20 portfolio excess returns; found {portfolio_excess.shape[1]}"
        )

    sample = equity.join(portfolio_excess, how="inner")
    require_complete_monthly_sample(sample)
    if sample.empty:
        raise ValueError("The common EQ-BM-MOM sample is empty")

    asset_names = ["xRw"] + list(portfolio_excess.columns)
    excess_returns = np.column_stack(
        [sample["x_rw"].to_numpy(dtype=float), portfolio_excess.loc[sample.index]]
    ).astype(float)
    if excess_returns.shape[1] != EXPECTED_ASSETS:
        raise ValueError(
            f"Expected {EXPECTED_ASSETS} test assets; found {excess_returns.shape[1]}"
        )
    if not np.isfinite(excess_returns).all():
        raise ValueError("The common excess-return sample contains non-finite values")
    return sample, excess_returns, asset_names


def moment_matrix(
    gamma: float, log_rw: np.ndarray, excess_returns: np.ndarray
) -> np.ndarray:
    """Return the T by 21 matrix of observation-level SDF moments."""
    discount_factor = np.exp(-gamma * log_rw)
    return discount_factor[:, None] * excess_returns


def moment_mean_and_derivative(
    gamma: float, log_rw: np.ndarray, excess_returns: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Return the 21 mean moments and their 21 by 1 derivative."""
    moments = moment_matrix(gamma, log_rw, excess_returns)
    mean_moment = moments.mean(axis=0)
    derivative = (-log_rw[:, None] * moments).mean(axis=0)[:, None]
    return mean_moment, derivative


def solve_identity_gmm(
    log_rw: np.ndarray, excess_returns: np.ndarray
) -> float:
    """Minimize the unconstrained 21-moment identity-GMM objective."""
    def first_order_condition(gamma: float) -> float:
        mean_moment, derivative = moment_mean_and_derivative(
            gamma, log_rw, excess_returns
        )
        return float(derivative[:, 0] @ mean_moment)

    lower, upper = -1.0, 1.0
    f_lower = first_order_condition(lower)
    f_upper = first_order_condition(upper)
    for _ in range(20):
        if not np.isfinite([f_lower, f_upper]).all():
            raise ArithmeticError("The Q3(e) GMM derivative became non-finite")
        if np.signbit(f_lower) != np.signbit(f_upper):
            break
        lower *= 2.0
        upper *= 2.0
        f_lower = first_order_condition(lower)
        f_upper = first_order_condition(upper)
    else:
        raise ArithmeticError("Could not bracket a Q3(e) stationary point")

    for _ in range(250):
        midpoint = (lower + upper) / 2.0
        f_midpoint = first_order_condition(midpoint)
        if abs(f_midpoint) <= 1e-14:
            gamma_hat = midpoint
            break
        if np.signbit(f_lower) != np.signbit(f_midpoint):
            upper = midpoint
        else:
            lower = midpoint
            f_lower = f_midpoint
        if upper - lower <= 1e-13 * max(1.0, abs(midpoint)):
            gamma_hat = (lower + upper) / 2.0
            break
    else:
        raise ArithmeticError("The Q3(e) identity-GMM bisection did not converge")

    def objective(gamma: float) -> float:
        mean_moment = moment_mean_and_derivative(
            gamma, log_rw, excess_returns
        )[0]
        return float(mean_moment @ mean_moment)

    step = 1e-4 * max(1.0, abs(gamma_hat))
    if not (
        objective(gamma_hat) < objective(gamma_hat - step)
        and objective(gamma_hat) < objective(gamma_hat + step)
    ):
        raise ArithmeticError("The Q3(e) stationary point is not a local minimum")
    return float(gamma_hat)


def chi_square_survival_even(statistic: float, degrees_of_freedom: int) -> float:
    """Return the exact chi-square survival probability for positive even df."""
    if statistic < 0.0:
        raise ValueError("The chi-square statistic cannot be negative")
    if degrees_of_freedom <= 0 or degrees_of_freedom % 2:
        raise ValueError("This survival calculation requires positive even df")
    half_statistic = statistic / 2.0
    terms = sum(
        half_statistic**index / factorial(index)
        for index in range(degrees_of_freedom // 2)
    )
    return float(exp(-half_statistic) * terms)


def calculate_inference(
    moments: np.ndarray, derivative: np.ndarray, covariance: np.ndarray
) -> tuple[float, np.ndarray, np.ndarray, float, float]:
    """Calculate identity-GMM and parameter-adjusted pricing-error inference."""
    sample_size, number_of_moments = moments.shape
    dtd = float((derivative.T @ derivative).item())
    if np.isclose(dtd, 0.0):
        raise ArithmeticError("The Q3(e) GMM derivative has zero rank")

    gamma_variance = float(
        (derivative.T @ covariance @ derivative).item()
        / (sample_size * dtd**2)
    )
    gamma_standard_error = sqrt(gamma_variance)

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

    standard_errors = np.sqrt(
        np.maximum(np.diag(pricing_error_covariance), 0.0)
    )
    if np.any(np.isclose(standard_errors, 0.0)):
        raise ArithmeticError("A Q3(e) pricing-error standard error is zero")

    pricing_errors = moments.mean(axis=0)
    t_statistics = pricing_errors / standard_errors
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
        gamma_standard_error,
        standard_errors,
        t_statistics,
        joint_statistic,
        joint_p_value,
    )


def write_summary_table(
    path: Path,
    gamma_hat: float,
    gamma_standard_error: float,
    sample_size: int,
    bandwidth: int,
    joint_statistic: float,
    joint_p_value: float,
) -> None:
    """Write the requested Q3(e) GMM summary table."""
    p_value_text = (
        f"{joint_p_value:.3e}" if joint_p_value < 1e-4 else f"{joint_p_value:.6f}"
    )
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Identity-weighted GMM summary for Q3(e).}",
        r"\label{tab:q3e-gmm-summary}",
        r"\begin{tabular}{lr}",
        r"\hline",
        r"Quantity & Value \\",
        r"\hline",
        f"$\\widehat{{\\gamma}}$ & {gamma_hat:.6f} \\\\ ".rstrip(),
        f"Newey-West standard error & {gamma_standard_error:.6f} \\\\ ".rstrip(),
        f"Observations & {sample_size} \\\\ ".rstrip(),
        f"Common bandwidth $L$ & {bandwidth} \\\\ ".rstrip(),
        f"Joint $\\chi^2({JOINT_TEST_DF})$ & {joint_statistic:.6f} \\\\ ".rstrip(),
        f"Joint-test $p$-value & {p_value_text} \\\\ ".rstrip(),
        r"\hline",
        r"\end{tabular}",
        r"\end{table}",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def display_name(name: str) -> str:
    """Return a concise LaTeX-safe portfolio label."""
    if name == "xRw":
        return r"Wealth portfolio ($xR_w$)"
    return name.replace(": ", " ")


def write_pricing_error_table(
    path: Path,
    asset_names: list[str],
    pricing_errors: np.ndarray,
    standard_errors: np.ndarray,
    t_statistics: np.ndarray,
) -> None:
    """Write all 21 pricing errors and t-statistics to a LaTeX table."""
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\small",
        r"\caption{Pricing errors for the Q3(e) identity-weighted GMM model.}",
        r"\label{tab:q3e-pricing-errors}",
        r"\begin{tabular}{lrrr}",
        r"\hline",
        r"Portfolio & Pricing error & Standard error & $t$-statistic \\",
        r"\hline",
    ]
    for name, error, standard_error, t_statistic in zip(
        asset_names, pricing_errors, standard_errors, t_statistics, strict=True
    ):
        lines.append(
            f"{display_name(name)} & {error:.6f} & {standard_error:.6f} & "
            f"{t_statistic:.3f} \\\\"
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
    log_rw = sample["re"].to_numpy(dtype=float)
    gamma_hat = solve_identity_gmm(log_rw, excess_returns)
    moments = moment_matrix(gamma_hat, log_rw, excess_returns)
    pricing_errors, derivative = moment_mean_and_derivative(
        gamma_hat, log_rw, excess_returns
    )

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
        gamma_standard_error,
        pricing_error_standard_errors,
        t_statistics,
        joint_statistic,
        joint_p_value,
    ) = calculate_inference(moments, derivative, covariance)

    write_summary_table(
        SUMMARY_FILE,
        gamma_hat,
        gamma_standard_error,
        len(sample),
        bandwidth,
        joint_statistic,
        joint_p_value,
    )
    write_pricing_error_table(
        PRICING_ERRORS_FILE,
        asset_names,
        pricing_errors,
        pricing_error_standard_errors,
        t_statistics,
    )

    diagnostics = pd.DataFrame(
        {
            "portfolio": asset_names,
            "pricing_error": pricing_errors,
            "standard_error": pricing_error_standard_errors,
            "t_statistic": t_statistics,
        }
    )
    print(
        f"Sample: {sample.index.min()} to {sample.index.max()} "
        f"({len(sample)} observations)"
    )
    print(f"gamma_hat: {gamma_hat:.12f}")
    print(f"Newey-West standard error: {gamma_standard_error:.12f}")
    print(f"q: {pilot_lag}")
    print(f"s0_hat: {s0_hat:.12f}")
    print(f"s1_hat: {s1_hat:.12f}")
    print(f"const_hat: {const_hat:.12f}")
    print(f"bandwidth_unrounded: {bandwidth_unrounded:.12f}")
    print(f"L: {bandwidth}")
    print(f"joint chi-square statistic: {joint_statistic:.12f}")
    print(f"joint-test df: {JOINT_TEST_DF}")
    print(f"joint-test p-value: {joint_p_value:.12f}")
    print(diagnostics.to_string(index=False, float_format=lambda value: f"{value:.12g}"))
    print(f"Wrote {SUMMARY_FILE.relative_to(REPO_ROOT)}")
    print(f"Wrote {PRICING_ERRORS_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
