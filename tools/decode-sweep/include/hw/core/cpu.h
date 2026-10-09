/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef SWEEP_HW_CORE_CPU_H
#define SWEEP_HW_CORE_CPU_H
#include "qemu/osdep.h"
typedef struct CPUState { int unused; } CPUState;
typedef struct CPUClass { int unused; } CPUClass;
typedef void (*DeviceRealize)(void *dev, void **errp);
typedef struct ResettablePhases { void *enter, *hold, *exit; } ResettablePhases;
typedef struct TranslationBlock TranslationBlock;
#define OBJECT_DECLARE_CPU_TYPE(Instance, Class, NAME) \
    typedef struct ArchCPU Instance; typedef struct Class Class;
#define PI32V2_CPU(obj) ((Pi32v2CPU *)(obj))
#define cpu_env(cs) (&PI32V2_CPU(cs)->env)
#endif
