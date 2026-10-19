"""A small HID report descriptor parser.

Enough of HID 1.11 short/long items to answer the questions the profiles care
about: is the descriptor well formed, and how many bytes does each declared
report carry? A host refuses (or silently drops) a device whose report
descriptor it cannot parse, and a report whose declared length does not match
what we send is read wrong, so both are worth checking without hardware.
"""
from dataclasses import dataclass, field

INPUT, OUTPUT, FEATURE, COLLECTION, END_COLLECTION = 0x08, 0x09, 0x0B, 0x0A, 0x0C
PUSH, POP = 0x0A, 0x0B  # global item tags; the prefix bytes are 0xA4 and 0xB4
LONG_ITEM = 0xFE
MAIN, GLOBAL = 0, 1
APPLICATION = 0x01  # collection *type* (0 = Physical, 1 = Application, 2 = Logical)

TYPE_NAME = {INPUT: "input", OUTPUT: "output", FEATURE: "feature"}
# Global item tags; only Report Size/Count/ID change how many bits a report takes.
REPORT_SIZE, REPORT_COUNT, REPORT_ID = 0x07, 0x09, 0x08


class Malformed(ValueError):
    """The descriptor is not something a HID parser would accept."""


@dataclass
class Report:
    kind: str  # "input", "output" or "feature"
    report_id: int  # 0 = the descriptor declares no report IDs
    bits: int = 0

    @property
    def size(self) -> int:
        """Bytes the host reads for this report, after the report ID."""
        return (self.bits + 7) // 8

    @property
    def padded(self) -> bool:
        """True when the fields do not fill whole bytes and the host has to pad."""
        return self.bits % 8 != 0


@dataclass
class Descriptor:
    reports: list[Report] = field(default_factory=list)
    applications: int = 0  # top level Application collections (0xA1 0x10)

    def find(self, kind: str, report_id: int) -> Report | None:
        return next((r for r in self.reports if r.kind == kind and r.report_id == report_id), None)

    def size(self, kind: str, report_id: int) -> int | None:
        """Bytes the host reads for a report, or None if it is not declared."""
        report = self.find(kind, report_id)
        return report.size if report else None

    @property
    def ids(self) -> set[int]:
        return {r.report_id for r in self.reports}


def _number(item: bytes) -> int:
    """An item's data as a little-endian number. Sizes and counts are unsigned."""
    return int.from_bytes(item, "little")


def parse(desc: bytes) -> Descriptor:
    """Walk a report descriptor. Raises Malformed on anything a HID parser would reject."""
    result = Descriptor()
    depth = 0
    pos = 0
    report_size = report_count = 0
    report_id = 0
    globals_stack: list[tuple[int, int]] = []

    while pos < len(desc):
        head = desc[pos]
        pos += 1
        if head == LONG_ITEM:  # 0xFE: data length byte, then that many bytes
            if pos >= len(desc):
                raise Malformed("truncated long item")
            pos += 2 + desc[pos]
            if pos > len(desc):
                raise Malformed("truncated long item data")
            continue
        # prefix byte: bTag in the high nibble, bType in the middle two bits, bSize in the
        # low two bits (3 means 4 bytes).
        tag, kind, size = head >> 4, (head >> 2) & 0x03, head & 0x03
        if size == 3:
            size = 4
        if pos + size > len(desc):
            raise Malformed(f"truncated item at offset {pos - 1}")
        item = desc[pos:pos + size]
        pos += size

        if kind == MAIN:
            if tag in TYPE_NAME:
                if not report_size or not report_count:
                    raise Malformed(f"{TYPE_NAME[tag]} item before Report Size/Count")
                bits = report_size * report_count
                current = result.find(TYPE_NAME[tag], report_id)
                if current:
                    current.bits += bits
                else:
                    result.reports.append(Report(TYPE_NAME[tag], report_id, bits))
            elif tag == COLLECTION:
                if depth == 0:
                    if item and item[0] != APPLICATION:
                        raise Malformed(f"top level collection 0x{item[0]:02x} is not an Application")
                    result.applications += 1
                depth += 1
            elif tag == END_COLLECTION:
                depth -= 1
                if depth < 0:
                    raise Malformed("End Collection without a matching Collection")
            elif tag in (PUSH, POP):
                raise Malformed("Push/Pop are global items, not main items")
        elif kind == GLOBAL:
            if tag == REPORT_SIZE:
                report_size = _number(item)
            elif tag == REPORT_COUNT:
                report_count = _number(item)
            elif tag == REPORT_ID:
                value = _number(item)
                if not 1 <= value <= 0xFF:
                    raise Malformed(f"bad Report ID {value}")
                report_id = value
            elif tag == PUSH:
                globals_stack.append((report_size, report_count))
            elif tag == POP:
                if not globals_stack:
                    raise Malformed("Global Pop without a Push")
                report_size, report_count = globals_stack.pop()
        # Local items only qualify the data items that follow, so they change nothing here.

    if depth:
        raise Malformed(f"{depth} collection(s) left open")
    if globals_stack:
        raise Malformed("global items pushed and never popped")
    return result


def unpack(desc: bytes) -> str:
    """The parsed descriptor, one report per line. For eyeballing a new profile."""
    parsed = parse(desc)
    lines = [f"application collections: {parsed.applications}"]
    for report in parsed.reports:
        lines.append(f"{report.kind:>7} report 0x{report.report_id:02x}: {report.size} bytes"
                     f"{' (padded)' if report.padded else ''}")
    return "\n".join(lines)