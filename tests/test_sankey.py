from ysearch import sankey


def test_build_flows_skips_birth_events_and_counts():
    pairs = [
        (None, "discovered"),  # birth — skipped
        ("discovered", "shortlisted"),
        ("discovered", "shortlisted"),
        ("shortlisted", "applied"),
    ]
    flows = sankey.build_flows(pairs)
    assert flows[("discovered", "shortlisted")] == 2
    assert flows[("shortlisted", "applied")] == 1
    assert (None, "discovered") not in flows


def test_figure_none_when_empty():
    assert sankey.figure(sankey.build_flows([(None, "discovered")])) is None
    assert sankey.figure(sankey.build_flows([])) is None


def test_figure_nodes_and_links():
    flows = sankey.build_flows(
        [("discovered", "shortlisted"), ("shortlisted", "applied"), ("applied", "rejected")]
    )
    fig = sankey.figure(flows)
    trace = fig.data[0]
    assert list(trace.node.label) == ["discovered", "shortlisted", "applied", "rejected"]
    assert len(trace.link.source) == 3
    assert sum(trace.link.value) == 3
