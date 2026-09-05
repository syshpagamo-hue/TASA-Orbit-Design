from __future__ import annotations

import math

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import newton

from .models import CartesianState, KeplerianElements, SpacecraftConfig


Array = np.ndarray


def rotation_1(angle_rad: float) -> Array:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def rotation_3(angle_rad: float) -> Array:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def keplerian_to_cartesian(elements: KeplerianElements, mu_km3_s2: float) -> Array:
    """Convert osculating Keplerian elements to an inertial Cartesian state."""
    a = elements.sma_km
    e = elements.ecc
    inc = math.radians(elements.inc_deg)
    raan = math.radians(elements.raan_deg)
    aop = math.radians(elements.aop_deg)
    ta = math.radians(elements.ta_deg)
    p = a * (1.0 - e * e)
    radius = p / (1.0 + e * math.cos(ta))
    r_pf = np.array([radius * math.cos(ta), radius * math.sin(ta), 0.0])
    velocity_scale = math.sqrt(mu_km3_s2 / p)
    v_pf = velocity_scale * np.array([-math.sin(ta), e + math.cos(ta), 0.0])
    transform = rotation_3(raan) @ rotation_1(inc) @ rotation_3(aop)
    return np.concatenate((transform @ r_pf, transform @ v_pf))


def spacecraft_state(spacecraft: SpacecraftConfig, mu_km3_s2: float) -> Array:
    if spacecraft.keplerian is not None:
        return keplerian_to_cartesian(spacecraft.keplerian, mu_km3_s2)
    assert spacecraft.cartesian is not None
    return np.array(
        [*spacecraft.cartesian.position_km, *spacecraft.cartesian.velocity_km_s],
        dtype=float,
    )


def _stumpff_c(z: float) -> float:
    if z > 1e-8:
        root = math.sqrt(z)
        return (1.0 - math.cos(root)) / z
    if z < -1e-8:
        root = math.sqrt(-z)
        return (math.cosh(root) - 1.0) / (-z)
    return 0.5 - z / 24.0 + z * z / 720.0 - z**3 / 40320.0


def _stumpff_s(z: float) -> float:
    if z > 1e-8:
        root = math.sqrt(z)
        return (root - math.sin(root)) / (root**3)
    if z < -1e-8:
        root = math.sqrt(-z)
        return (math.sinh(root) - root) / (root**3)
    return 1.0 / 6.0 - z / 120.0 + z * z / 5040.0 - z**3 / 362880.0


def _propagate_universal_core(
    state: Array,
    dt_s: float,
    mu_km3_s2: float,
    *,
    tolerance: float = 1e-11,
    max_iterations: int = 50,
) -> Array:
    """Two-body propagation with universal variables for elliptic/hyperbolic arcs."""
    state = np.asarray(state, dtype=float)
    if abs(dt_s) <= 1e-14:
        return state.copy()
    r0_vec = state[:3]
    v0_vec = state[3:]
    r0 = float(np.linalg.norm(r0_vec))
    v0_sq = float(v0_vec @ v0_vec)
    radial_velocity = float(r0_vec @ v0_vec) / r0
    sqrt_mu = math.sqrt(mu_km3_s2)
    alpha = 2.0 / r0 - v0_sq / mu_km3_s2
    if alpha < -1e-10:
        direction = math.copysign(1.0, dt_s)
        denominator = (
            r0 * radial_velocity
            + direction
            * math.sqrt(-mu_km3_s2 / alpha)
            * (1.0 - r0 * alpha)
        )
        argument = -2.0 * mu_km3_s2 * alpha * dt_s / denominator
        chi = direction * math.sqrt(-1.0 / alpha) * math.log(max(argument, 1e-12))
    elif abs(alpha) > 1e-10:
        chi = math.copysign(sqrt_mu * abs(alpha) * abs(dt_s), dt_s)
    else:
        chi = math.copysign(sqrt_mu * abs(dt_s) / r0, dt_s)

    for _ in range(max_iterations):
        z = alpha * chi * chi
        c = _stumpff_c(z)
        s = _stumpff_s(z)
        residual = (
            r0 * radial_velocity / sqrt_mu * chi * chi * c
            + (1.0 - alpha * r0) * chi**3 * s
            + r0 * chi
            - sqrt_mu * dt_s
        )
        derivative = (
            r0 * radial_velocity / sqrt_mu * chi * (1.0 - z * s)
            + (1.0 - alpha * r0) * chi * chi * c
            + r0
        )
        step = residual / derivative
        chi -= step
        if abs(step) <= tolerance * max(1.0, abs(chi)):
            break
    else:
        def equation(value: float) -> float:
            z_local = alpha * value * value
            return (
                r0 * radial_velocity / sqrt_mu * value * value * _stumpff_c(z_local)
                + (1.0 - alpha * r0) * value**3 * _stumpff_s(z_local)
                + r0 * value
                - sqrt_mu * dt_s
            )

        chi = float(newton(equation, chi, maxiter=max_iterations * 2, tol=tolerance))

    z = alpha * chi * chi
    c = _stumpff_c(z)
    s = _stumpff_s(z)
    f = 1.0 - chi * chi / r0 * c
    g = dt_s - chi**3 / sqrt_mu * s
    r_vec = f * r0_vec + g * v0_vec
    radius = float(np.linalg.norm(r_vec))
    fdot = sqrt_mu / (radius * r0) * (alpha * chi**3 * s - chi)
    gdot = 1.0 - chi * chi / radius * c
    v_vec = fdot * r0_vec + gdot * v0_vec
    return np.concatenate((r_vec, v_vec))


def _propagate_numerically(state: Array, dt_s: float, mu_km3_s2: float) -> Array:
    def dynamics(_time: float, value: Array) -> Array:
        radius = float(np.linalg.norm(value[:3]))
        return np.concatenate((value[3:], -mu_km3_s2 * value[:3] / radius**3))

    result = solve_ivp(
        dynamics,
        (0.0, dt_s),
        np.asarray(state, dtype=float),
        method="DOP853",
        rtol=2e-11,
        atol=2e-13,
    )
    if not result.success or not np.all(np.isfinite(result.y[:, -1])):
        raise RuntimeError(f"two-body propagation failed: {result.message}")
    return result.y[:, -1]


def propagate_universal(
    state: Array,
    dt_s: float,
    mu_km3_s2: float,
    *,
    tolerance: float = 1e-11,
    max_iterations: int = 50,
) -> Array:
    """Propagate a two-body state, with a numerical fallback for extreme arcs."""
    try:
        result = _propagate_universal_core(
            state,
            dt_s,
            mu_km3_s2,
            tolerance=tolerance,
            max_iterations=max_iterations,
        )
        if not np.all(np.isfinite(result)) or np.linalg.norm(result[:3]) <= 1e-9:
            raise FloatingPointError("non-finite universal-variable result")
        return result
    except (ArithmeticError, FloatingPointError, OverflowError, RuntimeError, ValueError):
        return _propagate_numerically(state, dt_s, mu_km3_s2)


def vnb_basis(state: Array) -> Array:
    """Return columns [V, N, B] using the GMAT VNB convention."""
    r = np.asarray(state[:3], dtype=float)
    v = np.asarray(state[3:], dtype=float)
    v_hat = v / np.linalg.norm(v)
    n = np.cross(r, v)
    n_hat = n / np.linalg.norm(n)
    b_hat = np.cross(v_hat, n_hat)
    b_hat /= np.linalg.norm(b_hat)
    return np.column_stack((v_hat, n_hat, b_hat))


def apply_vnb_burn(state: Array, dv_vnb_km_s: tuple[float, float, float] | Array) -> Array:
    updated = np.asarray(state, dtype=float).copy()
    updated[3:] += vnb_basis(updated) @ np.asarray(dv_vnb_km_s, dtype=float)
    return updated


def specific_energy(state: Array, mu_km3_s2: float) -> float:
    return 0.5 * float(state[3:] @ state[3:]) - mu_km3_s2 / float(np.linalg.norm(state[:3]))


def angular_momentum(state: Array) -> Array:
    return np.cross(state[:3], state[3:])
