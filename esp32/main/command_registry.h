/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once

#include "noise_control.h"

#ifdef __cplusplus
extern "C" {
#endif

// Adds every registered command to a link.register commands_v2 object.
void command_registry_append(cJSON *commands);

// The registered command with this name, or NULL.
const noise_ctrl_command_def_t *command_registry_find(const char *name);

#ifdef __cplusplus
}
#endif
