"""Testes da seleção, observação e entrega progressiva da frota v0.13."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mirai.devices import (
    MAX_DEVICE_TAGS,
    Device,
    add_device,
    get_device,
    normalize_device_tags,
    update_device_tags,
)
from mirai.errors import MiraiRuntimeError
from mirai.fleet import (
    execute_rollout,
    observe_fleet,
    parse_selector,
    rollout_batches,
    select_devices,
)
from mirai.pilot import LaunchResult


def _devices(*names: str) -> dict[str, Device]:
    return {
        name: Device(
            name,
            f"http://127.0.0.1:{8000 + index}",
            tags=("env=prod", "region=br"),
        )
        for index, name in enumerate(names)
    }


def _launch_result(name: str) -> LaunchResult:
    return LaunchResult(
        deployment={"deployment_id": f"deployment-{name}"},
        inference={"status": "success"},
        previous_active_deployment_id=f"previous-{name}",
        rollback=None,
    )


def test_tags_are_normalized_sorted_and_persisted(tmp_path: Path) -> None:
    registry = tmp_path / "devices.json"
    device = add_device(
        "edge",
        "http://127.0.0.1:8080",
        path=registry,
        tags=["Zone=sp", "env=Prod"],
    )

    assert device.tags == ("env=Prod", "zone=sp")
    assert get_device("edge", path=registry).tags == device.tags
    assert json.loads(registry.read_text(encoding="utf-8"))["devices"][0]["tags"] == [
        "env=Prod",
        "zone=sp",
    ]


def test_registry_v3_migrates_without_tags(tmp_path: Path) -> None:
    registry = tmp_path / "devices.json"
    registry.write_text(
        json.dumps(
            {
                "version": 3,
                "devices": [{"name": "local", "url": "http://127.0.0.1:8080"}],
            }
        ),
        encoding="utf-8",
    )

    assert get_device("local", path=registry).tags == ()


def test_update_tags_merges_replaces_and_removes(tmp_path: Path) -> None:
    registry = tmp_path / "devices.json"
    add_device(
        "edge",
        "http://127.0.0.1:8080",
        path=registry,
        tags=["env=dev", "region=br"],
    )

    updated = update_device_tags(
        "edge",
        set_tags=["env=prod", "zone=sp"],
        remove_keys=["REGION", "missing"],
        path=registry,
    )

    assert updated.tags == ("env=prod", "zone=sp")
    assert get_device("edge", path=registry) == updated


@pytest.mark.parametrize(
    "tags",
    [
        ["missing-separator"],
        ["1key=value"],
        ["key=value with spaces"],
        ["key=a", "KEY=b"],
        ["key="],
        [123],
        "key=value",
    ],
)
def test_tags_reject_invalid_or_ambiguous_values(tags: object) -> None:
    with pytest.raises(MiraiRuntimeError):
        normalize_device_tags(tags)


def test_tags_enforce_per_device_limit() -> None:
    tags = [f"key{index}=value" for index in range(MAX_DEVICE_TAGS + 1)]

    with pytest.raises(MiraiRuntimeError, match="no máximo"):
        normalize_device_tags(tags)


def test_selector_is_conjunctive_normalized_and_deterministic() -> None:
    devices = _devices("zeta", "alpha", "beta")
    devices["beta"] = Device(
        "beta",
        devices["beta"].url,
        tags=("env=dev", "region=br"),
    )

    selected = select_devices(devices, " ENV=prod,region=br ")

    assert [device.name for device in selected] == ["alpha", "zeta"]
    assert parse_selector(None) == {}
    assert parse_selector("  ") == {}


@pytest.mark.parametrize(
    "selector",
    [
        "env",
        "env=prod,ENV=dev",
        "=prod",
        "env=value with space",
        ",".join(f"k{i}=v" for i in range(17)),
    ],
)
def test_selector_rejects_invalid_expressions(selector: str) -> None:
    with pytest.raises(MiraiRuntimeError):
        parse_selector(selector)


@pytest.mark.parametrize(
    ("count", "canary", "batch_size", "sizes"),
    [
        (1, 10, 10, [1]),
        (10, 10, 3, [1, 3, 3, 3]),
        (11, 10, 4, [2, 4, 4, 1]),
        (5, 100, 1, [5]),
    ],
)
def test_rollout_batches_have_deterministic_canary(
    count: int,
    canary: int,
    batch_size: int,
    sizes: list[int],
) -> None:
    devices = list(reversed(list(_devices(*(f"d{i}" for i in range(count))).values())))

    batches = rollout_batches(devices, canary_percent=canary, batch_size=batch_size)

    assert [len(batch) for batch in batches] == sizes
    assert [device.name for batch in batches for device in batch] == sorted(
        f"d{i}" for i in range(count)
    )


def test_rollout_dry_run_validates_and_writes_complete_plan(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    launches: list[str] = []

    def launch(*args: Any, **kwargs: Any) -> LaunchResult:
        launches.append("unexpected")
        return _launch_result("unexpected")

    report = execute_rollout(
        dummy_model,
        _devices("b", "a"),
        canary_percent=50,
        batch_size=1,
        input_specs=["secret-input-must-not-be-recorded"],
        report_directory=tmp_path / "reports",
        launch=launch,
    )
    persisted = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))

    assert launches == []
    assert report["status"] == "planned"
    assert report["batches"] == [["a"], ["b"]]
    assert persisted["report_path"] == report["report_path"]
    assert "secret-input-must-not-be-recorded" not in json.dumps(persisted)


def test_rollout_success_processes_every_batch(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    launched: list[str] = []

    def launch(_: Path, name: str, *args: Any, **kwargs: Any) -> LaunchResult:
        launched.append(name)
        return _launch_result(name)

    report = execute_rollout(
        dummy_model,
        _devices("c", "a", "b"),
        canary_percent=34,
        batch_size=1,
        apply=True,
        report_directory=tmp_path,
        launch=launch,
    )

    assert report["status"] == "passed"
    assert sorted(launched) == ["a", "b", "c"]
    assert [item["device"] for item in report["results"]] == ["a", "b", "c"]
    assert all(item["status"] == "passed" for item in report["results"])


def test_rollout_gate_stops_skips_and_rolls_back_all_successes(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    rolled_back: list[str] = []

    def launch(_: Path, name: str, *args: Any, **kwargs: Any) -> LaunchResult:
        if name == "b":
            raise MiraiRuntimeError("health gate failed")
        return _launch_result(name)

    def rollback(device: Device, _: LaunchResult) -> dict[str, Any]:
        rolled_back.append(device.name)
        return {"status": "restored", "restored_deployment_id": f"previous-{device.name}"}

    report = execute_rollout(
        dummy_model,
        _devices("a", "b", "c", "d"),
        canary_percent=25,
        batch_size=2,
        max_failure_rate=0,
        apply=True,
        report_directory=tmp_path,
        launch=launch,
        rollback=rollback,
    )

    by_device = {item["device"]: item for item in report["results"]}
    assert report["status"] == "rolled_back"
    assert report["skipped"] == ["d"]
    assert set(rolled_back) == {"a", "c"}
    assert by_device["a"]["status"] == "rolled_back"
    assert by_device["b"]["status"] == "failed"
    assert by_device["c"]["status"] == "rolled_back"


def test_rollout_preserves_rollback_failure_as_evidence(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    def launch(_: Path, name: str, *args: Any, **kwargs: Any) -> LaunchResult:
        if name == "b":
            raise MiraiRuntimeError("failed")
        return _launch_result(name)

    def rollback(_: Device, __: LaunchResult) -> dict[str, Any]:
        raise MiraiRuntimeError("rollback unavailable")

    report = execute_rollout(
        dummy_model,
        _devices("a", "b"),
        canary_percent=50,
        batch_size=1,
        apply=True,
        report_directory=tmp_path,
        launch=launch,
        rollback=rollback,
    )

    assert report["status"] == "rollback_failed"
    assert report["results"][0]["status"] == "rollback_failed"
    assert "rollback unavailable" in report["rollback"][0]["error"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"canary_percent": 0},
        {"canary_percent": 101},
        {"batch_size": 0},
        {"workers": 0},
        {"workers": 33},
        {"max_failure_rate": -0.1},
        {"max_failure_rate": float("nan")},
        {"layout": "chw"},
    ],
)
def test_rollout_rejects_unsafe_policy_values(
    dummy_model: Path,
    tmp_path: Path,
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(MiraiRuntimeError):
        execute_rollout(
            dummy_model,
            _devices("a"),
            report_directory=tmp_path,
            **overrides,
        )


def test_rollout_rejects_missing_artifact_and_empty_signature(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(MiraiRuntimeError):
        execute_rollout(tmp_path / "missing.onnx", _devices("a"), report_directory=tmp_path)

    signature = tmp_path / "empty.dsse.json"
    signature.touch()
    with pytest.raises(MiraiRuntimeError, match="assinatura"):
        execute_rollout(
            dummy_model,
            _devices("a"),
            signature_path=signature,
            report_directory=tmp_path,
        )


def test_rollout_rejects_empty_selection(dummy_model: Path, tmp_path: Path) -> None:
    with pytest.raises(MiraiRuntimeError, match="nenhum dispositivo"):
        execute_rollout(
            dummy_model,
            _devices("a"),
            selector="env=dev",
            report_directory=tmp_path,
        )


def test_observe_fleet_keeps_partial_failures_and_stable_order() -> None:
    devices = list(_devices("b", "a").values())

    def metrics(device: Device) -> dict[str, Any]:
        if device.name == "b":
            raise MiraiRuntimeError("offline")
        return {"counters": {"inferences_total": 2}}

    results = observe_fleet(
        devices,
        metrics=metrics,
        drift=lambda _: {"deployments": {}},
    )

    assert [item["name"] for item in results] == ["a", "b"]
    assert results[0]["status"] == "online"
    assert results[1]["status"] == "offline"
    assert "offline" in results[1]["error"]


def test_observe_fleet_empty_and_invalid_workers() -> None:
    assert observe_fleet([]) == []
    with pytest.raises(MiraiRuntimeError, match="workers"):
        observe_fleet(list(_devices("a").values()), workers=0)
