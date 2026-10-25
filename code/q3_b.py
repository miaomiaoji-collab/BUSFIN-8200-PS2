"""Estimate risk aversion in Q3(b) and its Newey-West standard error.

The script solves the exactly identified Euler-equation moment

    E[Rw**(-gamma) * (Rw - Rf)] = 0,

then estimates the moment's long-run variance using the automatic Bartlett
bandwidth procedure specified in ``spec/q3.md``.  No prewhitening or
recoloring is applied.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = REPO_ROOT / "data" / "EQ Dataset.csv"


def load_returns(path: Path) -> pd.DataFrame:
    """Load and validate the monthly observations of annual log returns."""
    data = pd.read_csv(path)
    required = ["YEAR", "MONTH", "re", "rf"]
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    values = data[required].copy()
    if values.isna().any().any():
        raise ValueError("The required return and date columns contain missing values")
    if not np.isfinite(values[["re", "rf"]].to_numpy(dtype=float)).all():
        raise ValueError("The return columns contain non-finite values")

    periods = pd.PeriodIndex.from_fields(
        year=values["YEAR"].astype(int),
        month=values["MONTH"].astype(int),
        freq="M",
    )
    if periods.has_duplicates:
        raise ValueError("The data contain duplicate months")
    expected = pd.period_range(periods.min(), periods.max(), freq="M")
    if not periods.equals(expected):
        raise ValueError("The observations are not complete consecutive months")

    values.index = periods
    return values


def sample_moment(gamma: float, log_rw: np.ndarray, x_rw: np.ndarray) -> float:
    """Return the sample mean of the Euler-equation moment at gamma."""
    return float(np.mean(np.exp(-gamma * log_rw) * x_rw))


def solve_gamma(log_rw: np.ndarray, x_rw: np.ndarray) -> float:
    """Solve the scalar moment equation using bracket expansion and bisection."""
    lower, upper = -1.0, 1.0
    f_lower = sample_moment(lower, log_rw, x_rw)
    f_upper = sample_moment(upper, log_rw, x_rw)

    for _ in range(20):
        if not np.isfinite([f_lower, f_upper]).all():
            raise ArithmeticError("The moment became non-finite while bracketing gamma")
        if f_lower == 0.0:
            return lower
        if f_upper == 0.0:
            return upper
        if np.signbit(f_lower) != np.signbit(f_upper):
            break
        lower *= 2.0
        upper *= 2.0
        f_lower = sample_moment(lower, log_rw, x_rw)
        f_upper = sample_moment(upper, log_rw, x_rw)
    else:
        raise ArithmeticError("Could not bracket a root of the sample moment")

    for _ in range(200):
        midpoint = (lower + upper) / 2.0
        f_midpoint = sample_moment(midpoint, log_rw, x_rw)
        if not np.isfinite(f_midpoint):
            raise ArithmeticError("The moment became non-finite while solving for gamma")
        if abs(f_midpoint) <= 1e-14:
            return midpoint
        if np.signbit(f_lower) != np.signbit(f_midpoint):
            upper = midpoint
        else:
            lower = midpoint
            f_lower = f_midpoint
        if upper - lower <= 1e-13 * max(1.0, abs(midpoint)):
            return (lower + upper) / 2.0

    raise ArithmeticError("Bisection did not converge")


def autocovariance(series: np.ndarray, lag: int) -> float:
    """Compute the lag-l autocovariance using the specified T-l divisor."""
    sample_size = len(series)
    if lag < 0 or lag >= sample_size:
        raise ValueError("The autocovariance lag must be between 0 and T - 1")
    if lag == 0:
        return float(series @ series / sample_size)
    return float(series[lag:] @ series[:-lag] / (sample_size - lag))


def newey_west_long_run_variance(
    moments: np.ndarray,
) -> tuple[float, int, float, float, float, float, int]:
    """Estimate S and return it together with all bandwidth diagnostics."""
    centered = moments - moments.mean()
    sample_size = len(centered)
    pilot_lag = int(np.floor(4.0 * (sample_size / 100.0) ** (2.0 / 9.0)))
    pilot_autocovariances = np.array(
        [autocovariance(centered, lag) for lag in range(pilot_lag + 1)]
    )

    s0_hat = float(
        pilot_autocovariances[0] + 2.0 * pilot_autocovariances[1:].sum()
    )
    s1_hat = float(
        2.0
        * np.arange(1, pilot_lag + 1, dtype=float)
        @ pilot_autocovariances[1:]
    )
    if np.isclose(s0_hat, 0.0):
        raise ArithmeticError("The pilot s0 estimate is numerically zero")

    const_hat = float(1.1447 * ((s1_hat / s0_hat) ** 2) ** (1.0 / 3.0))
    bandwidth_unrounded = float(const_hat * sample_size ** (1.0 / 3.0))
    bandwidth = int(np.floor(bandwidth_unrounded))
    if bandwidth >= sample_size:
        raise ArithmeticError("The selected Newey-West bandwidth is at least T")

    final_autocovariances = np.array(
        [autocovariance(centered, lag) for lag in range(bandwidth + 1)]
    )
    if bandwidth == 0:
        long_run_variance = float(final_autocovariances[0])
    else:
        lags = np.arange(1, bandwidth + 1, dtype=float)
        bartlett_weights = 1.0 - lags / (bandwidth + 1.0)
        long_run_variance = float(
            final_autocovariances[0]
            + 2.0 * bartlett_weights @ final_autocovariances[1:]
        )
    if long_run_variance <= 0.0:
        raise ArithmeticError("The estimated long-run variance is not positive")

    return (
        long_run_variance,
        pilot_lag,
        s0_hat,
        s1_hat,
        const_hat,
        bandwidth_unrounded,
        bandwidth,
    )


def main() -> None:
    data = load_returns(DATA_FILE)
    log_rw = data["re"].to_numpy(dtype=float)
    rw = np.exp(log_rw)
    rf = np.exp(data["rf"].to_numpy(dtype=float))
    x_rw = rw - rf

    gamma_hat = solve_gamma(log_rw, x_rw)
    moments = np.exp(-gamma_hat * log_rw) * x_rw
    moment_derivative = float(np.mean(-log_rw * moments))
    if np.isclose(moment_derivative, 0.0):
        raise ArithmeticError("The sample moment derivative is numerically zero")

    (
        s_hat,
        pilot_lag,
        s0_hat,
        s1_hat,
        const_hat,
        bandwidth_unrounded,
        bandwidth,
    ) = newey_west_long_run_variance(moments)
    standard_error = float(
        np.sqrt(s_hat / (len(data) * moment_derivative**2))
    )

    print(
        f"Sample: {data.index.min()} to {data.index.max()} "
        f"({len(data)} monthly observations of annual returns)"
    )
    print(f"gamma_hat: {gamma_hat:.12f}")
    print(f"Newey-West standard error: {standard_error:.12f}")
    print(f"sample moment at gamma_hat: {moments.mean():.3e}")
    print(f"q: {pilot_lag}")
    print(f"s0_hat: {s0_hat:.12f}")
    print(f"s1_hat: {s1_hat:.12f}")
    print(f"const_hat: {const_hat:.12f}")
    print(f"bandwidth_unrounded: {bandwidth_unrounded:.12f}")
    print(f"L: {bandwidth}")
    print(f"S_hat: {s_hat:.12f}")


if __name__ == "__main__":
    main()
