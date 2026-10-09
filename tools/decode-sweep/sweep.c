/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Decode sweep driver. Runs the emulator's own instruction decoder
 * (translate.c, unchanged) over firmware addresses without generating code.
 *
 *   sweep IMAGE BASE < addresses   (one hex address per line)
 *
 * prints, per address: address, size, verdict and the rejected opcode, where
 * verdict is "ok", "reject" (always faults) or "guarded" (faults only for
 * some register values, such as a bit index >= 32). */
#include "../../src/target/pi32v2/translate.c"

TCGv_ptr tcg_env;
int sweep_guards;
uint32_t sweep_value;
static int rejected, guarded;
static uint32_t rejected_op;
static uint8_t *image;
static size_t image_size;
static uint32_t image_base;

TCGv_i32 sweep_temp(void) { static char storage; return (TCGv_i32)&storage; }
TCGv_i32 sweep_const(uint32_t value) { sweep_value = value; return sweep_temp(); }
TCGLabel *sweep_label(void) { static char storage; return (TCGLabel *)&storage; }

uint16_t sweep_fetch(vaddr address)
{
    uint64_t offset = address - image_base;
    if (address < image_base || offset + 2 > image_size) {
        return 0xffff;                  /* erased flash beyond the image */
    }
    return image[offset] | image[offset + 1] << 8;
}

void sweep_illegal(uint32_t op)
{
    if (sweep_guards) {
        guarded = 1;
    } else if (!rejected) {
        rejected = 1;
        rejected_op = op;
    }
}

int main(int argc, char **argv)
{
    static Pi32v2CPU cpu;
    char line[64];
    FILE *f;
    if (argc != 3 || !(f = fopen(argv[1], "rb"))) {
        fprintf(stderr, "usage: sweep IMAGE BASE < addresses\n");
        return 2;
    }
    fseek(f, 0, SEEK_END);
    image_size = ftell(f);
    rewind(f);
    image = malloc(image_size);
    if (fread(image, 1, image_size, f) != image_size) { return 2; }
    image_base = strtoul(argv[2], NULL, 0);
    pi32v2_translate_init();
    cpu.stop_pc = UINT32_MAX;           /* no observers, no budget */
    while (fgets(line, sizeof(line), stdin)) {
        uint32_t address = strtoul(line, NULL, 16);
        PiDisasContext d = {0};
        rejected = guarded = 0;
        sweep_guards = 0;
        d.base.pc_first = d.base.pc_next = address;
        d.base.is_jmp = DISAS_NEXT;
        init_disas(&d.base, (CPUState *)&cpu);
        translate_insn(&d.base, (CPUState *)&cpu);
        printf("%08x %u %s %04x\n", address, (unsigned)(d.base.pc_next - address),
               rejected ? "reject" : guarded ? "guarded" : "ok", rejected ? rejected_op : 0);
    }
    return 0;
}
