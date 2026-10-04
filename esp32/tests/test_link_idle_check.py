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

COMMON = r"""
#include <assert.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "led_status.h"
#define ESP_LOGW(tag, ...) ((void)(tag), (void)sizeof(printf(__VA_ARGS__)))
#define pdMS_TO_TICKS(ms) (ms)
#define pdPASS 1
static const char *TAG = "test";
static bool (*volatile s_idle_check)(void) = NULL;
static int busy_polls, waited_ms, long_waits, restarts, resets;
static const char *reset_source;
bool idle_after_polls(void) { return busy_polls-- <= 0; }
void vTaskDelay(int ms) {
    waited_ms += ms;
    if (ms == 1000) long_waits++;
}
void esp_restart(void) { restarts++; }
void stack_monitor_record(void *monitor) { (void)monitor; }
bool reset_setup_from_control(const char *source) {
    reset_source = source;
    resets++;
    return true;
}
"""

RESTART_MAIN = r"""
static void reset_counts(void) { waited_ms = long_waits = restarts = 0; }
int main(void) {
    // No idle check: upstream timing.
    restart_task(NULL);
    assert(restarts == 1 && long_waits == 0 && waited_ms == 500);

    // Busy for three polls: the reboot waits, checking once a second.
    reset_counts();
    app_set_idle_check(idle_after_polls);
    busy_polls = 3;
    restart_task(NULL);
    assert(restarts == 1 && long_waits == 3 && waited_ms == 3500);
    return 0;
}
"""

BUTTON_MAIN = r"""
static led_state_t led = LED_STATE_WS_CONNECTED;
static led_state_t shown[4];
static int changes;
led_state_t led_status_get_state(void) { return led; }
void led_status_set_state(led_state_t state) { led = state; shown[changes++] = state; }
"""

BUTTON_RUN = r"""
static bool never_idle(void) { return false; }
static bool always_idle(void) { return true; }
int main(void) {
    on_button_long_press();
    assert(resets == 1 && strcmp(reset_source, "button long-press") == 0);

    app_set_idle_check(never_idle);
    on_button_long_press();
    assert(resets == 1 && waited_ms == 3000);
    assert(changes == 2 && shown[0] == LED_STATE_WS_DISCONNECTED);
    assert(shown[1] == LED_STATE_WS_CONNECTED && led == LED_STATE_WS_CONNECTED);

    app_set_idle_check(always_idle);
    on_button_long_press();
    assert(resets == 2);
    return 0;
}
"""

API_FAKES = r"""
#include <assert.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "app.h"
static bool runtime_disabled(void) { return false; }
#define ESP_LOGW(tag, ...) ((void)(tag), (void)sizeof(printf(__VA_ARGS__)))
#define pdPASS 1
typedef void (*TaskFunction_t)(void *);
static const char *TAG = "test";
static bool (*volatile s_idle_check)(void) = NULL;
static atomic_bool s_setup_advertising, s_setup_reset_pending;
static bool setup_done, provisioned, connected, task_fails;
static int opens, resets;
static const char *last_source;
bool config_setup_complete(void) { return setup_done; }
bool config_is_provisioned(void) { return provisioned; }
bool noise_ctrl_is_connected(void) { return connected; }
void open_setup_window(const char *reason) { last_source = reason; opens++; }
bool reset_setup_from_control(const char *source) { last_source = source; resets++; return true; }
void stack_monitor_record(void *monitor) { (void)monitor; }
void vTaskDelete(void *task) { (void)task; }
int xTaskCreate(TaskFunction_t task, const char *name, unsigned stack, void *arg,
                unsigned priority, void *handle) {
    (void)name; (void)stack; (void)priority; (void)handle;
    if (task_fails) return 0;
    task(arg);
    return pdPASS;
}
"""

API_MAIN = r"""
static bool always_busy(void) { return false; }
int main(void) {
    app_gadget_status_t status;
    atomic_store(&s_setup_advertising, true);
    app_get_gadget_status(&status);
    assert(!status.paired && !status.connected && status.setup_window_open);

    // Opening the window: only while unpaired, never confirming anything.
    app_open_setup_window();
    assert(opens == 1 && strcmp(last_source, "host request") == 0);

    // Unpair is a no-op with nothing paired.
    app_request_unpair();
    assert(resets == 0);

    setup_done = provisioned = connected = true;
    app_get_gadget_status(&status);
    assert(status.paired && status.connected && !status.setup_window_open);
    app_open_setup_window();
    assert(opens == 1);

    app_set_idle_check(always_busy);
    assert(s_idle_check == always_busy);
    app_request_unpair();
    assert(resets == 1 && strcmp(last_source, "host unpair request") == 0);

    // Without memory for the worker, the heartbeat runs the reset.
    task_fails = true;
    app_request_unpair();
    assert(resets == 1 && atomic_load(&s_setup_reset_pending));
    app_get_gadget_status(NULL);
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


def functions(*signatures: str) -> str:
    app = APP_C.read_text()
    return "\n".join(function_source(app, s) for s in signatures)


class LinkIdleCheckTest(unittest.TestCase):
    def run_c(self, source_text: str) -> None:
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            source = tmp / "idle.c"
            source.write_text(source_text)
            binary = tmp / "idle"
            compiled = subprocess.run(
                [*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "main"), str(source), "-o", str(binary)],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_reboot_waits_for_idle_once_a_second(self) -> None:
        self.run_c(COMMON + functions(
            "void app_set_idle_check(bool (*idle)(void)) {",
            "static bool app_is_idle(void) {",
            "static void restart_task(void *arg) {",
        ) + RESTART_MAIN)

    def test_button_hold_is_refused_with_three_yellow_blinks(self) -> None:
        self.run_c(COMMON + BUTTON_MAIN + functions(
            "void app_set_idle_check(bool (*idle)(void)) {",
            "static bool app_is_idle(void) {",
            "static void on_button_long_press(void) {",
        ) + BUTTON_RUN)

    def test_status_open_window_and_guarded_unpair(self) -> None:
        self.run_c(API_FAKES + functions(
            "void app_set_idle_check(bool (*idle)(void)) {",
            "void app_get_gadget_status(app_gadget_status_t *out) {",
            "static void host_setup_window_task(void *arg) {",
            "void app_open_setup_window(void) {",
            "static void host_unpair_task(void *arg) {",
            "void app_request_unpair(void) {",
        ) + API_MAIN)

    def test_every_guarded_reboot_goes_through_restart_task(self) -> None:
        app = APP_C.read_text()
        restart = function_source(app, "static void restart_after_setup_reset(void) {")
        self.assertIn("xTaskCreate(restart_task,", restart)
        reset = function_source(app, "static bool reset_setup_from_control(")
        self.assertIn("restart_after_setup_reset()", reset)
        # Server device.unpair (worker and its no-memory path), node.unpaired,
        # token revocation (the heartbeat's deferred reset) and host unpair.
        worker = function_source(app, "static void ws_control_task(")
        unpair = worker[worker.index("case WS_CONTROL_UNPAIR:"):]
        self.assertIn("restart_after_setup_reset()", unpair[:unpair.index("break;")])
        self.assertIn('reset_setup_from_control("server unpair (node.unpaired)")',
                      function_source(app, "static void ws_unpaired_task("))
        self.assertIn('reset_setup_from_control("deferred setup reset")',
                      function_source(app, "void app_run("))
        self.assertIn('reset_setup_from_control("host unpair request")',
                      function_source(app, "static void host_unpair_task("))
        # Nothing else in app.c reboots directly, apart from the setup-reset
        # fallback when its task cannot start and Muse's pair-then-restart.
        self.assertEqual(app.count("esp_restart();"), 3)

    def test_led_state_can_be_read_back_on_every_backend(self) -> None:
        header = (ROOT / "main/led_status.h").read_text()
        self.assertIn("led_state_t led_status_get_state(void);", header)
        for impl in ("led_status.c", "epaper_status.c"):
            self.assertIn("\nled_state_t led_status_get_state(void) {",
                          (ROOT / "main" / impl).read_text())


if __name__ == "__main__":
    unittest.main()
