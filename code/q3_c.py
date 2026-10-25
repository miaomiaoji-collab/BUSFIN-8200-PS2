"""Estimate the identity-weighted nonlinear SDF model for Q3(c).

The script reconstructs the restricted expanding-window forecasts from PS1
Q2(e), forms the managed-portfolio moment, estimates gamma by identity-weighted
GMM, and reports Newey-West inference for gamma and the pricing errors.
"""

from __future__ import annotations

from math import erfc, sqrt
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = REPO_ROOT / "data" / "EQ Dataset.csv"
RESULTS_DIR = REPO_ROOT / "results"
LATEX_FILE = RESULTS_DIR / "q3c_results.tex"

MONTHS_PER_YEAR = 12
OOS_START = pd.Timestamp("1940-12-01")
OOS_END = pd.Timestamp("2021-12-01")
EXPECTED_OBSERVATIONS = 973


def load_data(path: Path) -> pd.DataFrame:
    """Load and validate the equity data used in PS1 and PS2."""
    data = pd.read_csv(path)
    required = ["YEAR", "MONTH", "dp", "dg", "rf", "re"]
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    values = data[required].copy()
    for column in required:
        values[column] = pd.to_numeric(values[column], errors="raise")
    if values.isna().any().any():
        raise ValueError("Required data columns contain missing values")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("Required data columns contain non-finite values")

    values["date"] = pd.to_datetime(
        {
            "year": values["YEAR"].astype(int),
            "month": values["MONTH"].astype(int),
            "day": 1,
        },
        errors="raise",
    )
    if values["date"].duplicated().any():
        raise ValueError("The data contain duplicate months")
    values = values.sort_values("date", kind="stable").reset_index(drop=True)
    observed = pd.PeriodIndex(values["date"], freq="M")
    expected = pd.period_range(observed.min(), observed.max(), freq="M")
    if not observed.equals(expected):
        raise ValueError("The observations are not complete consecutive months")

    values["rw"] = np.exp(values["re"])
    values["rf_level"] = np.exp(values["rf"])
    values["x_rw"] = values["rw"] - values["rf_level"]
    values["dividend_price"] = np.exp(values["dp"])
    values["dividend_growth_level"] = np.exp(values["dg"])
    constructed = [
        "rw",
        "rf_level",
        "x_rw",
        "dividend_price",
        "dividend_growth_level",
    ]
    if not np.isfinite(values[constructed].to_numpy()).all():
        raise ValueError("A constructed return or predictor is non-finite")
    return values


def reconstruct_ps1_forecasts(data: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the restricted expanding-window forecasts from PS1 Q2(e)."""
    pairs = pd.DataFrame(
        {
            "predictor_date": data["date"],
            "forecast_date": data["date"].shift(-MONTHS_PER_YEAR),
            "predictor": data["dividend_price"],
            "realized_excess_return": data["x_rw"].shift(-MONTHS_PER_YEAR),
            "realized_dividend_growth": data["dividend_growth_level"].shift(
                -MONTHS_PER_YEAR
            ),
        }
    ).dropna()
    if len(pairs) != len(data) - MONTHS_PER_YEAR:
        raise RuntimeError("The predictive-pair construction lost observations")

    oos_pairs = pairs.loc[
        pairs["forecast_date"].between(OOS_START, OOS_END)
    ].copy()
    rows: list[dict[str, float | int | pd.Timestamp]] = []
    for observation in oos_pairs.itertuples(index=False):
        training = pairs.loc[
            pairs["forecast_date"] <= observation.predictor_date
        ]
        if len(training) <= 2:
            raise ValueError("An expanding-window sample has too few observations")
        growth_mean = float(training["realized_dividend_growth"].mean())
        restricted_forecast = (
            growth_mean - 1.0 + growth_mean * observation.predictor
        )
        rows.append(
            {
                "forecast_date": observation.forecast_date,
                "predictor_date": observation.predictor_date,
                "realized_excess_return": observation.realized_excess_return,
                "restricted_oos_forecast": restricted_forecast,
                "G_t": growth_mean,
                "training_nobs": len(training),
            }
        )

    forecasts = pd.DataFrame(rows)
    if len(forecasts) != EXPECTED_OBSERVATIONS:
        raise ValueError(
            f"Expected {EXPECTED_OBSERVATIONS} forecasts; found {len(forecasts)}"
        )
    if (
        forecasts["forecast_date"].iloc[0] != OOS_START
        or forecasts["forecast_date"].iloc[-1] != OOS_END
    ):
        raise ValueError("The forecast sample has unexpected endpoints")
    return forecasts


def prepare_gmm_sample(
    data: pd.DataFrame, forecasts: pd.DataFrame
) -> tuple[pd.DataFrame, float]:
    """Align forecasts and returns and construct the trading instrument."""
    full_sample_variance = float(data["rw"].var(ddof=0))
    if full_sample_variance <= 0.0:
        raise ValueError("The full-sample wealth-return variance is not positive")

    sample = forecasts.merge(
        data[["date", "re", "rw", "x_rw"]],
        left_on="forecast_date",
        right_on="date",
        how="inner",
        validate="one_to_one",
    )
    if len(sample) != EXPECTED_OBSERVATIONS:
        raise ValueError("Forecast-return alignment changed the sample size")
    if not np.allclose(
        sample["realized_excess_return"],
        sample["x_rw"],
        rtol=0.0,
        atol=1e-14,
    ):
        raise ArithmeticError("PS1 realized returns do not match the PS2 returns")

    sample["z_lag"] = sample["restricted_oos_forecast"] / full_sample_variance
    sample["managed_excess_return"] = sample["z_lag"] * sample["x_rw"]
    if not np.isfinite(
        sample[["z_lag", "managed_excess_return"]].to_numpy()
    ).all():
        raise ArithmeticError("The instrument or managed return is non-finite")
    return sample, full_sample_variance


def moment_matrix(
    gamma: float, log_rw: np.ndarray, x_rw: np.ndarray, z_lag: np.ndarray
) -> np.ndarray:
    """Return the T by 2 matrix of observation-level moments."""
    base_moment = np.exp(-gamma * log_rw) * x_rw
    return np.column_stack([base_moment, base_moment * z_lag])


def moment_mean_and_derivative(
    gamma: float, log_rw: np.ndarray, x_rw: np.ndarray, z_lag: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Return the mean moment and its 2 by 1 derivative."""
    moments = moment_matrix(gamma, log_rw, x_rw, z_lag)
    mean_moment = moments.mean(axis=0)
    derivative = (-log_rw[:, None] * moments).mean(axis=0)[:, None]
    return mean_moment, derivative


def solve_identity_gmm(
    log_rw: np.ndarray, x_rw: np.ndarray, z_lag: np.ndarray
) -> float:
    """Find the unconstrained stationary point of the identity-GMM objective."""
    def first_order_condition(gamma: float) -> float:
        mean_moment, derivative = moment_mean_and_derivative(
            gamma, log_rw, x_rw, z_lag
        )
        return float(derivative[:, 0] @ mean_moment)

    lower, upper = -1.0, 1.0
    f_lower = first_order_condition(lower)
    f_upper = first_order_condition(upper)
    for _ in range(20):
        if not np.isfinite([f_lower, f_upper]).all():
            raise ArithmeticError("The GMM derivative became non-finite")
        if np.signbit(f_lower) != np.signbit(f_upper):
            break
        lower *= 2.0
        upper *= 2.0
        f_lower = first_order_condition(lower)
        f_upper = first_order_condition(upper)
    else:
        raise ArithmeticError("Could not bracket a stationary point for gamma")

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
        raise ArithmeticError("The identity-GMM bisection did not converge")

    objective = lambda value: float(
        moment_mean_and_derivative(value, log_rw, x_rw, z_lag)[0]
        @ moment_mean_and_derivative(value, log_rw, x_rw, z_lag)[0]
    )
    step = 1e-4 * max(1.0, abs(gamma_hat))
    if not (
        objective(gamma_hat) < objective(gamma_hat - step)
        and objective(gamma_hat) < objective(gamma_hat + step)
    ):
        raise ArithmeticError("The stationary point is not a local GMM minimum")
    return float(gamma_hat)


def scalar_autocovariance(series: np.ndarray, lag: int) -> float:
    """Compute a scalar lag-l autocovariance using divisor T-l."""
    sample_size = len(series)
    if lag == 0:
        return float(series @ series / sample_size)
    return float(series[lag:] @ series[:-lag] / (sample_size - lag))


def automatic_bandwidth(first_moment: np.ndarray) -> tuple[int, float, float, float, float, int]:
    """Apply the Q3(b) NW94 bandwidth calculation to the first moment."""
    centered = first_moment - first_moment.mean()
    sample_size = len(centered)
    pilot_lag = int(np.floor(4.0 * (sample_size / 100.0) ** (2.0 / 9.0)))
    pilot = np.array(
        [scalar_autocovariance(centered, lag) for lag in range(pilot_lag + 1)]
    )
    s0_hat = float(pilot[0] + 2.0 * pilot[1:].sum())
    s1_hat = float(
        2.0 * np.arange(1, pilot_lag + 1, dtype=float) @ pilot[1:]
    )
    if np.isclose(s0_hat, 0.0):
        raise ArithmeticError("The Q3(c) pilot s0 estimate is numerically zero")
    const_hat = float(1.1447 * ((s1_hat / s0_hat) ** 2) ** (1.0 / 3.0))
    bandwidth_unrounded = float(const_hat * sample_size ** (1.0 / 3.0))
    bandwidth = int(np.floor(bandwidth_unrounded))
    if bandwidth >= sample_size:
        raise ArithmeticError("The selected bandwidth is at least T")
    return (
        pilot_lag,
        s0_hat,
        s1_hat,
        const_hat,
        bandwidth_unrounded,
        bandwidth,
    )


def hac_matrix(moments: np.ndarray, bandwidth: int) -> np.ndarray:
    """Estimate the 2 by 2 Bartlett-kernel long-run covariance matrix."""
    centered = moments - moments.mean(axis=0)
    sample_size = len(centered)
    covariance = centered.T @ centered / sample_size
    for lag in range(1, bandwidth + 1):
        gamma_l = centered[lag:].T @ centered[:-lag] / (sample_size - lag)
        weight = 1.0 - lag / (bandwidth + 1.0)
        covariance += weight * (gamma_l + gamma_l.T)
    covariance = (covariance + covariance.T) / 2.0
    if np.linalg.eigvalsh(covariance).min() <= 0.0:
        raise ArithmeticError("The estimated HAC covariance matrix is not positive definite")
    return covariance


def calculate_inference(
    moments: np.ndarray, derivative: np.ndarray, covariance: np.ndarray
) -> tuple[float, np.ndarray, np.ndarray, float, float]:
    """Calculate gamma and pricing-error inference with estimation adjustment."""
    sample_size = len(moments)
    dtd = float((derivative.T @ derivative).item())
    if np.isclose(dtd, 0.0):
        raise ArithmeticError("The GMM derivative has zero rank")

    gamma_variance = float(
        (derivative.T @ covariance @ derivative).item()
        / (sample_size * dtd**2)
    )
    gamma_standard_error = sqrt(gamma_variance)

    projection = np.eye(2) - derivative @ derivative.T / dtd
    pricing_error_covariance = (
        projection @ covariance @ projection.T / sample_size
    )
    pricing_error_covariance = (
        pricing_error_covariance + pricing_error_covariance.T
    ) / 2.0
    standard_errors = np.sqrt(np.maximum(np.diag(pricing_error_covariance), 0.0))
    if np.any(np.isclose(standard_errors, 0.0)):
        raise ArithmeticError("A pricing-error standard error is numerically zero")

    pricing_errors = moments.mean(axis=0)
    t_statistics = pricing_errors / standard_errors
    joint_statistic = float(
        pricing_errors
        @ np.linalg.pinv(pricing_error_covariance, hermitian=True)
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
    """Write the requested Q3(c) results as a dependency-free LaTeX table."""
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Identity-weighted GMM estimates and pricing errors for Q3(c).}",
        r"\label{tab:q3c-results}",
        r"\begin{tabular}{lrrr}",
        r"\hline",
        r"Quantity & Estimate & Standard error & $t$-statistic \\",
        r"\hline",
        f"$\\widehat{{\\gamma}}$ & {gamma_hat:.6f} & {gamma_standard_error:.6f} & \\\\",
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
        f"Observations & {sample_size} & & \\\\",
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
    gamma_hat = solve_identity_gmm(log_rw, x_rw, z_lag)
    moments = moment_matrix(gamma_hat, log_rw, x_rw, z_lag)
    pricing_errors, derivative = moment_mean_and_derivative(
        gamma_hat, log_rw, x_rw, z_lag
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
    print(f"gamma_hat: {gamma_hat:.12f}")
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
    print(f"Wrote {LATEX_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
