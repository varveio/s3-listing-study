"""Render a release's committed figures as deterministic, self-contained SVG.

A chart is an argument, so the selection behind it belongs in a committed file
rather than in the plotting code: `benchmark/publication/charts.yaml` names, per
chart, which rows it draws, by what rule, against which metric, and with what
caption. This module only draws what the spec selects, and writes the exact rows
it drew beside each figure as CSV so a reader can check the picture against the
data without re-running anything.

No JavaScript, no web fonts, no external assets: an SVG committed to a
repository has to render the same in a browser, in a diff and in ten years.

Usage:
    python -m benchmark.public_render --release-dir results/<release>
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import yaml

SPEC_VERSION = 1

PALETTE = ("#2f6f9f", "#b4643c", "#4f8a5b", "#7a5ea8", "#9a8232", "#8a5060")
FAILED_FILL = "#b03a3a"
INK = "#1c1c1c"
MUTED = "#5c5c5c"
GRID = "#d8d8d8"
PAPER = "#ffffff"
FONT = "font-family=\"'DejaVu Sans','Helvetica Neue',Helvetica,Arial,sans-serif\""


class RenderError(RuntimeError):
    """A chart spec cannot be drawn from the rows it selects."""


def value_at(row: Mapping[str, Any], path: str, fixtures: Mapping[str, Any]) -> Any:
    """Read one dotted field, plus the release-level joins a chart may name."""
    if path.startswith("fixture.") and path.split(".", 1)[1] not in (row.get("fixture") or {}):
        fixture = row.get("fixture") or {}
        current: Any = fixtures.get(str(fixture.get("id"))) or {}
        for part in path.split(".")[1:]:
            if not isinstance(current, Mapping):
                return None
            current = current.get(part)
        return current
    current = row
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def matches(row: Mapping[str, Any], where: Mapping[str, Any], fixtures: Mapping[str, Any]) -> bool:
    for path, expected in where.items():
        actual = value_at(row, path, fixtures)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def select(
    chart: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], fixtures: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    """The rows one chart draws, in a stable order, by the spec's own rule."""
    selection = chart.get("select") or {}
    explicit = selection.get("attempt_ids")
    if explicit:
        by_id = {str(row["attempt_id"]): row for row in rows}
        missing = [attempt for attempt in explicit if attempt not in by_id]
        if missing:
            raise RenderError(f"chart {chart['id']}: no such attempt(s): {', '.join(missing)}")
        return [by_id[attempt] for attempt in explicit]
    chosen = [row for row in rows if matches(row, selection.get("where") or {}, fixtures)]
    rule = selection.get("pick", "all")
    if rule == "best-successful-wall-per-tool":
        best: dict[str, Mapping[str, Any]] = {}
        for row in chosen:
            wall = row["outcome"]["wall_seconds"]
            if row["state"]["provider"] != "SUCCEEDED" or not isinstance(wall, (int, float)):
                continue
            key = str(row["tool"]["name"])
            if key not in best or wall < best[key]["outcome"]["wall_seconds"]:
                best[key] = row
        chosen = [best[key] for key in sorted(best)]
    elif rule != "all":
        raise RenderError(f"chart {chart['id']}: unknown selection rule {rule!r}")
    return sorted(chosen, key=lambda row: str(row["attempt_id"]))


def label_for(row: Mapping[str, Any], template: str, fixtures: Mapping[str, Any]) -> str:
    """`{dotted.path}` substitution; a null renders as `-`, never as a blank."""
    out, rest = "", template
    while "{" in rest:
        head, _, rest = rest.partition("{")
        path, _, rest = rest.partition("}")
        value = value_at(row, path, fixtures)
        out += head + ("-" if value is None else str(value))
    return out + rest


def _nice_ceiling(value: float) -> float:
    if value <= 0:
        return 1.0
    step = 10.0 ** (len(str(int(value))) - 1)
    return step * (int(value / step) + 1)


def _text(x: float, y: float, body: str, *, size: float, fill: str, anchor: str = "start") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" {FONT} font-size="{size:g}" fill="{fill}" '
        f'text-anchor="{anchor}">{escape(body)}</text>'
    )


def _frame(width: float, height: float, title: str, caption: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:g} {height:g}" '
        f'width="{width:g}" height="{height:g}" role="img" '
        f'aria-labelledby="chart-title chart-desc">\n'
        f'<title id="chart-title">{escape(title)}</title>\n'
        f'<desc id="chart-desc">{escape(caption)}</desc>\n'
        f'<rect width="{width:g}" height="{height:g}" fill="{PAPER}"/>\n'
        + _text(24, 34, title, size=16, fill=INK)
        + "\n"
        + body
        + "\n"
        + _text(24, height - 16, caption, size=11, fill=MUTED)
        + "\n</svg>\n"
    )


def render_bars(
    chart: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    fixtures: Mapping[str, Any],
) -> str:
    metric = str(chart["metric"])
    values = [value_at(row, metric, fixtures) for row in selected]
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        raise RenderError(f"chart {chart['id']}: no numeric values to draw")
    top = _nice_ceiling(max(numeric))
    left, right, head, foot = 210.0, 60.0, 62.0, 74.0
    band, gap = 34.0, 16.0
    height = head + foot + len(selected) * (band + gap)
    width = 900.0
    plot = width - left - right
    parts = []
    for index in range(5):
        x = left + plot * index / 4
        parts.append(
            f'<line x1="{x:.1f}" y1="{head:.1f}" x2="{x:.1f}" y2="{height - foot:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            _text(
                x, height - foot + 16, f"{top * index / 4:g}", size=10, fill=MUTED, anchor="middle"
            )
        )
    for index, (row, value) in enumerate(zip(selected, values, strict=True)):
        y = head + index * (band + gap)
        failed = not isinstance(value, (int, float))
        length = 0.0 if failed else plot * float(value) / top
        fill = FAILED_FILL if failed else PALETTE[index % len(PALETTE)]
        parts.append(
            f'<rect x="{left:.1f}" y="{y:.1f}" width="{max(length, 2.0):.1f}" '
            f'height="{band:.1f}" fill="{fill}"/>'
        )
        parts.append(
            _text(
                left - 10,
                y + band * 0.62,
                label_for(row, str(chart["series_label"]), fixtures),
                size=12,
                fill=INK,
                anchor="end",
            )
        )
        annotation = (
            label_for(row, str(chart.get("bar_label", "")), fixtures)
            if chart.get("bar_label")
            else ""
        )
        shown = "no value" if failed else f"{float(value):g}"
        parts.append(
            _text(
                left + max(length, 2.0) + 8,
                y + band * 0.62,
                f"{shown}  {annotation}".strip(),
                size=11,
                fill=MUTED,
            )
        )
    parts.append(
        _text(
            left + plot / 2,
            height - foot + 34,
            str(chart["axis_label"]),
            size=11,
            fill=MUTED,
            anchor="middle",
        )
    )
    return _frame(width, height, str(chart["title"]), str(chart["caption"]), "\n".join(parts))


def render_scatter(
    chart: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    fixtures: Mapping[str, Any],
) -> str:
    # `x` may name a list of paths: the first non-null wins. The one use is a
    # fixture count that is staged for some fixtures and only observed for
    # others; the spec's axis label must say so where it uses that.
    x_paths = [str(p) for p in (chart["x"] if isinstance(chart["x"], list) else [chart["x"]])]
    y_path = str(chart["y"])

    def x_of(row: Mapping[str, Any]) -> Any:
        for path in x_paths:
            value = value_at(row, path, fixtures)
            if value is not None:
                return value
        return None

    points = [(row, x_of(row), value_at(row, y_path, fixtures)) for row in selected]
    xs = [float(x) for _, x, _ in points if isinstance(x, (int, float))]
    ys = [float(y) for _, _, y in points if isinstance(y, (int, float))]
    if not xs or not ys:
        raise RenderError(f"chart {chart['id']}: no numeric points to draw")
    x_top, y_top = _nice_ceiling(max(xs)), _nice_ceiling(max(ys))
    left, right, head, foot = 96.0, 220.0, 62.0, 78.0
    width, height = 940.0, 470.0
    plot_w, plot_h = width - left - right, height - head - foot
    parts = [
        f'<rect x="{left:.1f}" y="{head:.1f}" width="{plot_w:.1f}" height="{plot_h:.1f}" '
        f'fill="none" stroke="{GRID}" stroke-width="1"/>'
    ]
    for index in range(5):
        x = left + plot_w * index / 4
        y = head + plot_h * index / 4
        parts.append(
            f'<line x1="{left:.1f}" y1="{y:.1f}" x2="{left + plot_w:.1f}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            _text(
                x,
                height - foot + 18,
                f"{x_top * index / 4:.3g}",
                size=10,
                fill=MUTED,
                anchor="middle",
            )
        )
        parts.append(
            _text(
                left - 8,
                head + plot_h - plot_h * index / 4 + 4,
                f"{y_top * index / 4:.3g}",
                size=10,
                fill=MUTED,
                anchor="end",
            )
        )
    tools = sorted({str(row["tool"]["name"]) for row, _, _ in points})
    colour = {name: PALETTE[index % len(PALETTE)] for index, name in enumerate(tools)}
    for row, x, y in points:
        failed = row["state"]["provider"] != "SUCCEEDED" or not isinstance(y, (int, float))
        if not isinstance(x, (int, float)):
            continue
        px = left + plot_w * float(x) / x_top
        py = head + plot_h - (plot_h * float(y) / y_top if isinstance(y, (int, float)) else 0.0)
        name = str(row["tool"]["name"])
        if failed:
            parts.append(
                f'<path d="M{px - 6:.1f},{py - 6:.1f} l12,12 M{px + 6:.1f},{py - 6:.1f} l-12,12" '
                f'stroke="{FAILED_FILL}" stroke-width="2.4" fill="none"/>'
            )
        else:
            parts.append(
                f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{colour[name]}" '
                f'fill-opacity="0.85"/>'
            )
    legend_x = left + plot_w + 26
    parts.append(_text(legend_x, head + 4, "tool", size=11, fill=INK))
    for index, name in enumerate(tools):
        y = head + 24 + index * 20
        parts.append(
            f'<circle cx="{legend_x + 6:.1f}" cy="{y - 4:.1f}" r="5" fill="{colour[name]}"/>'
        )
        parts.append(_text(legend_x + 20, y, name, size=11, fill=MUTED))
    y = head + 24 + len(tools) * 20
    parts.append(
        f'<path d="M{legend_x:.1f},{y - 9:.1f} l11,11 M{legend_x + 11:.1f},{y - 9:.1f} l-11,11" '
        f'stroke="{FAILED_FILL}" stroke-width="2.4" fill="none"/>'
    )
    parts.append(_text(legend_x + 20, y, "no accepted result", size=11, fill=MUTED))
    parts.append(
        _text(
            left + plot_w / 2,
            height - foot + 40,
            str(chart["x_label"]),
            size=11,
            fill=MUTED,
            anchor="middle",
        )
    )
    parts.append(_text(24, head - 14, str(chart["y_label"]), size=11, fill=MUTED))
    return _frame(width, height, str(chart["title"]), str(chart["caption"]), "\n".join(parts))


def chart_csv(
    chart: Mapping[str, Any], selected: Sequence[Mapping[str, Any]], fixtures: Mapping[str, Any]
) -> str:
    columns = list(chart["csv_columns"])
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in selected:
        writer.writerow(
            [
                "" if value_at(row, column, fixtures) is None else value_at(row, column, fixtures)
                for column in columns
            ]
        )
    return buffer.getvalue()


def load_spec(path: Path) -> list[Mapping[str, Any]]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("spec_version") != SPEC_VERSION:
        raise RenderError(f"chart spec {path} is not a spec_version {SPEC_VERSION} mapping")
    return [dict(chart) for chart in document.get("charts") or []]


def render_charts(
    spec_path: Path,
    *,
    rows: Sequence[Mapping[str, Any]],
    output_dir: Path,
    fixtures: Mapping[str, Any] | None = None,
) -> list[Path]:
    """Draw every chart whose spec entry is `status: rendered`."""
    fixtures = fixtures or {}
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for chart in load_spec(spec_path):
        if chart.get("status") != "rendered":
            continue
        selected = select(chart, rows, fixtures)
        if not selected:
            raise RenderError(f"chart {chart['id']}: selected no rows")
        kind = chart.get("kind")
        if kind == "bars":
            svg = render_bars(chart, selected, fixtures)
        elif kind == "scatter":
            svg = render_scatter(chart, selected, fixtures)
        else:
            raise RenderError(f"chart {chart['id']}: unknown kind {kind!r}")
        svg_path = output_dir / f"{chart['id']}.svg"
        csv_path = output_dir / f"{chart['id']}.csv"
        svg_path.write_text(svg)
        csv_path.write_text(chart_csv(chart, selected, fixtures))
        written.extend((svg_path, csv_path))
    return written


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one release's committed charts.")
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--charts", type=Path, default=Path("benchmark/publication/charts.yaml"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows = [
            json.loads(line)
            for line in (args.release_dir / "attempts.jsonl").read_text().splitlines()
            if line
        ]
        fixtures = json.loads((args.release_dir / "fixtures.json").read_text())
        written = render_charts(
            args.charts, rows=rows, output_dir=args.release_dir / "charts", fixtures=fixtures
        )
    except (OSError, RenderError, ValueError, yaml.YAMLError) as exc:
        print(f"public-render: {exc}", file=sys.stderr)
        return 1
    print(f"public-render: wrote {len(written)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
