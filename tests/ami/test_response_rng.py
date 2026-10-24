"""Directed software checks of the public response RNG seam; no model instances."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pyibisami.ami import model

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/issue27-response-rng-20261024/fixtures.json").read_text(
        encoding="utf-8"
    )
)


class FakeResponseSelf:
    """Plain memory fields and a wave boundary returning two fixed patterns."""

    def __init__(self, ignore_bits=8, getwave=True):
        self.bit_time = 2.0
        self.sample_interval = 1.0
        self.row_size = 8
        self.channel_response = FIXTURES["channel"][:]
        self.initOut = FIXTURES["initOut"][:]
        self._info_params = {
            "Init_Returns_Impulse": False,
            "GetWave_Exists": SimpleNamespace(pvalue=getwave),
            "Ignore_Bits": SimpleNamespace(pvalue=ignore_bits),
        }
        self.calls = []

    def getWave(self, wave, bits_per_call=0):
        """Record the actual input and return a repeated literal, not a model."""
        self.calls.append((np.array(wave, copy=True), bits_per_call))
        pattern = FIXTURES["probe_outputs_cycle"][(len(self.calls) - 1) % 2]
        output = np.array((pattern * ((len(wave) + 7) // 8))[: len(wave)], dtype=float)
        return output, [], [f"probe{len(self.calls)}"]


def assert_fixed_result(result):
    """Compare every returned array with the independent worked example."""
    assert set(result) == {model.IMP_RESP_GETW, model.OUT_RESP_GETW}
    oracle = FIXTURES["expected_return"]
    np.testing.assert_array_equal(result[model.IMP_RESP_GETW], oracle["imp_resp_getw"])
    response = result[model.OUT_RESP_GETW]
    assert len(response) == 6
    for actual, name in zip(response[:5], ("t", "h", "s", "p", "f")):
        np.testing.assert_array_equal(actual, oracle["out_resp_getw"][name])
    expected_fft = np.array(oracle["out_resp_getw"]["H_real"]) + 1j * np.array(
        oracle["out_resp_getw"]["H_imag"]
    )
    np.testing.assert_allclose(response[5], expected_fft, rtol=0, atol=1e-14)


@pytest.fixture
def no_default_factory(monkeypatch):
    def unexpected_default_rng():
        raise AssertionError("The supplied generator must be used directly.")

    monkeypatch.setattr(model, "default_rng", unexpected_default_rng)


@pytest.fixture
def default_factory(monkeypatch):
    created = []

    def controlled_default_rng():
        generator = np.random.default_rng(999)
        created.append((generator, copy.deepcopy(generator.bit_generator.state)))
        return generator

    monkeypatch.setattr(model, "default_rng", controlled_default_rng)
    return created


def test_seeded_generators_replay_both_probes_and_complete_returns(no_default_factory):
    first, second = FakeResponseSelf(), FakeResponseSelf()
    for fake in (first, second):
        result = model.AMIModel.get_responses(
            fake, bits_per_call=7, max_run_length=2, nbits=8, rng=np.random.default_rng(123)
        )
        assert_fixed_result(result)
        assert len(fake.calls) == 2
        for wave, block in fake.calls:
            np.testing.assert_array_equal(wave, FIXTURES["probe_input_waves"]["123"][0])
            assert block == 7
        assert fake._getwave_step_response_out_params == ["probe2"]
    for one, two in zip(first.calls, second.calls):
        assert one[0].tobytes() == two[0].tobytes()


def test_different_seed_has_known_different_inputs(no_default_factory):
    fake = FakeResponseSelf()
    result = model.AMIModel.get_responses(
        fake, max_run_length=2, nbits=8, rng=np.random.default_rng(124)
    )
    expected = np.array(FIXTURES["probe_input_waves"]["124"][0])
    assert expected.tobytes() != np.array(FIXTURES["probe_input_waves"]["123"][0]).tobytes()
    for wave, block in fake.calls:
        assert wave.tobytes() == expected.tobytes()
        assert block == 0
    assert_fixed_result(result)


def test_same_generator_advances_once_per_response_request(no_default_factory):
    fake = FakeResponseSelf()
    generator = np.random.default_rng(123)
    for iteration in range(2):
        result = model.AMIModel.get_responses(fake, max_run_length=2, nbits=8, rng=generator)
        assert generator.bit_generator.state == FIXTURES["seeds"]["123"]["states_after_draw"][iteration]
        expected = np.array(FIXTURES["probe_input_waves"]["123"][iteration])
        for wave, _ in fake.calls[2 * iteration : 2 * iteration + 2]:
            assert wave.tobytes() == expected.tobytes()
        assert_fixed_result(result)
    assert generator.integers(0, 2, size=8).tolist() == FIXTURES["seeds"]["123"]["draws"][2]


@pytest.mark.parametrize("explicit_none", [False, True])
def test_omitted_and_none_use_the_default_factory(default_factory, explicit_none):
    fake = FakeResponseSelf()
    kwargs = {"rng": None} if explicit_none else {}
    result = model.AMIModel.get_responses(fake, max_run_length=2, nbits=8, **kwargs)
    assert len(default_factory) == 1
    generator, _ = default_factory[0]
    assert generator.bit_generator.state == FIXTURES["seeds"]["999"]["states_after_draw"][0]
    for wave, _ in fake.calls:
        np.testing.assert_array_equal(wave, FIXTURES["probe_input_waves"]["999"][0])
    assert_fixed_result(result)


@pytest.mark.parametrize("reason", ["calc_disabled", "getwave_absent"])
@pytest.mark.parametrize("mode", ["omitted", "none", "provided"])
def test_unused_getwave_neither_creates_nor_consumes_rng(no_default_factory, reason, mode):
    fake = FakeResponseSelf(getwave=reason != "getwave_absent")
    generator = np.random.default_rng(123)
    before = copy.deepcopy(generator.bit_generator.state)
    kwargs = {} if mode == "omitted" else {"rng": None if mode == "none" else generator}
    result = model.AMIModel.get_responses(
        fake, max_run_length=2, nbits=8, calc_getw=reason != "calc_disabled", **kwargs
    )
    assert result == {} and fake.calls == []
    assert generator.bit_generator.state == before


@pytest.mark.parametrize("mode", ["provided", "default"])
def test_ignore_zero_keeps_empty_prefix_and_generator_state(default_factory, mode):
    fake = FakeResponseSelf(ignore_bits=0)
    generator = np.random.default_rng(123)
    before = copy.deepcopy(generator.bit_generator.state)
    result = model.AMIModel.get_responses(
        fake, max_run_length=2, nbits=8, rng=generator if mode == "provided" else None
    )
    if mode == "provided":
        assert default_factory == []
        assert generator.bit_generator.state == before
    else:
        assert len(default_factory) == 1
        fresh, fresh_before = default_factory[0]
        assert fresh.bit_generator.state == fresh_before
    assert len(fake.calls) == 2
    for wave, _ in fake.calls:
        np.testing.assert_array_equal(wave, FIXTURES["probe_input_wave_ignore0"])
    assert_fixed_result(result)


@pytest.mark.parametrize("mode", ["provided", "default"])
def test_legacy_global_rng_state_is_unchanged(default_factory, mode):
    before = np.random.get_state()
    model.AMIModel.get_responses(
        FakeResponseSelf(), max_run_length=2, nbits=8,
        rng=np.random.default_rng(123) if mode == "provided" else None,
    )
    after = np.random.get_state()
    assert before[0] == after[0] and before[2:] == after[2:]
    np.testing.assert_array_equal(before[1], after[1])


def test_old_five_positional_arguments_keep_return_api(default_factory):
    fake = FakeResponseSelf()
    result = model.AMIModel.get_responses(fake, 7, 2, 8, True, False)
    assert len(default_factory) == 1
    assert len(fake.calls) == 2 and all(block == 7 for _, block in fake.calls)
    assert_fixed_result(result)


def test_rng_is_keyword_only():
    with pytest.raises(TypeError):
        model.AMIModel.get_responses(FakeResponseSelf(), 7, 2, 8, True, False, np.random.default_rng(123))
