"""Estimate the Q4(d) one-factor time-series beta pricing model.

The script uses the Q4(b) sample and 21 assets, estimates each excess return
on a constant and the gross wealth return, applies residual-selected automatic
Newey-West inference, and calculates the standard one-factor GRS test.
"""

from __future__ import annotations

from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd
import mpmath as mp

from q3_c import automatic_bandwidth, hac_matrix
from q3_e import display_name, prepare_returns


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_FILE = RESULTS_DIR / "q4d_results.tex"

EXPECTED_ASSETS = 21
NUMBER_OF_FACTORS = 1


def estimate_mean_newey_west(
    series: np.ndarray,
) -> tuple[float, float, int, np.ndarray]:
    """Estimate a mean and its automatic-bandwidth Newey-West standard error."""
    estimate = float(series.mean())
    residual = series - estimate
    bandwidth = automatic_bandwidth(residual)[-1]
    long_run_variance = float(hac_matrix(residual[:, None], bandwidth)[0, 0])
    standard_error = sqrt(long_run_variance / len(series))
    return estimate, standard_error, bandwidth, residual


def estimate_factor_regressions(
    factor: np.ndarray,
    excess_returns: np.ndarray,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    """Estimate 21 OLS factor regressions with Newey-West alpha inference."""
    sample_size, number_of_assets = excess_returns.shape
    if number_of_assets != EXPECTED_ASSETS:
        raise ValueError(
            f"Expected {EXPECTED_ASSETS} assets; found {number_of_assets}"
        )

    design = np.column_stack([np.ones(sample_size), factor])
    q_matrix = design.T @ design / sample_size
    if np.linalg.matrix_rank(q_matrix) != design.shape[1]:
        raise ArithmeticError("The Q4(d) regression design matrix is singular")
    q_inverse = np.linalg.inv(q_matrix)

    coefficients = np.linalg.solve(
        design.T @ design,
        design.T @ excess_returns,
    )
    fitted = design @ coefficients
    residuals = excess_returns - fitted

    alphas = coefficients[0]
    betas = coefficients[1]
    alpha_standard_errors = np.empty(number_of_assets)
    alpha_t_statistics = np.empty(number_of_assets)
    bandwidths = np.empty(number_of_assets, dtype=int)

    for asset_index in range(number_of_assets):
        residual = residuals[:, asset_index]
        bandwidth = automatic_bandwidth(residual)[-1]
        score = design * residual[:, None]
        score_covariance = hac_matrix(score, bandwidth)
        coefficient_covariance = (
            q_inverse @ score_covariance @ q_inverse / sample_size
        )
        coefficient_covariance = (
            coefficient_covariance + coefficient_covariance.T
        ) / 2.0
        alpha_variance = float(coefficient_covariance[0, 0])
        if alpha_variance <= 0.0:
            raise ArithmeticError(
                f"Asset {asset_index + 1} has a nonpositive alpha variance"
            )
        alpha_standard_errors[asset_index] = sqrt(alpha_variance)
        alpha_t_statistics[asset_index] = (
            alphas[asset_index] / alpha_standard_errors[asset_index]
        )
        bandwidths[asset_index] = bandwidth

    return (
        alphas,
        betas,
        residuals,
        alpha_standard_errors,
        alpha_t_statistics,
        bandwidths,
    )


def calculate_grs_test(
    factor: np.ndarray,
    alphas: np.ndarray,
    residuals: np.ndarray,
) -> tuple[float, int, int, mp.mpf]:
    """Calculate the standard one-factor GRS F test using divisor T."""
    sample_size, number_of_assets = residuals.shape
    if number_of_assets != EXPECTED_ASSETS:
        raise ValueError(
            f"Expected {EXPECTED_ASSETS} residual series; found {number_of_assets}"
        )

    denominator_degrees_of_freedom = (
        sample_size - number_of_assets - NUMBER_OF_FACTORS
    )
    if denominator_degrees_of_freedom <= 0:
        raise ValueError("The Q4(d) sample is too short for the GRS test")

    residual_covariance = residuals.T @ residuals / sample_size
    residual_covariance = (
        residual_covariance + residual_covariance.T
    ) / 2.0
    if np.linalg.eigvalsh(residual_covariance).min() <= 0.0:
        raise ArithmeticError("The GRS residual covariance is not positive definite")

    centered_factor = factor - factor.mean()
    factor_variance = float(centered_factor @ centered_factor / sample_size)
    if factor_variance <= 0.0:
        raise ArithmeticError("The GRS factor variance is not positive")

    alpha_quadratic = float(
        alphas @ np.linalg.solve(residual_covariance, alphas)
    )
    factor_adjustment = 1.0 + float(factor.mean()) ** 2 / factor_variance
    statistic = (
        denominator_degrees_of_freedom
        / number_of_assets
        * alpha_quadratic
        / factor_adjustment
    )
    with mp.workdps(100):
        numerator_df = mp.mpf(number_of_assets)
        denominator_df = mp.mpf(denominator_degrees_of_freedom)
        f_statistic = mp.mpf(str(statistic))
        transformed_statistic = denominator_df / (
            denominator_df + numerator_df * f_statistic
        )
        p_value = mp.betainc(
            denominator_df / 2,
            numerator_df / 2,
            0,
            transformed_statistic,
            regularized=True,
        )
    return (
        statistic,
        number_of_assets,
        denominator_degrees_of_freedom,
        p_value,
    )


def write_results_table(
    path: Path,
    lambda_hat: float,
    lambda_standard_error: float,
    lambda_bandwidth: int,
    sample_size: int,
    grs_statistic: float,
    grs_df_numerator: int,
    grs_df_denominator: int,
    grs_p_value: mp.mpf,
    asset_names: list[str],
    alphas: np.ndarray,
    alpha_t_statistics: np.ndarray,
) -> None:
    """Write the requested Q4(d) lambda, alpha, and GRS results."""
    if grs_p_value < mp.mpf("1e-4"):
        exponent = int(mp.floor(mp.log10(grs_p_value)))
        mantissa = float(grs_p_value / mp.power(10, exponent))
        p_value_text = (
            f"${mantissa:.3f}\\times 10^{{{exponent}}}$"
        )
    else:
        p_value_text = f"{float(grs_p_value):.6f}"
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\small",
        r"\caption{Time-series beta-pricing results for the linear SDF model.}",
        r"\label{tab:q4d-results}",
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
        (
            f"GRS $F({grs_df_numerator},{grs_df_denominator})$ & "
            f"{grs_statistic:.6f} & \\\\"
        ),
        f"GRS $p$-value & {p_value_text} & \\\\",
        r"\hline",
        r"\multicolumn{3}{l}{\textit{Time-series regression alphas}} \\",
        r"\hline",
    ]
    for name, alpha, t_statistic in zip(
        asset_names, alphas, alpha_t_statistics, strict=True
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

    (
        lambda_hat,
        lambda_standard_error,
        lambda_bandwidth,
        _,
    ) = estimate_mean_newey_west(wealth_return)
    (
        alphas,
        betas,
        residuals,
        alpha_standard_errors,
        alpha_t_statistics,
        alpha_bandwidths,
    ) = estimate_factor_regressions(wealth_return, excess_returns)
    (
        grs_statistic,
        grs_df_numerator,
        grs_df_denominator,
        grs_p_value,
    ) = calculate_grs_test(wealth_return, alphas, residuals)

    write_results_table(
        RESULTS_FILE,
        lambda_hat,
        lambda_standard_error,
        lambda_bandwidth,
        len(sample),
        grs_statistic,
        grs_df_numerator,
        grs_df_denominator,
        grs_p_value,
        asset_names,
        alphas,
        alpha_t_statistics,
    )

    diagnostics = pd.DataFrame(
        {
            "portfolio": asset_names,
            "alpha_hat": alphas,
            "beta_hat": betas,
            "alpha_standard_error": alpha_standard_errors,
            "alpha_t_statistic": alpha_t_statistics,
            "bandwidth": alpha_bandwidths,
        }
    )
    normal_equation_error = float(
        np.max(
            np.abs(
                np.column_stack(
                    [np.ones(len(sample)), wealth_return]
                ).T
                @ residuals
            )
        )
    )
    print(
        f"Sample: {sample.index.min()} to {sample.index.max()} "
        f"({len(sample)} observations)"
    )
    print(f"lambda_hat: {lambda_hat:.12f}")
    print(f"lambda Newey-West standard error: {lambda_standard_error:.12f}")
    print(f"lambda bandwidth: {lambda_bandwidth}")
    print(f"maximum OLS normal-equation error: {normal_equation_error:.3e}")
    print(f"GRS statistic: {grs_statistic:.12f}")
    print(f"GRS numerator df: {grs_df_numerator}")
    print(f"GRS denominator df: {grs_df_denominator}")
    print(
        "GRS p-value: "
        f"{mp.nstr(grs_p_value, 13, min_fixed=0, max_fixed=0)}"
    )
    print(diagnostics.to_string(index=False, float_format=lambda value: f"{value:.12g}"))
    print(f"Wrote {RESULTS_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
