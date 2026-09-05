from __future__ import annotations

import math

import numpy as np
import pytest

from tasa_v4.astrodynamics import (
    angular_momentum,
    keplerian_to_cartesian,
    propagate_universal,
    specific_energy,
    vnb_basis,
)
from tasa_v4.models import KeplerianElements


def test_circular_orbit_closes_and_invariants_hold():
    mu = 398600.4415
    elements = KeplerianElements(
        sma_km=7000,
        ecc=0,
        inc_deg=37,
        raan_deg=21,
        aop_deg=0,
        ta_deg=83,
    )
    initial = keplerian_to_cartesian(elements, mu)
    period = 2 * math.pi * math.sqrt(elements.sma_km**3 / mu)
    final = propagate_universal(initial, period, mu)
    assert np.linalg.norm(final[:3] - initial[:3]) < 1e-6
    assert np.linalg.norm(final[3:] - initial[3:]) < 1e-9
    assert specific_energy(final, mu) == pytest.approx(specific_energy(initial, mu), abs=1e-11)
    assert np.linalg.norm(angular_momentum(final) - angular_momentum(initial)) < 1e-8


def test_vnb_is_right_handed_and_gmat_convention():
    mu = 398600.4415
    state = keplerian_to_cartesian(
        KeplerianElements(
            sma_km=7000,
            ecc=0,
            inc_deg=0,
            raan_deg=0,
            aop_deg=0,
            ta_deg=0,
        ),
        mu,
    )
    basis = vnb_basis(state)
    assert np.allclose(basis.T @ basis, np.eye(3), atol=1e-13)
    assert np.allclose(np.cross(basis[:, 0], basis[:, 1]), basis[:, 2])
    assert basis[:, 2] @ state[:3] > 0  # B points outward for a circular orbit.
