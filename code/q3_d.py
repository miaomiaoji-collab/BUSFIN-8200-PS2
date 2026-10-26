"""Estimate the efficient two-step GMM model required for Q3(d).

The first step exactly reproduces Q3(c), including its automatic Newey-West
bandwidth and long-run covariance matrix.  The inverse of that fixed first-step
matrix is then used to estimate gamma and conduct the requested inference.
"""

from __future__ import annotations

from math import erfc, sqrt
from pathlib import Path

import numpy as np

from q3_c import (
    DATA_FILE,
    REPO_ROOT,
    automatic_bandwidth,
    hac_matrix,
    load_data,
    moment_matrix,
    moment_mean_and_derivative,
    prepare_gmm_sample,
    reconstruct_ps1_forecasts,
    solve_identity_gmm,
)


RESULTS_DIR = REPO_ROOT / "results"
LATEX_FILE = RESULTS_DIR / "q3d_results.tex"
PSEUDOINVERSE_RCOND = 1e-10


def solve_efficient_gmm(
    log_rw: np.ndarray,
    x_rw: np.ndarray,
    z_lag: np.ndarray,
    weighting_matrix: np.ndarray,
) -> float:
    """Minimize the unconstrained second-step GMM objective by its FOC."""
    def first_order_condition(gamma: float) -> float:
        mean_moment, derivative = moment_mean_and_derivative(
            gamma, log_rw, x_rw, z_lag
        )
        return float(derivative[:, 0] @ weighting_matrix @ mean_moment)

    lower, upper = -1.0, 1.0
    f_lower = first_order_condition(lower)
    f_upper = first_order_condition(upper)
    for _ in range(20):
        if not np.isfinite([f_lower, f_upper]).all():
            raise ArithmeticError("The efficient-GMM derivative became non-finite")
        if np.signbit(f_lower) != np.signbit(f_upper):
            break
        lower *= 2.0
        upper *= 2.0
        f_lower = first_order_condition(lower)
        f_upper = first_order_condition(upper)
    else:
        raise ArithmeticError("Could not bracket the second-step stationary point")

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
        raise ArithmeticError("The efficient-GMM bisection did not converge")

    def objective(gamma: float) -> float:
        mean_moment = moment_mean_and_derivative(
            gamma, log_rw, x_rw, z_lag
        )[0]
        return float(mean_moment @ weighting_matrix @ mean_moment)

    step = 1e-4 * max(1.0, abs(gamma_hat))
    if not (
        objective(gamma_hat) < objective(gamma_hat - step)
        and objective(gamma_hat) < objective(gamma_hat + step)
    ):
        raise ArithmeticError("The second-step stationary point is not a local minimum")
    return float(gamma_hat)


def calculate_efficient_inference(
    moments: np.ndarray,
    derivative: np.ndarray,
    first_step_covariance: np.ndarray,
    weighting_matrix: np.ndarray,
) -> tuple[float, np.ndarray, np.ndarray, float, float]:
    """Calculate efficient gamma and parameter-adjusted pricing-error inference."""
    sample_size = len(moments)
    information = float(
        (derivative.T @ weighting_matrix @ derivative).item()
    )
    if information <= 0.0:
        raise ArithmeticError("The efficient GMM information scalar is not positive")

    gamma_standard_error = sqrt(1.0 / (sample_size * information))
    pricing_error_covariance = (
        first_step_covariance
        - derivative @ derivative.T / information
    ) / sample_size
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
        raise ArithmeticError("A pricing-error standard error is numerically zero")

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
    joint_p_value = erfc(sqrt(max(joint_statistic, 0.0) / 2.0))
    return (
        gamma_standard_error,
        standard_errors,
        t_statistics,
        joint_statistic,
        joint_p_value,
    )


def write_latex_table(
    path: Path,
    gamma_hat: float,
    gamma_standard_error: float,
    pricing_errors: np.ndarray,
    pricing_error_standard_errors: np.ndarray,
    t_statistics: np.ndarray,
    joint_statistic: float,
    joint_p_value: float,
    sample_size: int,
) -> None:
    """Write the requested Q3(d) results as a dependency-free LaTeX table."""
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Efficient two-step GMM estimates and pricing errors for Q3(d).}",
        r"\label{tab:q3d-results}",
        r"\begin{tabular}{lrrr}",
        r"\hline",
        r"Quantity & Estimate & Standard error & $t$-statistic \\",
        r"\hline",
        f"$\\widehat{{\\gamma}}_{{2step}}$ & {gamma_hat:.6f} & "
        f"{gamma_standard_error:.6f} & \\\\ ".strip(),
        (
            "Wealth-portfolio pricing error & "
            f"{pricing_errors[0]:.6f} & {pricing_error_standard_errors[0]:.6f} "
            f"& {t_statistics[0]:.3f} \\\\"
        ),
        (
            "Managed-portfolio pricing error & "
            f"{pricing_errors[1]:.6f} & {pricing_error_standard_errors[1]:.6f} "
            f"& {t_statistics[1]:.3f} \\\\"
        ),
        r"\hline",
        (
            f"Joint $\\chi^2(1)$ & {joint_statistic:.6f} & "
            f"$p$-value & {joint_p_value:.6f} \\\\"
        ),
        f"Observations & {sample_size} & & \\\\ ".strip(),
        r"\hline",
        r"\end{tabular}",
        r"\end{table}",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    data = load_data(DATA_FILE)
    forecasts = reconstruct_ps1_forecasts(data)
    sample, full_sample_variance = prepare_gmm_sample(data, forecasts)
    log_rw = sample["re"].to_numpy(dtype=float)
    x_rw = sample["x_rw"].to_numpy(dtype=float)
    z_lag = sample["z_lag"].to_numpy(dtype=float)

    first_step_gamma = solve_identity_gmm(log_rw, x_rw, z_lag)
    first_step_moments = moment_matrix(
        first_step_gamma, log_rw, x_rw, z_lag
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

    gamma_hat = solve_efficient_gmm(
        log_rw, x_rw, z_lag, weighting_matrix
    )
    moments = moment_matrix(gamma_hat, log_rw, x_rw, z_lag)
    pricing_errors, derivative = moment_mean_and_derivative(
        gamma_hat, log_rw, x_rw, z_lag
    )
    (
        gamma_standard_error,
        pricing_error_standard_errors,
        t_statistics,
        joint_statistic,
        joint_p_value,
    ) = calculate_efficient_inference(
        moments, derivative, first_step_covariance, weighting_matrix
    )

    write_latex_table(
        LATEX_FILE,
        gamma_hat,
        gamma_standard_error,
        pricing_errors,
        pricing_error_standard_errors,
        t_statistics,
        joint_statistic,
        joint_p_value,
        len(sample),
    )

    print(
        f"Sample: {sample['forecast_date'].iloc[0]:%Y-%m} to "
        f"{sample['forecast_date'].iloc[-1]:%Y-%m} ({len(sample)} observations)"
    )
    print(f"Full-sample Var(Rw), ddof=0: {full_sample_variance:.12f}")
    print(f"first-step gamma_hat: {first_step_gamma:.12f}")
    print(f"gamma_hat_2step: {gamma_hat:.12f}")
    print(f"Newey-West standard error: {gamma_standard_error:.12f}")
    print(f"wealth pricing error: {pricing_errors[0]:.12f}")
    print(f"wealth pricing-error SE: {pricing_error_standard_errors[0]:.12f}")
    print(f"wealth pricing-error t-statistic: {t_statistics[0]:.12f}")
    print(f"managed pricing error: {pricing_errors[1]:.12f}")
    print(f"managed pricing-error SE: {pricing_error_standard_errors[1]:.12f}")
    print(f"managed pricing-error t-statistic: {t_statistics[1]:.12f}")
    print(f"joint chi-square statistic: {joint_statistic:.12f}")
    print(f"joint-test p-value: {joint_p_value:.12f}")
    print(f"q: {pilot_lag}")
    print(f"s0_hat: {s0_hat:.12f}")
    print(f"s1_hat: {s1_hat:.12f}")
    print(f"const_hat: {const_hat:.12f}")
    print(f"bandwidth_unrounded: {bandwidth_unrounded:.12f}")
    print(f"L: {bandwidth}")
    print(f"cond(S_hat_Q3c): {np.linalg.cond(first_step_covariance):.12f}")
    print(f"Wrote {LATEX_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
