"""Execute chooser wrappers; caller geometry and view state must not change."""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))
from world_map_follow_modal import build_modal, TEXT_OFFSET, RECT_OFFSET

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import (
        UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_ESI,
        UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_EIP,
    )
except ImportError:
    Uc = None


@unittest.skipIf(Uc is None, "Install unicorn to execute native camera wrappers")
class CameraModalExecutionTests(unittest.TestCase):
    def run_wrapper(self, kind, rx, ry):
        base, sp, stop, view = 0x700000, 0x10018000, 0x1001F000, 0x701000
        state = {"rx": 0x70F000, "ry": 0x70F004}
        chunks, _hooks = build_modal(base, state)
        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(0x400000, 0x300000)
        uc.mem_map(base, 0x10000)
        uc.mem_map(0x10000000, 0x20000)
        for offset, code in chunks.items():
            uc.mem_write(base + offset, code)

        def put(addr, *values):
            uc.mem_write(addr, struct.pack("<" + "i" * len(values), *values))

        def get(addr, count=1):
            values = struct.unpack("<" + "i" * count, uc.mem_read(addr, count * 4))
            return values[0] if count == 1 else values

        put(state["rx"], rx, ry)
        put(view + 0x54, 9, 32)
        rect_pointer, text_pointer = 0x701800, 0x701900
        rectangle = (0, 16, 15, 31)
        put(rect_pointer, *rectangle)
        uc.mem_write(text_pointer, b"1\0")
        args = (2, 3, text_pointer) if kind == "text" else (rect_pointer, 0)
        put(sp, stop, *args)
        uc.reg_write(UC_X86_REG_ESP, sp)
        uc.reg_write(UC_X86_REG_ECX, view)
        preserved = {UC_X86_REG_EBX: 0x11112222, UC_X86_REG_ESI: 0x33334444,
                     UC_X86_REG_EDI: 0x55556666, UC_X86_REG_EBP: 0x12345678}
        for reg, value in preserved.items():
            uc.reg_write(reg, value)
        calls = []

        def hook(machine, address, size, context):
            if address not in (0x426860, 0x4B5C18):
                return
            esp = machine.reg_read(UC_X86_REG_ESP)
            if address == 0x426860:
                self.assertEqual(machine.reg_read(UC_X86_REG_ECX), view)
                self.assertEqual(get(view + 0x54, 2), (9 - rx, 32 - ry))
                self.assertEqual(get(esp + 4, 3), args)
                cleanup = 12
            else:
                self.assertEqual(get(get(esp + 4), 4),
                                 (rectangle[0] - rx, rectangle[1] - ry,
                                  rectangle[2] - rx, rectangle[3] - ry))
                self.assertEqual(get(esp + 8), 0)
                cleanup = 8
            calls.append(address)
            machine.reg_write(UC_X86_REG_EAX, 77)
            machine.reg_write(UC_X86_REG_EIP, get(esp))
            machine.reg_write(UC_X86_REG_ESP, esp + 4 + cleanup)

        uc.hook_add(UC_HOOK_CODE, hook)
        uc.emu_start(base + (TEXT_OFFSET if kind == "text" else RECT_OFFSET), stop, count=10000)
        self.assertEqual(uc.reg_read(UC_X86_REG_EIP), stop)
        self.assertEqual(uc.reg_read(UC_X86_REG_ESP), sp + 4 + len(args) * 4)
        self.assertEqual(uc.reg_read(UC_X86_REG_EAX), 77)
        self.assertEqual(len(calls), 1)
        self.assertEqual(get(view + 0x54, 2), (9, 32))
        self.assertEqual(get(rect_pointer, 4), rectangle)
        for reg, value in preserved.items():
            self.assertEqual(uc.reg_read(reg), value)

    def test_number_labels_shift_once_and_restore_view(self):
        for rx, ry in ((0, 0), (1, 15), (15, 1), (15, 15)):
            with self.subTest(residual=(rx, ry)):
                self.run_wrapper("text", rx, ry)

    def test_xor_rectangles_shift_a_copy_for_draw_and_erase(self):
        for rx, ry in ((0, 0), (1, 15), (15, 1), (15, 15)):
            with self.subTest(residual=(rx, ry)):
                self.run_wrapper("rect", rx, ry)


if __name__ == "__main__":
    unittest.main()
