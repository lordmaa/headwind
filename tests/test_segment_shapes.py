"""Segment shapes: validation of stored/friend-supplied data, and recovery of a missing shape from a real ride."""
import json
import math

from services.segments import _haversine, _slice_between, clean_polyline, valid_coord


def test_valid_coord_rejects_junk():
    assert valid_coord(54.9, -1.6) == [54.9, -1.6]
    for bad in ((None, 1), ('abc', 1), (91, 0), (0, 181), (float('nan'), 0), ("1);alert(1);//", 1)):
        assert valid_coord(*bad) is None


def test_clean_polyline_rejects_malicious_and_malformed():
    assert clean_polyline('[[54.9,-1.6],[54.91,-1.61]]') == [[54.9, -1.6], [54.91, -1.61]]
    assert clean_polyline('[[54.9,-1.6],[54.91,-1.61]];alert(1)//') is None      # not JSON at all
    assert clean_polyline([[54.9, -1.6], ['</script><script>alert(1)</script>', 0]]) is None
    assert clean_polyline([[54.9, -1.6], [200, 0]]) is None                         # impossible latitude
    assert clean_polyline([[54.9, -1.6]]) is None                                   # too short to be a route
    assert clean_polyline({'a': 1}) is None and clean_polyline(None) is None and clean_polyline('') is None


def test_clean_polyline_downsamples_and_keeps_ends():
    pts = [[54 + i * 1e-5, -1 - i * 1e-5] for i in range(5000)]
    out = clean_polyline(pts, max_points=500)
    assert len(out) <= 502 and out[0] == [54.0, -1.0] and out[-1] == [round(pts[-1][0], 6), round(pts[-1][1], 6)]


def _ride(n=400):
    """A curved climb ~3 km long, as a streams JSON."""
    ll = [[54.9 + 0.0001 * i, -1.6 + 0.00005 * math.sin(i / 25)] for i in range(n)]
    return ll, json.dumps({'latlng': {'data': ll}})


def _seg(ll, a, b, dist=None):
    length = sum(_haversine(*ll[i], *ll[i + 1]) for i in range(a, b))
    return {'startLat': ll[a][0], 'startLng': ll[a][1], 'endLat': ll[b][0], 'endLng': ll[b][1],
            'distanceM': length if dist is None else dist}


def test_recovers_shape_between_start_and_finish():
    ll, streams = _ride()
    shape = _slice_between(streams, _seg(ll, 100, 300))
    assert shape and len(shape) > 50
    assert _haversine(*shape[0], *ll[100]) < 5 and _haversine(*shape[-1], *ll[300]) < 5


def test_refuses_when_ride_does_not_follow_segment():
    ll, streams = _ride()
    far = {'startLat': 40.0, 'startLng': -3.0, 'endLat': 40.01, 'endLng': -3.0, 'distanceM': 1000}
    assert _slice_between(streams, far) is None                                    # nowhere near
    assert _slice_between(streams, _seg(ll, 100, 300, dist=900)) is None           # path length disagrees with segment
    reversed_seg = _seg(ll, 300, 100)
    assert _slice_between(streams, reversed_seg) is None                           # wrong direction (finish before start)
