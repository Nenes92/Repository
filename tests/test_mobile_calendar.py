import pytest
from mobile_shift_calendar import validate_day_save


def valid():
    return dict(date='2026-10-20',holiday=True,overtime=30,leave=45,at_start=True,request_id='request-1')


def test_save_validation_preserves_day_values():
    payload=valid()
    assert validate_day_save(payload, {'2026-10-20'}) == payload


@pytest.mark.parametrize('field,value', [
    ('date',[]),('date','2026-11-20'),('date','<script>'),('holiday','true'),('at_start',1),
    ('overtime',15),('overtime',150),('overtime',True),('leave',14),('leave',255),
    ('leave',45.0),('request_id',''),('request_id',None),
])
def test_invalid_browser_payload_cannot_be_saved(field,value):
    payload=valid();payload[field]=value
    with pytest.raises(ValueError):
        validate_day_save(payload, {'2026-10-20'})


def test_asset_updates_replace_cached_browser_component_but_data_updates_do_not(monkeypatch):
    import mobile_shift_calendar as calendar
    mounts = []
    definitions = []
    def declare(name, **assets):
        definitions.append(name)
        def render(**kwargs):
            mounts.append(kwargs)
            return None
        return render
    monkeypatch.setattr(calendar, 'component', declare)
    calendar.render_mobile_shift_calendar([], 'october')
    calendar.render_mobile_shift_calendar([None], 'october')
    assert definitions[0] == definitions[1]
    assert mounts[0]['key'] == mounts[1]['key']
    monkeypatch.setattr(calendar, 'CSS', calendar.CSS + '\n/* updated badges */')
    calendar.render_mobile_shift_calendar([], 'october')
    assert definitions[2] != definitions[0]
    assert mounts[2]['key'] != mounts[0]['key']
