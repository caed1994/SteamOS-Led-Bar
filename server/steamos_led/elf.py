"""Just enough ELF parsing to list what a shared object exports.

Guessing symbol names is how the Steamworks binding first went wrong: the flat
API's accessors carry a version suffix that depends on the SDK a library was
built from, and Proton's copy has a different set from a current SDK. Reading
the dynamic symbol table turns that guess into a lookup - and makes
`--steam-check` able to say what a library actually offers.

binutils is not installed on a stock SteamOS, so this does not shell out to nm
or objdump.
"""

from __future__ import annotations

import struct

ELFCLASS32, ELFCLASS64 = 1, 2
SHT_DYNSYM = 11


class ElfError(ValueError):
    pass


def _unpack(fmt, data, offset):
    return struct.unpack_from(fmt, data, offset)


def exported_symbols(path):
    """Every name in the dynamic symbol table of an ELF shared object."""
    with open(path, "rb") as handle:
        data = handle.read()

    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise ElfError("%s is not an ELF file" % path)

    elf_class = data[4]
    little = data[5] == 1
    endian = "<" if little else ">"

    if elf_class == ELFCLASS64:
        # e_shoff at 0x28, e_shentsize 0x3a, e_shnum 0x3c
        (section_offset,) = _unpack(endian + "Q", data, 0x28)
        (entry_size,) = _unpack(endian + "H", data, 0x3A)
        (count,) = _unpack(endian + "H", data, 0x3C)
        sym_size, name_fmt, shndx_offset = 24, endian + "I", 6
    elif elf_class == ELFCLASS32:
        (section_offset,) = _unpack(endian + "I", data, 0x20)
        (entry_size,) = _unpack(endian + "H", data, 0x2E)
        (count,) = _unpack(endian + "H", data, 0x30)
        sym_size, name_fmt, shndx_offset = 16, endian + "I", 14
    else:
        raise ElfError("%s has an unknown ELF class %s" % (path, elf_class))

    if not section_offset or not count:
        raise ElfError("%s has no section headers" % path)

    sections = []
    for index in range(count):
        base = section_offset + index * entry_size
        if base + entry_size > len(data):
            raise ElfError("%s has a truncated section table" % path)
        if elf_class == ELFCLASS64:
            sh_type, = _unpack(endian + "I", data, base + 4)
            sh_link, = _unpack(endian + "I", data, base + 40)
            sh_offset, = _unpack(endian + "Q", data, base + 24)
            sh_size, = _unpack(endian + "Q", data, base + 32)
        else:
            sh_type, = _unpack(endian + "I", data, base + 4)
            sh_link, = _unpack(endian + "I", data, base + 24)
            sh_offset, = _unpack(endian + "I", data, base + 16)
            sh_size, = _unpack(endian + "I", data, base + 20)
        sections.append((sh_type, sh_link, sh_offset, sh_size))

    names = set()
    for sh_type, sh_link, sh_offset, sh_size in sections:
        if sh_type != SHT_DYNSYM or sh_link >= len(sections):
            continue
        _, _, str_offset, str_size = sections[sh_link]
        strings = data[str_offset:str_offset + str_size]

        for position in range(sh_offset, sh_offset + sh_size, sym_size):
            if position + sym_size > len(data):
                break
            (name_index,) = _unpack(name_fmt, data, position)
            (shndx,) = _unpack(endian + "H", data, position + shndx_offset)
            if not name_index or shndx == 0:
                continue        # unnamed, or imported rather than exported
            end = strings.find(b"\x00", name_index)
            if end < 0:
                continue
            names.add(strings[name_index:end].decode("ascii", "replace"))
    return names
