"""Dump the static + delay import tables of a PE file.

Usage:  python tools/pe_imports.py <path-to-exe-or-dll>

Used while diagnosing the MuMu "no Qt platform plugin could be initialized"
failure: it shows exactly which DLLs qwindows.dll needs at load time.
"""
import struct
import sys


def describe(path):
    data = open(path, "rb").read()
    if data[:2] != b"MZ":
        raise SystemExit("%s: not a DOS/PE image" % path)
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        raise SystemExit("%s: missing PE signature" % path)
    coff = e_lfanew + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    optsz = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    pe32p = magic == 0x20B
    dd = opt + (112 if pe32p else 96)
    imp_rva = struct.unpack_from("<I", data, dd + 8)[0]
    delay_rva = struct.unpack_from("<I", data, dd + 13 * 8)[0]

    secs = []
    so = opt + optsz
    for i in range(nsec):
        b = so + i * 40
        vsz, va, rsz, raw = struct.unpack_from("<IIII", data, b + 8)
        secs.append((va, vsz, raw, rsz))

    def r2o(rva):
        for va, vsz, raw, rsz in secs:
            if va <= rva < va + max(vsz, rsz):
                return raw + (rva - va)
        raise SystemExit("RVA 0x%x is not mapped" % rva)

    def cstr(off):
        end = data.index(b"\0", off)
        return data[off:end].decode("ascii", "replace")

    out = {"machine64": pe32p, "static": [], "delay": []}
    if imp_rva:
        off = r2o(imp_rva)
        while True:
            name_rva = struct.unpack_from("<I", data, off + 12)[0]
            if name_rva == 0:
                break
            out["static"].append(cstr(r2o(name_rva)))
            off += 20
    if delay_rva:
        off = r2o(delay_rva)
        while True:
            if struct.unpack_from("<I", data, off)[0] == 0:
                break
            name_rva = struct.unpack_from("<I", data, off + 4)[0]
            out["delay"].append(cstr(r2o(name_rva)) if name_rva else "?")
            off += 32
    return out


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    info = describe(argv[1])
    print("pe32+ =", info["machine64"])
    print("static imports (%d):" % len(info["static"]))
    for name in info["static"]:
        print("   ", name)
    print("delay imports (%d):" % len(info["delay"]))
    for name in info["delay"]:
        print("   ", name)


if __name__ == "__main__":
    main(sys.argv)
