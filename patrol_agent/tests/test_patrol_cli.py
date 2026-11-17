"""patrol.py 觸發情境：整個 namespace 與指定 Deployment。"""

from datetime import UTC, datetime

import pytest

import patrol
from prompts import build_main_prompt
from settings import Settings

NOW = datetime(2026, 11, 17, 8, 0, 0, tzinfo=UTC)


def test_namespace_only_args():
    args = patrol.parse_args(["-n", "payments"])
    assert args.namespace == ["payments"]
    assert args.deployment is None


def test_deployment_args_dedup_and_keep_order():
    args = patrol.parse_args(["-n", "payments", "-d", "checkout", "-d", "cart", "-d", "checkout"])
    assert args.deployment == ["checkout", "cart"]


def test_deployment_requires_single_namespace():
    with pytest.raises(SystemExit):
        patrol.parse_args(["-n", "payments", "-n", "orders", "-d", "checkout"])


@pytest.mark.parametrize("bad", ["Checkout", "../etc", "a/b", "-x", "name_with_underscore", ""])
def test_rejects_invalid_k8s_names(bad):
    with pytest.raises(SystemExit):
        patrol.parse_args(["-n", "payments", "-d", bad])
    with pytest.raises(SystemExit):
        patrol.parse_args(["-n", bad])


def test_request_for_whole_namespace():
    req = patrol.build_request("payments", NOW)
    assert "巡檢範圍：整個 namespace" in req
    assert "所有 Deployment" in req


def test_request_for_specific_deployments():
    req = patrol.build_request("payments", NOW, ["checkout", "cart"])
    assert "巡檢範圍：指定 Deployment" in req
    assert "「checkout」、「cart」" in req
    assert "不要巡檢這個 namespace 的其他 Deployment" in req
    assert "所有 Deployment" not in req


def test_report_filename_includes_deployment(tmp_path):
    path = patrol.write_report(tmp_path, "payments", NOW, "# 報告", ["checkout"])
    assert path.parent == tmp_path / "payments"
    assert path.name.endswith("-checkout.md")


def test_report_filename_truncates_many_deployments(tmp_path):
    many = [f"service-{i:03d}-with-a-long-name" for i in range(10)]
    path = patrol.write_report(tmp_path, "payments", NOW, "# 報告", many)
    assert path.name.endswith(f"-{many[0]}_and_9_more.md")


def test_prompt_describes_specific_deployment_scope():
    prompt = build_main_prompt(Settings())
    assert "指定 Deployment" in prompt
    assert "不得**巡檢或回報同一個 namespace 的其他 Deployment" in prompt
    assert "找不到" in prompt
