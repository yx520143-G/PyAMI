"""Exercise production AMI methods through Python callbacks, never a model DLL."""

from collections import Counter
from ctypes import c_double, c_long
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
        self.init_params = b"(candidate (initialized 1))"
        self.write_init_params = True
        self.init_seen = []
        self.close_error = None
        self.close_status = 1
        self.close_owned = []
        self.on_close = None

    def init(self, impulse, rows, aggressors, ts, ui, params, output, handle, message):
        FAKE_CALLS["Init"] += 1
        FAKE_EVENTS.append(("Init", self.init_status))
        self.init_seen.append(output._obj.value)
        handle._obj.value = self.init_handle
        if self.write_init_params:
            output._obj.value = self.init_params
        message._obj.value = b"fake Init only"
        if self.init_cleanup_error:
            self.close_error = self.init_cleanup_error
        if self.init_error:
            raise self.init_error
        return self.init_status

    def wave(self, signal, size, clocks, output, handle):
        FAKE_CALLS["GetWave"] += 1
        block = self.blocks.pop(0)
        self.seen.append(dict(size=size, clocks=list(clocks._obj), wave=list(signal._obj),
                             params_in=output._obj.value))
        FAKE_EVENTS.append(("GetWave", size))
        for i in range(size):
            signal._obj[i] += block.get("offset", 0.)
        for i, value in enumerate(block["clocks"]):
            clocks._obj[i] = value
        if block.get("write_params", True):
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
        return self.close_status


def initializer():
    result = model.AMIModelInitializer({"root_name": "candidate"},
                                       bit_time=c_double(1.), sample_interval=c_double(.5))
    result.channel_response = np.array([0., 1., 0., 0., 0., 0., 0., 0.])
    return result


def test_clock_history_retains_short_chunks_across_calls_and_close(fake):
    identity = fake.dut.clock_history["epoch_id"]
    fake.blocks = [{"clocks": [0., -1.]}, {"clocks": [1., -1.]}, {"clocks": [2., -1.]}]
    fake.dut.getWave(np.arange(3.), bits_per_call=1)
    fake.dut.getWave(np.arange(1.))
    history = fake.dut.clock_history
    assert history["epoch_id"] == identity and history["sample_interval_s"] == .5
    assert [item["input_samples"] for item in history["invocations"]] == [3, 1]
    assert [item["wave_size"] for call in history["invocations"] for item in call["chunks"]] == [2, 1, 1]
    assert [item["valid_clocks"] for call in history["invocations"] for item in call["chunks"]] == [(0.,), (1.,), (2.,)]
    history["invocations"].clear()
    fake.dut.close()
    assert len(fake.dut.clock_history["invocations"]) == 2
    assert not fake.dut.clock_history["ready"]


def test_new_init_has_new_identity_and_empty_history(fake):
    first = fake.dut.clock_history["epoch_id"]
    other = FakeAMI()
    other.dut.initialize(initializer())
    assert other.dut.clock_history["epoch_id"] != first
    fake.dut.initialize(initializer())
    assert fake.dut.clock_history["epoch_id"] != first
    assert fake.dut.clock_history["epoch"] == 2
    assert fake.dut.clock_history["invocations"] == []


def test_failed_call_and_rejected_retry_cannot_form_complete_history(fake):
    fake.blocks = [{"clocks": [0., -1.]}, {"clocks": [1., -1.], "status": 0}]
    with pytest.raises(model.AMIModelError):
        fake.dut.getWave(np.arange(3.), bits_per_call=1)
    original = fake.dut.clock_history
    assert not original["invocations"][0]["completed"]
    assert [item["status"] for item in original["invocations"][0]["chunks"]] == [1, 0]
    with pytest.raises(model.AMIModelError):
        fake.dut.getWave(np.arange(1.))
    current = fake.dut.clock_history
    assert not current["ready"] and current["invocations"] == original["invocations"]
    assert current["python_calls"] == original["python_calls"] + 1


@pytest.mark.parametrize("block,phase,kind", [
    ({"clocks": [-1.], "status": 0}, "checking_status", "model_execution"),
    ({"clocks": []}, "validating_clocks", "clock_interface"),
    ({"clocks": [-1.], "params": b"\xff"}, "decoding_parameters", "host_post_return"),
    ({"clocks": [-1.], "error": OSError("boundary")}, "calling", "native_call_unknown"),
])
def test_failure_phase_does_not_conflate_host_decode_with_model_status(fake, block, phase, kind):
    fake.blocks = [block]
    with pytest.raises(model.AMIModelError) as caught:
        fake.dut.getWave(np.array([1., 2.]))
    record = caught.value.diagnostic
    assert record["phase"] == phase and record["failure_kind"] == kind
    assert record["wave"] == (1., 2.)
    assert record["returned"] is ("error" not in block)
    assert record["continuation"] == "blocked"
    with pytest.raises(model.AMIModelError):
        fake.dut.getWave(np.array([1., 2.]))
    assert len(fake.seen) == 1


@pytest.mark.parametrize("after_chunk", [False, True])
def test_pre_call_host_failure_tracks_whether_this_python_call_already_consumed_input(fake, after_chunk):
    fake.blocks = [{"clocks": [-1.]}]
    data = [1., 2., "bad"] if after_chunk else ["bad"]
    with pytest.raises(model.AMIModelError) as caught:
        fake.dut.getWave(data, bits_per_call=1)
    record = caught.value.diagnostic
    assert record["phase"] == "preparing" and record["failure_kind"] == "host_pre_call"
    assert record["c_call"] is None and record["returned"] is False
    assert record["completed_samples"] == (2 if after_chunk else 0)
    assert len(fake.seen) == int(after_chunk)
    if after_chunk:
        with pytest.raises(model.AMIModelError):
            fake.dut.getWave([3.])
    else:
        assert record["continuation"] == "ready"
        assert fake.dut.getWave([3.])[0].tolist() == [3.]


def test_cancel_during_native_call_is_retained_and_never_retried(fake):
    fake.blocks = [{"clocks": [-1.], "error": KeyboardInterrupt()}]
    with pytest.raises(KeyboardInterrupt):
        fake.dut.getWave([1., 2.])
    record = fake.dut.last_getwave_calls[-1]
    assert record["phase"] == "calling" and record["failure_kind"] == "cancelled"
    assert record["wave"] == (1., 2.) and record["continuation"] == "blocked"
    with pytest.raises(model.AMIModelError):
        fake.dut.getWave([1., 2.])
    assert len(fake.seen) == 1


@pytest.mark.parametrize("native_error", [None, OSError("native call failed"), 0])
def test_failed_auxiliary_capture_keeps_wave_and_primary_failure(fake, native_error):
    class UnreadableParameters:
        @property
        def value(self):
            raise ValueError("host parameter capture failed")

    original = fake.dut._amiGetWave
    fake.blocks = [{"clocks": [-1.], "offset": 4.}]

    def invoke(*args):
        result = original(*args)
        fake.dut._ami_params_out = UnreadableParameters()
        if isinstance(native_error, Exception):
            raise native_error
        return native_error if native_error == 0 else result

    fake.dut._amiGetWave = invoke
    with pytest.raises(model.AMIModelError) as caught:
        fake.dut.getWave([1., 2.])
    record = caught.value.diagnostic
    assert record["wave"] == (5., 6.) and record["clock_buffer"][0] == -1.
    assert "params_raw" not in record
    assert record["continuation"] == "blocked"
    if isinstance(native_error, Exception):
        assert caught.value.__cause__ is native_error
        assert record["failure_kind"] == "native_call_unknown"
        assert record["capture_error"] == "host parameter capture failed"
    elif native_error == 0:
        assert record["status"] == 0 and record["returned"] is True
        assert record["failure_kind"] == "model_execution"
        assert record["capture_error"] == "host parameter capture failed"
    else:
        assert record["status"] == 1 and record["returned"] is True
        assert record["phase"] == "capturing_outputs"
        assert record["failure_kind"] == "host_post_return"


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


def test_close_binding_uses_c_long_return(monkeypatch):
    def close(_handle):
        return 1

    close.restype = None
    dll = SimpleNamespace(AMI_Init=lambda *args: 1, AMI_GetWave=lambda *args: 1, AMI_Close=close)
    monkeypatch.setattr(model, "CDLL", lambda _filename: dll)
    dut = model.AMIModel("NO_DLL_ALLOWED")
    assert dut._amiClose.restype is c_long


def test_close_success_retains_status_without_claiming_release(fake):
    fake.dut.close()
    record = fake.dut.last_close_call
    assert record["status"] == 1 and record["returned"] is True
    assert record["release_status"] == "not_verified"
    record["status"] = 99
    fake.dut.close()
    assert fake.dut.last_close_call["status"] == 1
    assert fake.closed == [b"fake-memory"]


@pytest.mark.parametrize("status", [0, -1, 2, None])
def test_close_bad_status_is_failure_and_never_retried(fake, status):
    fake.close_status = status
    with pytest.raises(model.AMIModelError, match="Unsuccessful return status") as failure:
        fake.dut.close()
    record = fake.dut.last_close_call
    assert record["status"] == status and record["returned"] is True
    assert record["release_status"] == "unknown"
    assert failure.value.diagnostic == record
    assert fake.close_owned == [False]
    fake.dut.close()
    fake.dut.__del__()
    assert fake.dut.last_close_call == record
    assert fake.closed == [b"fake-memory"]


def test_close_exception_has_no_return_status(fake):
    error = OSError("ABI callback failed")
    fake.close_error = error
    with pytest.raises(model.AMIModelError) as failure:
        fake.dut.close()
    assert failure.value.__cause__ is error
    assert failure.value.diagnostic["status"] is None
    assert failure.value.diagnostic["returned"] is False


def test_reinit_close_zero_stops_before_new_init(fake):
    count = FAKE_CALLS["Init"]
    fake.close_status = 0
    with pytest.raises(model.AMIModelError):
        fake.dut.initialize(initializer())
    assert FAKE_CALLS["Init"] == count
    assert fake.dut.last_init_call["phase"] == "closing_previous"
    assert fake.dut.last_close_call["status"] == 0
    assert fake.closed == [b"fake-memory"]


def test_init_failure_retains_cleanup_zero_separately(fake):
    def fail_init(*args):
        status = fake.init(*args)
        fake.close_status = 0
        return status

    fake.init_status = 0
    fake.dut._amiInit = fail_init
    with pytest.raises(model.AMIModelError) as failure:
        fake.dut.initialize(initializer())
    assert failure.value.diagnostic["status"] == 0
    cleanup = failure.value.cleanup_error
    assert isinstance(cleanup, model.AMIModelError)
    assert cleanup.diagnostic["status"] == 0 and cleanup.diagnostic["returned"] is True
    fake.dut.close()
    fake.dut.__del__()
    assert fake.closed == [b"fake-memory", b"fake-memory"]


def test_destructor_logs_close_zero_without_retry(fake, caplog):
    fake.close_status = 0
    with caplog.at_level("ERROR", logger=model.__name__):
        fake.dut.__del__()
    assert "Unsuccessful return status 0" in caplog.text
    fake.dut.__del__()
    assert fake.closed == [b"fake-memory"]


@pytest.mark.parametrize("write,params", [
    (False, None), (True, None), (True, b""), (True, b"(candidate )"), (True, b"(candidate (value 4))"),
])
def test_init_parameter_pointer_starts_null_and_raw_is_distinct(fake, write, params):
    original = fake.dut.last_init_call
    fake.write_init_params, fake.init_params = write, params
    fake.dut.initialize(initializer())
    assert fake.init_seen == [None, None]
    assert fake.dut.last_init_call["params_raw"] == params
    assert fake.dut.ami_params_out == (params.decode() if params else "(candidate )")
    assert original["params_raw"] == b"(candidate (initialized 1))"


def test_getwave_clears_parameter_pointer_per_chunk_and_python_call(fake):
    fake.blocks = [
        {"clocks": [-1.], "params": b"(candidate (value 8))"},
        {"clocks": [-1.], "write_params": False},
        {"clocks": [-1.], "params": None},
        {"clocks": [-1.], "params": b""},
        {"clocks": [-1.], "params": b"(candidate )"},
        {"clocks": [-1.], "write_params": False},
    ]
    params = fake.dut.getWave(np.arange(17.), bits_per_call=2)[2]
    records = fake.dut.last_getwave_calls
    assert [record["params_raw"] for record in records] == [
        b"(candidate (value 8))", None, None, b"", b"(candidate )"]
    assert params == ["(candidate (value 8))"] + ["(candidate )"] * 4
    assert [entry["size"] for entry in fake.seen] == [4, 4, 4, 4, 1]
    assert fake.dut.getWave(np.array([1.]))[2] == ["(candidate )"]
    assert fake.dut.last_getwave_calls[0]["params_raw"] is None
    assert all(entry["params_in"] is None for entry in fake.seen)
    assert records[0]["params_raw"] == b"(candidate (value 8))"


@pytest.mark.parametrize("failure", [{"status": 0}, {"error": OSError("GetWave failed")}])
def test_failed_getwave_without_parameter_write_never_reuses_previous_value(fake, failure):
    fake.blocks = [{"clocks": [-1.], "params": b"previous"}, {"clocks": [-1.], "write_params": False, **failure}]
    fake.dut.getWave(np.array([1.]))
    with pytest.raises(model.AMIModelError) as caught:
        fake.dut.getWave(np.array([1.]))
    assert caught.value.diagnostic["params_raw"] is None
    assert fake.seen[-1]["params_in"] is None


def test_empty_wave_does_not_clear_last_parameter_observation(fake):
    fake.blocks = [{"clocks": [-1.], "params": b"previous"}]
    fake.dut.getWave(np.array([1.]))
    assert fake.dut.getWave(np.array([]))[2] == []
    assert fake.dut.ami_params_out == "previous"
    assert len(fake.seen) == 1
