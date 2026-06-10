# -*- coding: utf-8 -*-

import pytest

from emulator_start_jobs import _jobs_mem, _lock, start_emulator_job, start_switch_model_job


def test_start_emulator_job_rejects_duplicate():
    with _lock:
        _jobs_mem.clear()
        _jobs_mem["running-job"] = {"state": "running", "job_id": "running-job"}

    try:
        with pytest.raises(RuntimeError, match="已有模拟器启动任务进行中"):
            start_emulator_job("Testory_Pixel7")
    finally:
        with _lock:
            _jobs_mem.clear()


def test_start_switch_model_job_rejects_duplicate():
    with _lock:
        _jobs_mem.clear()
        _jobs_mem["running-job"] = {"state": "running", "job_id": "running-job"}

    try:
        with pytest.raises(RuntimeError, match="已有模拟器启动任务进行中"):
            start_switch_model_job("pixel_7")
    finally:
        with _lock:
            _jobs_mem.clear()
