"""Plotly Sankey of the application funnel, built from state_events.

The classic job-hunt funnel: every recorded transition becomes a flow. Only
real transitions count — the synthetic NULL→discovered birth events are
skipped so the diagram starts where the pipeline starts.
"""

from __future__ import annotations

from collections import Counter

import plotly.graph_objects as go

from ysearch.tracker import STATES

_NODE_COLORS = {
    "offer": "#2e7d32",
    "interview": "#558b2f",
    "rejected": "#c62828",
    "ghosted": "#9e9e9e",
    "withdrawn": "#9e9e9e",
}
_DEFAULT_COLOR = "#1565c0"


def build_flows(pairs: list[tuple[str | None, str]]) -> Counter:
    """Count (from_state, to_state) transitions, skipping birth events."""
    return Counter((f, t) for f, t in pairs if f)


def figure(flows: Counter) -> go.Figure | None:
    """Sankey figure, or None when there's nothing to draw yet."""
    if not flows:
        return None
    present = {s for pair in flows for s in pair}
    nodes = [s for s in STATES if s in present]
    idx = {s: i for i, s in enumerate(nodes)}
    fig = go.Figure(
        go.Sankey(
            node=dict(
                label=nodes,
                color=[_NODE_COLORS.get(s, _DEFAULT_COLOR) for s in nodes],
                pad=24,
                thickness=16,
            ),
            link=dict(
                source=[idx[f] for f, _ in flows],
                target=[idx[t] for _, t in flows],
                value=[count for count in flows.values()],
            ),
        )
    )
    fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=420)
    return fig
