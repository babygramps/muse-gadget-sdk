# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_C = ROOT / "main" / "app.c"

FAKES = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include "esp_ota_ops.h"
#define ESP_LOGI(tag, ...) ((void)(tag), (void)sizeof(printf(__VA_ARGS__)))
#define ESP_LOGW ESP_LOGI
#define ESP_LOGE ESP_LOGI
#define pdMS_TO_TICKS(ms) (ms)
#define OTA_VERIFY_TIMEOUT_US (300LL * 1000000LL)
static const char *TAG = "test";
static bool s_ota_pending_verify, connected;
static esp_ota_img_states_t state;
static int64_t now_us;
static int validated, rollbacks, deletes;
static const esp_partition_t running_partition;
int64_t esp_timer_get_time(void) { return now_us; }
void vTaskDelay(int ms) { now_us += (int64_t)ms * 1000; }
void vTaskDelete(void *task) { (void)task; deletes++; }
void stack_monitor_record(void *monitor) { (void)monitor; }
bool noise_ctrl_is_connected(void) { return connected; }
const esp_partition_t *esp_ota_get_running_partition(void) { return &running_partition; }
esp_err_t esp_ota_get_state_partition(const esp_partition_t *partition,
                                      esp_ota_img_states_t *out) {
    assert(partition == &running_partition);
    *out = state;
    return ESP_OK;
}
esp_err_t esp_ota_mark_app_valid_cancel_rollback(void) { validated++; return ESP_OK; }
esp_err_t esp_ota_mark_app_invalid_rollback_and_reboot(void) { rollbacks++; return ESP_OK; }
"""

MAIN = r"""
static void run(bool pending_at_boot, esp_ota_img_states_t at_timeout, bool session) {
    s_ota_pending_verify = pending_at_boot;
    state = at_timeout;
    connected = session;
    now_us = validated = rollbacks = deletes = 0;
    ota_verify_task(NULL);
    assert(deletes == 1);
}
int main(void) {
    // Still pending after 300 s without a session: roll back.
    run(true, ESP_OTA_IMG_PENDING_VERIFY, false);
    assert(rollbacks == 1 && validated == 0 && now_us >= 300000000);

    // Validated meanwhile by something else: keep it.
    run(true, ESP_OTA_IMG_VALID, false);
    assert(rollbacks == 0 && validated == 0);

    // A session validates it at once.
    run(true, ESP_OTA_IMG_PENDING_VERIFY, true);
    assert(rollbacks == 0 && validated == 1 && now_us == 0);

    // A normal boot is never rolled back.
    run(false, ESP_OTA_IMG_PENDING_VERIFY, false);
    assert(rollbacks == 0);
    return 0;
}
"""


def function_source(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for end in range(brace, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                return source[start : end + 1]
    raise AssertionError(f"Unclosed function: {signature}")


class LinkOtaVerifyTest(unittest.TestCase):
    def test_only_an_image_still_pending_is_rolled_back(self) -> None:
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            self.skipTest("C compiler unavailable")
        task = function_source(APP_C.read_text(), "static void ota_verify_task(void *arg) {")
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            source = tmp / "verify.c"
            source.write_text(FAKES + task + MAIN)
            binary = tmp / "verify"
            compiled = subprocess.run(
                [*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "tests/link_fakes"), str(source), "-o", str(binary)],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_builds_without_ota_never_start_the_task(self) -> None:
        app_run = function_source(APP_C.read_text(), "void app_run(")
        gate = app_run.index("if (ota_is_enabled()) {")
        block = function_source(app_run[gate:], "if (ota_is_enabled()) {")
        self.assertIn('xTaskCreate(ota_verify_task, "ota_verify"', block)
        self.assertIn("s_ota_pending_verify = true", block)
        self.assertEqual(app_run.count("xTaskCreate(ota_verify_task"), 1)


if __name__ == "__main__":
    unittest.main()
