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

#include "sdkconfig.h"

// What the three SDK token log sites (boot banner, pairing_confirmed, refresh)
// pass to "%.12s". Every site goes through this one function so the choice
// between the 12-character hint and "set" cannot drift between them. With
// CONFIG_HOMEHUB_LOG_SDK_TOKEN_PREFIX off, no character of the token reaches a
// log. Header-only so host tests compile the real thing without identity.c.
static inline const char *sdk_token_log_text(const char *token) {
    if (!token) return "(none)";
#if CONFIG_HOMEHUB_LOG_SDK_TOKEN_PREFIX
    return token;
#else
    return "set";
#endif
}
