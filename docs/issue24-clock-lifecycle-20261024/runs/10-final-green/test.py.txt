"""Exercise production AMI methods through Python callbacks, never a model DLL."""

from collections import Counter
from ctypes import c_double
from types import SimpleNamespace

import numpy as np
import pytest

from pyibisami.ami import model

FAKE_CALLS = Counter()
FAKE_EVENTS = []


class FakeAMI:
    """External ABI boundary only; waveform/clock expectations live in the tests."""

    def __init__(self):
        self.dut = model.AMIModel.__new__(model.AMIModel)
        self.dut._ami_mem_handle = None
        self.dut._filename = "NO_DLL_ALLOWED"
        self.dut._amiInit = self.init
        self.dut._amiGetWave = self.wave
        self.dut._amiClose = self.close
        self.blocks = []
        self.seen = []
        self.closed = []
        self.init_status = 1
        self.init_handle = b"fake-memory"
        self.init_error = None
        self.init_cleanup_error = None
        self.close_error = None
        self.close_owned = []
        self.on_close = None

    def init(self, impulse, rows, aggressors, ts, ui, params, output, handle, message):
        FAKE_CALLS["Init"] += 1
        FAKE_EVENTS.append(("Init", self.init_status))
        handle._obj.value = self.init_handle
        output._obj.value = b"(candidate (initialized 1))"
        message._obj.value = b"fake Init only"
        if self.init_cleanup_error:
            self.close_error = self.init_cleanup_error
        if self.init_error:
            raise self.init_error
        return self.init_status

    def wave(self, signal, size, clocks, output, handle):
        FAKE_CALLS["GetWave"] += 1
        block = self.blocks.pop(0)
        self.seen.append(dict(size=size, clocks=list(clocks._obj), wave=list(signal._obj)))
        FAKE_EVENTS.append(("GetWave", size))
        for i in range(size):
            signal._obj[i] += block.get("offset", 0.)
        for i, value in enumerate(block["clocks"]):
            clocks._obj[i] = value
        output._obj.value = block.get("params", b"(candidate )")
        if "error" in block:
            raise block["error"]
        return block.get("status", 1)

    def close(self, handle):
        FAKE_CALLS["Close"] += 1
        self.closed.append(handle.value)
        self.close_owned.append(bool(self.dut._ami_mem_handle))
        FAKE_EVENTS.append(("Close", handle.value.decode()))
        if self.close_error:
            raise self.close_error
        if self.on_close:
            self.on_close()


def initializer():
    result = model.AMIModelInitializer({"root_name": "candidate"},
                                       bit_time=c_double(1.), sample_interval=c_double(.5))
    result.channel_response = np.array([0., 1., 0., 0., 0., 0., 0., 0.])
    return result


@pytest.fixture
def fake():
    boundary = FakeAMI()
    boundary.dut.initialize(initializer())
    yield boundary
    # These are only Python-owned fake handles. Avoid a GC-only Close contaminating callback counts.
    boundary.dut._ami_mem_handle = None


def test_valid_prefix_keeps_zero_and_all_ticks_with_wave_params_order(fake):
    fake.blocks = [
        dict(clocks=[0., .1, .2, -1., np.nan, -7.], offset=10., params=b"first"),
        dict(clocks=[.3, .4, -1., np.inf], offset=20., params=b"second"),
    ]
    wave, clocks, params = fake.dut.getWave(np.array([1., 2., 3., 4., 5.]), bits_per_call=2)
    assert wave.tolist() == [11., 12., 13., 14., 25.]
    assert clocks.tolist() == [0., .1, .2, .3, .4]
    assert params == ["first", "second"]
    assert [entry["size"] for entry in fake.seen] == [4, 1]


@pytest.mark.parametrize("clocks", [
    [], [0., .1], [-.1, -1.], [np.nan, -1.], [np.inf, -1.],
    [0., 0., -1.], [.2, .1, -1.], [-1.0000000001, -1.],
])
def test_bad_prefix_poison_preserves_host_owned_raw(fake, clocks):
    fake.blocks = [dict(clocks=clocks, offset=3., params=b"failed params")]
    with pytest.raises(RuntimeError) as failure:
        fake.dut.getWave(np.array([1., 2.]))
    diagnostic = failure.value.diagnostic
    assert diagnostic["method"] == "AMI_GetWave"
    assert diagnostic["python_call"] == 1 and diagnostic["c_call"] == 1
    assert diagnostic["wave"] == (4., 5.)
    assert diagnostic["params_raw"] == b"failed params"
    assert len(diagnostic["clock_buffer"]) == 12  # Init inherited block=4 plus historical 8.
    with pytest.raises(RuntimeError):
        fake.dut.getWave(np.array([1., 2.]))
    assert len(fake.seen) == 1


@pytest.mark.parametrize("status", [0, 2, -1])
def test_getwave_status_only_one_succeeds(fake, status):
    fake.blocks = [dict(clocks=[0., -1.], status=status)]
    with pytest.raises(RuntimeError) as failure:
        fake.dut.getWave(np.array([1., 2.]))
    assert failure.value.diagnostic["status"] == status
    with pytest.raises(RuntimeError):
        fake.dut.getWave(np.array([1., 2.]))
    assert len(fake.seen) == 1


@pytest.mark.parametrize("error", [OSError("fake ABI failure"), ValueError("fake callback failure")])
def test_getwave_exception_poison_retains_original_cause(fake, error):
    fake.blocks = [dict(clocks=[0., -1.], params=b"partial", error=error)]
    with pytest.raises(RuntimeError) as failure:
        fake.dut.getWave(np.array([1., 2.]))
    assert failure.value.__cause__ is error
    assert failure.value.diagnostic["returned"] is False
    assert failure.value.diagnostic["params_raw"] == b"partial"
    with pytest.raises(RuntimeError):
        fake.dut.getWave(np.array([1., 2.]))
    assert len(fake.seen) == 1


def test_cross_wrapper_monotonicity_survives_empty_prefix(fake):
    fake.blocks = [dict(clocks=[.2, -1.]), dict(clocks=[-1., np.nan, -8.]), dict(clocks=[.1, -1.])]
    assert fake.dut.getWave(np.array([1., 2.]))[1].tolist() == [.2]
    assert fake.dut.getWave(np.array([3., 4.]))[1].tolist() == []
    with pytest.raises(RuntimeError):
        fake.dut.getWave(np.array([5., 6.]))
    assert len(fake.seen) == 3


def test_duplicate_across_wrappers_is_failure(fake):
    fake.blocks = [dict(clocks=[0., -1.]), dict(clocks=[0., -1.])]
    fake.dut.getWave(np.array([1., 2.]))
    with pytest.raises(RuntimeError):
        fake.dut.getWave(np.array([1., 2.]))


def test_every_call_prefills_nan_and_zero_block_inherits(fake):
    fake.blocks = [dict(clocks=[0., -1., 9., 8.]), dict(clocks=[-1.]), dict(clocks=[.5, -1.])]
    fake.dut.getWave(np.arange(5.), bits_per_call=2)
    assert fake.dut.getWave(np.arange(4.), bits_per_call=0)[1].tolist() == [.5]
    assert [entry["size"] for entry in fake.seen] == [4, 1, 4]
    assert all(all(np.isnan(value) for value in entry["clocks"]) for entry in fake.seen)


def test_failure_does_not_publish_partial_tuple_and_observations_are_copies(fake):
    fake.blocks = [dict(clocks=[0., -1.], offset=2., params=b"done"), dict(clocks=[], params=b"partial")]
    with pytest.raises(RuntimeError) as failure:
        fake.dut.getWave(np.arange(5.), bits_per_call=2)
    records = fake.dut.last_getwave_calls
    assert len(records) == 2 and records[0]["wave"] == (2., 3., 4., 5.)
    assert failure.value.diagnostic["c_call"] == 2
    records[0]["wave"] = ()
    assert fake.dut.last_getwave_calls[0]["wave"] == (2., 3., 4., 5.)


def test_successful_new_init_recovers_poisoned_epoch(fake):
    fake.blocks = [dict(clocks=[1., -1.]), dict(clocks=[.5, -1.]), dict(clocks=[0., -1.])]
    fake.dut.getWave(np.array([1., 2.]))
    with pytest.raises(RuntimeError):
        fake.dut.getWave(np.array([1., 2.]))
    fake.dut.initialize(initializer())
    assert fake.dut.getWave(np.array([1., 2.]))[1].tolist() == [0.]
    assert fake.dut.last_getwave_calls[0]["epoch"] == 2


def test_close_is_idempotent_and_disowns_before_callback(fake):
    fake.on_close = lambda: fake.dut.close()
    fake.dut.close()
    fake.dut.close()
    fake.dut.__del__()
    assert fake.closed == [b"fake-memory"]
    assert fake.close_owned == [False]
    assert fake.dut.last_close_call["returned"] is True
    with pytest.raises(RuntimeError):
        fake.dut.getWave(np.array([1., 2.]))
    assert fake.seen == []


def test_reinit_prepare_failure_then_destructor_never_closes_old_handle_twice(fake):
    with pytest.raises(Exception):
        fake.dut.initialize(object())
    fake.dut.__del__()
    assert fake.closed == [b"fake-memory"]
    assert fake.dut.last_init_call["phase"] == "preparing"
    with pytest.raises(RuntimeError):
        fake.dut.getWave(np.array([1., 2.]))


def test_reinit_capability_failure_retains_original_and_closed_state(fake):
    data = initializer()
    data.info_params = {"GetWave_Exists": SimpleNamespace(pvalue=True)}
    fake.dut._amiGetWave = None
    with pytest.raises(RuntimeError) as failure:
        fake.dut.initialize(data)
    assert "can't bind" in str(failure.value.__cause__)
    fake.dut.__del__()
    assert fake.closed == [b"fake-memory"]


@pytest.mark.parametrize("status", [0, 2, -1])
def test_failed_init_status_cleans_nonnull_handle_once_and_blocks_wave(fake, status):
    fake.init_status, fake.init_handle = status, b"failed-init"
    with pytest.raises(RuntimeError) as failure:
        fake.dut.initialize(initializer())
    assert failure.value.diagnostic["status"] == status
    assert failure.value.diagnostic["init_out"] == (0., 1., 0., 0., 0., 0., 0., 0.)
    assert failure.value.diagnostic["params_raw"] == b"(candidate (initialized 1))"
    assert fake.closed == [b"fake-memory", b"failed-init"]
    fake.dut.close()
    fake.dut.__del__()
    assert len(fake.closed) == 2
    with pytest.raises(RuntimeError):
        fake.dut.getWave(np.array([1., 2.]))
    assert fake.seen == []


@pytest.mark.parametrize("error", [OSError("fake Init ABI failure"), ValueError("fake Init callback failure")])
def test_init_exception_cleans_handle_and_retains_original(fake, error):
    fake.init_error, fake.init_handle = error, b"partial-init"
    with pytest.raises(RuntimeError) as failure:
        fake.dut.initialize(initializer())
    assert failure.value.__cause__ is error
    assert failure.value.diagnostic["returned"] is False
    assert fake.closed == [b"fake-memory", b"partial-init"]


def test_init_failure_and_cleanup_failure_preserve_both_and_do_not_retry(fake):
    original, cleanup = ValueError("Init failed"), OSError("Close failed")
    fake.init_error, fake.init_cleanup_error = original, cleanup
    with pytest.raises(RuntimeError) as failure:
        fake.dut.initialize(initializer())
    assert failure.value.__cause__ is original
    assert failure.value.cleanup_error.__cause__ is cleanup
    assert failure.value.diagnostic["cleanup"]["returned"] is False
    assert fake.dut.last_close_call["release_status"] == "unknown"
    fake.dut.close()
    fake.dut.__del__()
    assert len(fake.closed) == 2


def test_explicit_close_failure_cannot_be_retried_by_close_or_destructor(fake):
    error = OSError("fake Close failure")
    fake.close_error = error
    with pytest.raises(RuntimeError) as failure:
        fake.dut.close()
    assert failure.value.__cause__ is error
    assert fake.dut.last_close_call["returned"] is False
    assert fake.dut.last_close_call["release_status"] == "unknown"
    fake.dut.close()
    fake.dut.__del__()
    assert fake.closed == [b"fake-memory"] and fake.close_owned == [False]


def test_destructor_failure_is_recorded_and_logged_without_escaping(fake, caplog):
    fake.close_error = OSError("destructor cleanup failed")
    with caplog.at_level("ERROR", logger=model.__name__):
        fake.dut.__del__()
    assert "destructor cleanup failed" in caplog.text
    fake.dut.__del__()
    assert fake.closed == [b"fake-memory"]
    assert fake.dut.last_close_call["returned"] is False


def test_reinit_close_failure_stops_before_new_init(fake):
    count = FAKE_CALLS["Init"]
    fake.close_error = OSError("old Close failed")
    with pytest.raises(RuntimeError):
        fake.dut.initialize(initializer())
    assert FAKE_CALLS["Init"] == count
    assert fake.dut.last_init_call["phase"] == "closing_previous"
    fake.dut.__del__()
    assert fake.closed == [b"fake-memory"]


def test_uninitialized_destructor_and_stateless_success_are_safe(fake):
    empty = model.AMIModel.__new__(model.AMIModel)
    empty.__del__()
    fake.dut.close()
    fake.init_handle = None
    fake.dut.initialize(initializer())
    fake.blocks = [dict(clocks=[0., -1.])]
    assert fake.dut.getWave(np.array([1., 2.]))[1].tolist() == [0.]
    fake.dut.close()
    assert fake.closed == [b"fake-memory"]


def test_empty_prefix_and_empty_wave_do_not_reset_epoch_history(fake):
    fake.blocks = [dict(clocks=[-1., np.nan, -5.]), dict(clocks=[0., -1.]), dict(clocks=[0., -1.])]
    assert fake.dut.getWave(np.array([1., 2.]))[1].tolist() == []
    fake.dut.getWave(np.array([1., 2.]))
    empty = fake.dut.getWave(np.array([]))
    assert all(len(item) == 0 for item in empty)
    with pytest.raises(RuntimeError):
        fake.dut.getWave(np.array([1., 2.]))
    assert len(fake.seen) == 3


def test_failed_init_with_null_handle_does_not_call_close_for_it(fake):
    fake.init_status, fake.init_handle = 0, None
    with pytest.raises(RuntimeError) as failure:
        fake.dut.initialize(initializer())
    assert failure.value.diagnostic["has_handle"] is False
    assert failure.value.diagnostic["cleanup"] is None
    assert fake.closed == [b"fake-memory"]
    fake.dut.__del__()
    assert len(fake.closed) == 1


def test_observations_copy_init_input_and_raw_parameters_without_parser(fake):
    record = fake.dut.last_init_call
    assert record["input_impulse"] == (0., 1., 0., 0., 0., 0., 0., 0.)
    assert record["params_in"] == b"(candidate )"
    assert (record["row_size"], record["num_aggressors"],
            record["bit_time"], record["sample_interval"]) == (8, 0, 1., .5)
    fake.dut.close()
    assert record["message_raw"] == b"fake Init only"
    assert record["params_raw"] == b"(candidate (initialized 1))"


def test_uninitialized_getwave_is_rejected_before_any_callback():
    dut = model.AMIModel.__new__(model.AMIModel)
    with pytest.raises(RuntimeError) as failure:
        dut.getWave(np.array([1., 2.]))
    assert failure.value.diagnostic["epoch"] == 0
    assert failure.value.diagnostic["c_call"] is None


def test_default_empty_parameter_string_fallback_is_unchanged(fake):
    fake.blocks = [dict(clocks=[0., -1.], params=None)]
    assert fake.dut.getWave(np.array([1., 2.]))[2] == ["(candidate )"]
